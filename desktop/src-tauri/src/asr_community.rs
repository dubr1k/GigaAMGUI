//! Offline Community-1 inference on the shared canonical 16 kHz timeline.
//! The model bundle is provisioned separately by the application or supplied
//! locally; this module only loads and verifies it.
//!
//! Segmentation aggregation and reconstruction are adapted from pyannote.audio
//! 4.0.7 `core/inference.py`, `pipelines/speaker_diarization.py` and
//! `pipelines/utils/diarization.py`, distributed under the MIT License.
//!
//! Copyright (c) 2020-2025 CNRS
//! Copyright (c) 2025- pyannoteAI
//!
//! Permission is hereby granted, free of charge, to any person obtaining a copy
//! of this software and associated documentation files (the "Software"), to deal
//! in the Software without restriction, including without limitation the rights
//! to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
//! copies of the Software, and to permit persons to whom the Software is
//! furnished to do so, subject to the following conditions:
//!
//! The above copyright notice and this permission notice shall be included in all
//! copies or substantial portions of the Software.
//!
//! THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
//! IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
//! FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
//! AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
//! LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
//! OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
//! SOFTWARE.

#[path = "asr_community_vbx.rs"]
mod vbx;

use std::collections::HashMap;
use std::fs::File;
use std::io::{BufReader, Read};
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};

use polyvoice::onnx::{ExecutionProvider, InferenceRuntime, InferenceTensor, RuntimeSession};
use serde::Deserialize;
use sha2::{Digest, Sha256};

use super::audio;
use super::embedder::WespeakerFrontend;
use super::pipeline::{Region, RegionKind};

const RATE: usize = 16_000;
const WINDOW: usize = 10 * RATE;
const HOP: usize = RATE;
const FRAMES: usize = 589;
const FBANK_FRAMES: usize = 998;
const FBANK_BINS: usize = 80;
const SEGMENTATION: &str = "tract/segmentation.onnx";
const EMBEDDING: &str = "tract/embedding.onnx";
const PLDA: &str = "plda.json";

// Hugging Face community-1 snapshot 3533c8cf8e369892e6b79ff1bf80f7b0286a54ee.
const SOURCE_HASHES: [(&str, &str); 4] = [
    ("segmentation/pytorch_model.bin", "7ad24338d844fb95985486eb1a464e32d229f6d7a03c9abe60f978bacf3f816e"),
    ("embedding/pytorch_model.bin", "6f10ff60898a1d185fa22e1d11e0bfa8a92efec811f11bca48cb8cafebefd929"),
    ("plda/plda.npz", "9b77bcd840692710dd3496f62ecfeed8d8e5f002fd991b785079b244eab7d255"),
    ("plda/xvec_transform.npz", "325f1ce8e48f7e55e9c8aa47e05d2766b7c48c4b25b8de8dd751e7a4cc5fbe8f"),
];

#[derive(Deserialize)]
struct Manifest {
    source_sha256: HashMap<String, String>,
    runtime_sha256: HashMap<String, String>,
    segmentation: SegmentationInfo,
    embedding: EmbeddingInfo,
}

#[derive(Deserialize)]
struct SegmentationInfo {
    receptive_field_start: f64,
    receptive_field_step: f64,
    receptive_field_duration: f64,
}

#[derive(Deserialize)]
struct EmbeddingInfo {
    min_num_samples: usize,
    fbank_hyperparameters: FbankInfo,
}

#[derive(Deserialize)]
struct FbankInfo {
    sample_rate: usize,
    num_channels: usize,
    num_mel_bins: usize,
    frame_length: f64,
    frame_shift: f64,
    round_to_power_of_two: bool,
    snip_edges: bool,
    dither: f64,
    window_type: String,
    use_energy: bool,
    fbank_centering_span: Option<f64>,
}

pub(super) fn require_bundle(directory: &Path) -> Result<(), String> {
    for name in ["export.json", PLDA, SEGMENTATION, EMBEDDING] {
        let path = directory.join(name);
        let metadata = path.metadata().map_err(|error|
            format!("Не найден подготовленный файл Community-1 {}: {error}", path.display()))?;
        if !metadata.is_file() || metadata.len() == 0 {
            return Err(format!("Некорректный файл Community-1: {}", path.display()));
        }
    }
    Ok(())
}

fn check_cancelled(flag: &AtomicBool) -> Result<(), String> {
    if flag.load(Ordering::Acquire) { Err("Диаризация отменена".into()) } else { Ok(()) }
}

fn verify_file(path: &Path, expected: &str, flag: &AtomicBool) -> Result<(), String> {
    if expected.len() != 64 || !expected.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)) {
        return Err("Некорректная контрольная сумма Community-1".into());
    }
    let mut file = File::open(path).map_err(|e| format!("Не удалось открыть {}: {e}", path.display()))?;
    let mut hash = Sha256::new();
    let mut buffer = [0u8; 64 * 1024];
    loop {
        check_cancelled(flag)?;
        let n = file.read(&mut buffer).map_err(|e| format!("Не удалось прочитать модель: {e}"))?;
        if n == 0 { break; }
        hash.update(&buffer[..n]);
    }
    let actual = hash.finalize();
    for (index, pair) in expected.as_bytes().chunks_exact(2).enumerate() {
        let nibble = |byte: u8| if byte <= b'9' { byte - b'0' } else { byte - b'a' + 10 };
        if actual[index] != ((nibble(pair[0]) << 4) | nibble(pair[1])) {
            return Err(format!("Контрольная сумма {} не совпадает с манифестом", path.display()));
        }
    }
    Ok(())
}

impl Manifest {
    fn load(directory: &Path, flag: &AtomicBool) -> Result<Self, String> {
        let path = directory.join("export.json");
        let file = File::open(&path).map_err(|e| format!(
            "Не найден экспорт Community-1 в {}: {e}", directory.display()))?;
        let manifest: Self = serde_json::from_reader(BufReader::new(file))
            .map_err(|e| format!("Некорректный манифест Community-1: {e}"))?;
        for (path, expected) in SOURCE_HASHES {
            if manifest.source_sha256.get(path).map(String::as_str) != Some(expected) {
                return Err(format!("Экспорт Community-1 не соответствует закреплённому исходнику {path}"));
            }
        }
        let seg = &manifest.segmentation;
        let emb = &manifest.embedding;
        let f = &emb.fbank_hyperparameters;
        if seg.receptive_field_start != 0.0
            || seg.receptive_field_step != 0.016875
            || seg.receptive_field_duration != 0.0619375
            || emb.min_num_samples != 400
            || f.sample_rate != RATE || f.num_channels != 1 || f.num_mel_bins != FBANK_BINS
            || f.frame_length != 25.0 || f.frame_shift != 10.0 || !f.round_to_power_of_two
            || !f.snip_edges || f.dither != 0.0 || f.window_type != "hamming"
            || f.use_energy || f.fbank_centering_span.is_some()
        {
            return Err("Неподдерживаемые параметры модели Community-1".into());
        }
        for name in [SEGMENTATION, EMBEDDING, PLDA] {
            let hash = manifest.runtime_sha256.get(name)
                .ok_or_else(|| format!("В манифесте отсутствует контрольная сумма {name}"))?;
            verify_file(&directory.join(name), hash, flag)?;
        }
        Ok(manifest)
    }
}

fn decode_powerset(scores: &[f32]) -> Result<(Vec<u8>, [usize; 3], [usize; 3]), String> {
    if scores.len() != FRAMES * 7 || scores.iter().any(|x| !x.is_finite()) {
        return Err("Неверный результат сегментации Community-1".into());
    }
    let mut frames = Vec::with_capacity(FRAMES);
    let mut active = [0usize; 3];
    let mut clean = [0usize; 3];
    for row in scores.chunks_exact(7) {
        let mut index = 0;
        for i in 1..7 { if row[i] > row[index] { index = i; } }
        let bits = [0u8, 1, 2, 4, 3, 5, 6][index];
        frames.push(bits);
        for s in 0..3 {
            if bits & (1 << s) != 0 {
                active[s] += 1;
                if bits.count_ones() == 1 { clean[s] += 1; }
            }
        }
    }
    Ok((frames, active, clean))
}

struct Chunk {
    // Each value is the three-speaker bitset from the seven-class powerset.
    frames: Vec<u8>,
    embeddings: vbx::Chunk,
}

pub(super) struct Pipeline {
    segmentation: RuntimeSession,
    embedding: RuntimeSession,
    frontend: WespeakerFrontend,
    plda: vbx::Plda,
    frame_step: f64,
    frame_duration: f64,
    clean_min_frames: usize,
}

impl Pipeline {
    pub(super) fn load(directory: &Path, flag: &AtomicBool) -> Result<Self, String> {
        let manifest = Manifest::load(directory, flag)?;
        let plda = vbx::Plda::load(&directory.join(PLDA))?;
        check_cancelled(flag)?;
        let segmentation = RuntimeSession::from_path(&directory.join(SEGMENTATION), ExecutionProvider::Cpu, None)
            .map_err(|e| format!("Не удалось загрузить сегментацию Community-1: {e}"))?;
        check_cancelled(flag)?;
        let embedding = RuntimeSession::from_path(&directory.join(EMBEDDING), ExecutionProvider::Cpu, None)
            .map_err(|e| format!("Не удалось загрузить эмбеддинг Community-1: {e}"))?;
        if segmentation.input_names().len() != 1 || embedding.input_names().len() != 2 {
            return Err("Неверные входы графов Community-1".into());
        }
        Ok(Self {
            segmentation,
            embedding,
            frontend: WespeakerFrontend::new(),
            plda,
            frame_step: manifest.segmentation.receptive_field_step,
            frame_duration: manifest.segmentation.receptive_field_duration,
            clean_min_frames: (FRAMES * manifest.embedding.min_num_samples).div_ceil(WINDOW),
        })
    }

    fn infer(&mut self, samples: &[f32], flag: &AtomicBool) -> Result<Chunk, String> {
        check_cancelled(flag)?;
        if samples.len() != WINDOW || samples.iter().any(|x| !x.is_finite()) {
            return Err("Некорректное аудио в окне Community-1".into());
        }
        let input = InferenceTensor::f32(vec![1, 1, WINDOW], samples.to_vec());
        let output = self.segmentation.run_ordered(&[&input])
            .map_err(|e| format!("Ошибка сегментации Community-1: {e}"))?
            .into_iter().next().ok_or("Сегментация Community-1 не вернула результат")?;
        let scores = output.into_f32().map_err(|e| format!("Неверный тип сегментации: {e}"))?;
        let (frames, active, clean) = decode_powerset(&scores)?;
        let mut embeddings: [Option<Vec<f32>>; 3] = std::array::from_fn(|_| None);
        if active.iter().any(|&count| count > 0) {
            let (n, features) = self.frontend.extract_community(samples)
                .map_err(|e| format!("Ошибка входных признаков Community-1: {e}"))?;
            if n != FBANK_FRAMES { return Err("Неверное число входных кадров Community-1".into()); }
            let fbank = InferenceTensor::f32(vec![1, FBANK_FRAMES, FBANK_BINS], features);
            for s in 0..3 {
                if active[s] == 0 { continue; }
                check_cancelled(flag)?;
                let exclude_overlap = clean[s] > self.clean_min_frames;
                let mask: Vec<f32> = frames.iter().map(|bits| {
                    let is_active = bits & (1 << s) != 0;
                    if is_active && (!exclude_overlap || bits.count_ones() == 1) { 1.0 } else { 0.0 }
                }).collect();
                let input = InferenceTensor::f32(vec![1, FRAMES], mask);
                let output = self.embedding.run_ordered(&[&fbank, &input])
                    .map_err(|e| format!("Ошибка эмбеддинга Community-1: {e}"))?
                    .into_iter().next().ok_or("Модель говорящего не вернула результат")?;
                let vector = output.into_f32().map_err(|e| format!("Неверный тип эмбеддинга: {e}"))?;
                if vector.len() != 256 { return Err("Неверная размерность эмбеддинга Community-1".into()); }
                if vector.iter().all(|value| value.is_finite()) { embeddings[s] = Some(vector); }
            }
        }
        Ok(Chunk { frames, embeddings: vbx::Chunk { embeddings, active, clean } })
    }

    /// Regions are provisional until the complete pass succeeds.
    pub(super) fn diarize<F, P>(&mut self, path: &Path, samples: u64, flag: &AtomicBool,
        mut progress: P, mut emit: F) -> Result<(), String>
    where
        F: FnMut(Region) -> Result<(), String>,
        P: FnMut(f64),
    {
        if samples == 0 { return Err("Пустое аудио Community-1".into()); }
        let mut buffer = Vec::with_capacity(WINDOW + HOP);
        let mut chunks = Vec::new();
        let mut received = 0u64;
        audio::for_each_window(path, HOP, flag, |start, data| {
            check_cancelled(flag)?;
            if start != received { return Err("Непоследовательная шкала аудио Community-1".into()); }
            received = received.checked_add(data.len() as u64).ok_or("Слишком длинное аудио")?;
            buffer.extend_from_slice(data);
            if buffer.len() >= WINDOW {
                chunks.push(self.infer(&buffer[..WINDOW], flag)?);
                buffer.copy_within(HOP.., 0);
                buffer.truncate(buffer.len() - HOP);
            }
            progress((received as f64 / samples as f64).min(1.0));
            Ok(())
        }).map_err(|e| format!("Ошибка чтения аудио Community-1: {e}"))?;
        if received != samples { return Err("Неполное временное аудио Community-1".into()); }
        if received < WINDOW as u64 || (received - WINDOW as u64) % HOP as u64 != 0 {
            buffer.resize(WINDOW, 0.0);
            chunks.push(self.infer(&buffer, flag)?);
        }
        if chunks.is_empty() { return Err("Нет окон Community-1".into()); }
        let clustering: Vec<_> = chunks.iter_mut().map(|c| std::mem::replace(&mut c.embeddings,
            vbx::Chunk { embeddings: std::array::from_fn(|_| None), active: [0; 3], clean: [0; 3] })).collect();
        let labels = vbx::cluster(&self.plda, &clustering, FRAMES, flag)?;
        drop(clustering);
        self.reconstruct(&chunks, &labels, samples, flag, &mut emit)
    }

    fn reconstruct<F>(&self, chunks: &[Chunk], labels: &[[Option<usize>; 3]], samples: u64,
        flag: &AtomicBool, emit: &mut F) -> Result<(), String>
    where F: FnMut(Region) -> Result<(), String>
    {
        let speakers = labels.iter().flat_map(|row| row.iter().flatten()).copied().max().map_or(0, |x| x + 1);
        if speakers == 0 { return Ok(()); }
        let starts: Vec<usize> = (0..chunks.len()).map(|i|
            ((i as f64) / self.frame_step).round_ties_even() as usize).collect();
        let last_time = (chunks.len() - 1) as f64 + WINDOW as f64 / RATE as f64;
        let frame_total = (last_time / self.frame_step).round_ties_even() as usize + 1;
        let mut activations = vec![0u32; speakers];
        let mut first = 0usize;
        let mut end = 0usize;
        let mut previous = None;
        let mut region_start = 0u64;
        let mut final_time = 0u64;
        for t in 0..frame_total {
            if t % 1024 == 0 { check_cancelled(flag)?; }
            while first < chunks.len() && starts[first].saturating_add(FRAMES) <= t { first += 1; }
            while end < chunks.len() && starts[end] <= t { end += 1; }
            activations.fill(0);
            let mut count_sum = 0u32;
            let mut contributing = 0u32;
            for i in first..end {
                let frame = t - starts[i];
                if frame >= FRAMES { continue; }
                let bits = chunks[i].frames[frame];
                count_sum += bits.count_ones();
                contributing += 1;
                for s in 0..3 {
                    if bits & (1 << s) == 0 { continue; }
                    if let Some(k) = labels[i][s] {
                        // Multiple local speakers can share a cluster when the
                        // training set has fewer than two embeddings: upstream
                        // reconstructs that cluster using max, not sum.
                        if (0..s).any(|other| bits & (1 << other) != 0 && labels[i][other] == Some(k)) {
                            continue;
                        }
                        activations[k] += 1;
                    }
                }
            }
            let count = if contributing == 0 { 0 } else {
                (count_sum as f64 / contributing as f64).round_ties_even() as usize
            };
            let status = match count {
                0 => None,
                1 => {
                    let mut best = 0;
                    for k in 1..speakers { if activations[k] > activations[best] { best = k; } }
                    Some(RegionKind::Speaker(u32::try_from(best).map_err(|_| "Слишком много говорящих")?))
                }
                _ => Some(RegionKind::Overlap),
            };
            // Inference.aggregate resets the receptive-field window's start to
            // zero; Binarize places transitions at the frame centre.
            let time = ((0.5 * self.frame_duration + t as f64 * self.frame_step) * RATE as f64)
                .round_ties_even().max(0.0) as u64;
            let time = time.min(samples);
            if status != previous {
                if let Some(kind) = previous {
                    if time > region_start { emit(Region { start_sample: region_start, end_sample: time, kind })?; }
                }
                previous = status;
                region_start = time;
            }
            final_time = time;
            if time == samples { break; }
        }
        if let Some(kind) = previous {
            if final_time > region_start { emit(Region { start_sample: region_start, end_sample: final_time, kind })?; }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn powerset_preserves_solo_and_overlapping_speakers() {
        let mut scores = vec![0.0; FRAMES * 7];
        for class in 1..7 { scores[class * 7 + class] = 1.0; }
        let (frames, active, clean) = decode_powerset(&scores).unwrap();
        assert_eq!(&frames[..7], &[0, 1, 2, 4, 3, 5, 6]);
        assert_eq!(active, [3, 3, 3]);
        assert_eq!(clean, [1, 1, 1]);
        scores[0] = f32::NAN;
        assert!(decode_powerset(&scores).is_err());
    }

    #[test]
    fn missing_local_bundle_fails_before_recognition() {
        let directory = tempfile::tempdir().unwrap();
        let error = require_bundle(directory.path()).unwrap_err();
        assert!(error.contains("export.json"));
    }

    #[test]
    fn runtime_artifact_hash_mismatch_fails_closed() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("artifact");
        std::fs::write(&path, b"abc").unwrap();
        let flag = AtomicBool::new(false);
        let hash = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";
        assert!(verify_file(&path, hash, &flag).is_ok());
        std::fs::write(&path, b"changed").unwrap();
        assert!(verify_file(&path, hash, &flag).is_err());
    }

    /// Run explicitly with an authorized exported bundle and a small voiced
    /// 16-kHz mono WAVE; emitted sample intervals can be compared to pyannote.
    #[test]
    #[ignore = "requires an authorized Community-1 bundle and local audio"]
    fn official_bundle_smoke() {
        let bundle = std::env::var_os("GIGAAM_COMMUNITY_BUNDLE").expect("GIGAAM_COMMUNITY_BUNDLE");
        let wav = std::env::var_os("GIGAAM_COMMUNITY_WAV").expect("GIGAAM_COMMUNITY_WAV");
        let flag = AtomicBool::new(false);
        let mut samples = 0u64;
        audio::for_each_window(Path::new(&wav), HOP, &flag, |start, data| {
            assert_eq!(start, samples);
            samples += data.len() as u64;
            Ok(())
        }).unwrap();
        let mut pipeline = Pipeline::load(Path::new(&bundle), &flag).unwrap();
        let mut regions = Vec::new();
        let mut progress = 0.0f64;
        pipeline.diarize(Path::new(&wav), samples, &flag,
            |fraction| progress = fraction,
            |region| { regions.push(region); Ok(()) }).unwrap();
        assert!(progress > 0.0);
        assert!(!regions.is_empty(), "fixture should contain speech");
        for region in regions {
            println!("COMMUNITY_REGION {} {} {:?}", region.start_sample, region.end_sample, region.kind);
        }
    }
}
