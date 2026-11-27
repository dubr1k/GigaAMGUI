//! Stateful GigaAM v4 / Multilingual v2 RNNT streaming.
//!
//! All graph files and external tensors must be verified by the pinned bundle
//! installer before a stream is loaded. Timestamps use the model frame clock.

use std::fs;
use std::path::{Component, Path};
use std::sync::atomic::{AtomicBool, Ordering};

use gigastt_core::inference::WordInfo;
use gigastt_core::runtime_api::{cpu_factory, RuntimeSession, Shape, Tensor, TensorData};
use kaldi_fbank::fft::{compute_power_spectrum, Rfft};
use sentencepiece_rs::SentencePieceProcessor;
use serde::Deserialize;

use crate::asr_zapformer::ZapformerModel;

const RATE: usize = 16_000;
const FFT: usize = 320;
const HOP: usize = 160;
const MELS: usize = 64;
const SPECTRUM: usize = FFT / 2 + 1;
const HIDDEN: usize = 320;
const MAX_CACHE_ELEMENTS: usize = 64 * 1024 * 1024;
const MAX_MANIFEST_BYTES: u64 = 1_000_000;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct StateSpec {
    shape: Vec<usize>,
    dtype: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct FrontSpec {
    file: String,
    states: Vec<StateSpec>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct StepSpec {
    file: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    model_name: String,
    future_context_ms: usize,
    batched: bool,
    subsampling_factor: usize,
    frame_shift_ms: f64,
    encoder_dim: usize,
    frontend: FrontSpec,
    step: StepSpec,
    states: Vec<StateSpec>,
}

fn check_cancelled(cancelled: &AtomicBool) -> Result<(), String> {
    if cancelled.load(Ordering::Relaxed) { Err("Задание отменено".into()) } else { Ok(()) }
}

fn f32_tensor(shape: &[usize], values: Vec<f32>) -> Result<Tensor, String> {
    Tensor::new(Shape::new(shape.to_vec()), TensorData::F32(values)).map_err(|e| e.to_string())
}

fn i64_tensor(shape: &[usize], values: Vec<i64>) -> Result<Tensor, String> {
    Tensor::new(Shape::new(shape.to_vec()), TensorData::I64(values)).map_err(|e| e.to_string())
}

fn cache_elements(spec: &StateSpec) -> Result<usize, String> {
    if spec.shape.len() > 8 {
        return Err("Слишком много измерений состояния модели".into());
    }
    let count = spec.shape.iter().try_fold(1usize, |n, &dim| n.checked_mul(dim))
        .ok_or_else(|| "Переполнение размера состояния модели".to_string())?;
    if count > MAX_CACHE_ELEMENTS {
        return Err("Состояние модели слишком велико".into());
    }
    match spec.dtype.as_str() {
        "float32" | "int32" | "int64" => Ok(count),
        _ => Err("Неподдерживаемый тип состояния модели".into()),
    }
}

fn zero_states(specs: &[StateSpec]) -> Result<Vec<Tensor>, String> {
    let mut total = 0usize;
    let mut output = Vec::with_capacity(specs.len());
    for spec in specs {
        let count = cache_elements(spec)?;
        total = total.checked_add(count).ok_or_else(|| "Переполнение состояния модели".to_string())?;
        if total > MAX_CACHE_ELEMENTS { return Err("Состояние модели слишком велико".into()); }
        let data = match spec.dtype.as_str() {
            "float32" => TensorData::F32(vec![0.0; count]),
            "int32" => TensorData::I32(vec![0; count]),
            "int64" => TensorData::I64(vec![0; count]),
            _ => return Err("Неподдерживаемый тип состояния модели".into()),
        };
        output.push(Tensor::new(Shape::new(spec.shape.clone()), data).map_err(|e| e.to_string())?);
    }
    Ok(output)
}

fn valid_state(value: &Tensor, spec: &StateSpec) -> bool {
    if value.shape().dims() != spec.shape.as_slice() { return false; }
    let view = value.view();
    match spec.dtype.as_str() {
        "float32" => view.data().as_f32().is_some(),
        "int32" => view.data().as_i32().is_some(),
        "int64" => view.data().as_i64().is_some(),
        _ => false,
    }
}

fn one_i64(value: &Tensor) -> Result<i64, String> {
    if value.shape().dims() != [1] { return Err("Неверная форма состояния счётчика".into()); }
    let view = value.view();
    let data = view.data().as_i64().ok_or("Неверный тип состояния счётчика")?;
    Ok(data[0])
}

fn graph_path(dir: &Path, name: &str) -> Result<std::path::PathBuf, String> {
    let path = Path::new(name);
    let mut parts = path.components();
    if !matches!(parts.next(), Some(Component::Normal(_))) || parts.next().is_some()
        || name.contains(['\\', ':'])
    {
        return Err("Недопустимое имя графа потоковой модели".into());
    }
    let path = dir.join(path);
    let metadata = fs::symlink_metadata(&path).map_err(|e| format!("Нет графа модели: {e}"))?;
    if !metadata.file_type().is_file() { return Err("Граф модели не является обычным файлом".into()); }
    Ok(path)
}

fn read_manifest(dir: &Path, channels: usize) -> Result<Manifest, String> {
    let path = dir.join("streaming.json");
    let metadata = fs::symlink_metadata(&path).map_err(|e| format!("Нет описания потоковой модели: {e}"))?;
    if !metadata.file_type().is_file() || metadata.len() > MAX_MANIFEST_BYTES {
        return Err("Некорректное описание потоковой модели".into());
    }
    let bytes = fs::read(path).map_err(|e| format!("Не удалось прочитать описание модели: {e}"))?;
    let manifest: Manifest = serde_json::from_slice(&bytes).map_err(|e| format!("Некорректное описание модели: {e}"))?;
    if manifest.model_name != "rnnt_320ms" || manifest.future_context_ms != 320 || manifest.batched
        || manifest.encoder_dim != channels || manifest.subsampling_factor == 0 || manifest.subsampling_factor > 16
        || !manifest.frame_shift_ms.is_finite() || manifest.frame_shift_ms <= 0.0
        || manifest.frame_shift_ms != manifest.subsampling_factor as f64 * HOP as f64 * 1000.0 / RATE as f64
    {
        return Err("Несовместимая геометрия потоковой модели".into());
    }
    let frames = manifest.future_context_ms as f64 / manifest.frame_shift_ms;
    if frames.fract() != 0.0 || !(1.0..=64.0).contains(&frames) {
        return Err("Неверный размер потокового пакета".into());
    }
    if manifest.frontend.file != "rnnt_320ms_stream_frontend.onnx"
        || manifest.step.file != "rnnt_320ms_stream_step.onnx"
        || manifest.frontend.states.len() > 64 || manifest.states.len() > 512
    {
        return Err("Несовместимые графы или состояния потоковой модели".into());
    }
    // Validate the total before allocating any state tensor.
    let mut total = 0usize;
    for state in manifest.frontend.states.iter().chain(&manifest.states) {
        total = total.checked_add(cache_elements(state)?).ok_or("Переполнение состояния модели")?;
        if total > MAX_CACHE_ELEMENTS { return Err("Состояние модели слишком велико".into()); }
    }
    Ok(manifest)
}

/// Newly committed text/words and the current uncommitted text. `partial` is
/// None when this call produced no new tokens; Some("") clears a prior partial.
pub(crate) struct StreamUpdate {
    pub(crate) text: String,
    pub(crate) partial: Option<String>,
    pub(crate) words: Vec<WordInfo>,
}

#[derive(Default)]
struct PendingWord {
    raw: String,
    start: u64,
    end: u64,
    confidence_sum: f32,
    count: usize,
}

impl PendingWord {
    fn commit(&mut self, output: &mut Vec<WordInfo>, frame_seconds: f64) {
        let text = self.raw.trim();
        if !text.is_empty() && self.count != 0 {
            let start = self.start as f64 * frame_seconds;
            let end = (self.end + 1) as f64 * frame_seconds;
            output.push(WordInfo::new(text, start, end, self.confidence_sum / self.count as f32, None));
        }
        *self = Self::default();
    }
}

pub(crate) struct ZapformerStream {
    manifest: Manifest,
    model: ZapformerModel,
    frontend: Box<dyn RuntimeSession>,
    step: Box<dyn RuntimeSession>,
    decoder: Box<dyn RuntimeSession>,
    joint: Box<dyn RuntimeSession>,
    tokenizer: SentencePieceProcessor,
    front_state: Vec<Tensor>,
    layer_state: Vec<Tensor>,
    position: Option<Tensor>,
    cache_valid: Option<Tensor>,
    decoder_inputs: [Tensor; 3],
    joint_inputs: [Tensor; 2],
    predicted: Vec<f32>,
    next_h: Vec<f32>,
    next_c: Vec<f32>,
    logits: Vec<f32>,
    have_prediction: bool,
    mel: Mel,
    wave: Vec<f32>,
    packet_frames: usize,
    received_samples: u64,
    encoded_frames: u64,
    tokens: Vec<usize>,
    stable_tokens: usize,
    published_any: bool,
    published_space: bool,
    pending_word: PendingWord,
    finished: bool,
}

impl ZapformerStream {
    /// The caller must keep the verified bundle immutable while sessions load.
    pub(crate) fn load(base: &Path, stream: &Path, model: ZapformerModel, threads: usize, cancelled: &AtomicBool) -> Result<Self, String> {
        check_cancelled(cancelled)?;
        let (channels, blank, _) = dimensions(model);
        let manifest = read_manifest(stream, channels)?;
        let packet_frames = (manifest.future_context_ms as f64 / manifest.frame_shift_ms) as usize
            * manifest.subsampling_factor;
        let tokenizer = SentencePieceProcessor::open(graph_path(base, "rnnt_e2e_tokenizer.model")?)
            .map_err(|e| format!("Не удалось загрузить токенизатор: {e}"))?;
        if tokenizer.model().vocab_size() != blank { return Err("Размер словаря не соответствует модели".into()); }
        let runtime = cpu_factory().create(threads.max(1)).map_err(|e| format!("Не удалось запустить ONNX Runtime: {e}"))?;
        let load = |dir: &Path, name: &str| -> Result<Box<dyn RuntimeSession>, String> {
            let path = graph_path(dir, name)?;
            runtime.load_session(&path, false).map_err(|e| format!("Не удалось загрузить {name}: {e}"))
        };
        let frontend = load(stream, &manifest.frontend.file)?;
        check_cancelled(cancelled)?;
        let step = load(stream, &manifest.step.file)?;
        check_cancelled(cancelled)?;
        let decoder = load(base, "rnnt_e2e_decoder.onnx")?;
        let joint = load(base, "rnnt_e2e_joint.onnx")?;
        check_cancelled(cancelled)?;
        let front_state = zero_states(&manifest.frontend.states)?;
        let layer_state = zero_states(&manifest.states)?;
        Ok(Self {
            manifest, model, frontend, step, decoder, joint, tokenizer, front_state, layer_state,
            position: Some(i64_tensor(&[1], vec![0])?), cache_valid: Some(i64_tensor(&[1], vec![0])?),
            decoder_inputs: [i64_tensor(&[1, 1], vec![blank as i64])?, f32_tensor(&[1, 1, HIDDEN], vec![0.0; HIDDEN])?, f32_tensor(&[1, 1, HIDDEN], vec![0.0; HIDDEN])?],
            joint_inputs: [f32_tensor(&[1, channels, 1], vec![0.0; channels])?, f32_tensor(&[1, HIDDEN, 1], vec![0.0; HIDDEN])?],
            predicted: Vec::with_capacity(HIDDEN), next_h: Vec::with_capacity(HIDDEN), next_c: Vec::with_capacity(HIDDEN),
            logits: Vec::with_capacity(blank + 1), have_prediction: false, mel: Mel::new(),
            wave: Vec::with_capacity(FFT + (packet_frames - 1) * HOP), packet_frames,
            received_samples: 0, encoded_frames: 0, tokens: Vec::new(), stable_tokens: 0,
            published_any: false, published_space: false, pending_word: PendingWord::default(), finished: false,
        })
    }

    /// Push arbitrary 16 kHz mono samples. An error makes this stream terminal.
    pub(crate) fn push(&mut self, mut samples: &[f32], cancelled: &AtomicBool) -> Result<StreamUpdate, String> {
        if self.finished { return Err("Поток уже завершён".into()); }
        let before = self.tokens.len();
        let mut words = Vec::new();
        let result = (|| {
            check_cancelled(cancelled)?;
            let packet_samples = FFT + (self.packet_frames - 1) * HOP;
            while !samples.is_empty() {
                check_cancelled(cancelled)?;
                let take = (packet_samples - self.wave.len()).min(samples.len());
                let chunk = &samples[..take];
                if chunk.iter().any(|sample| !sample.is_finite()) { return Err("Неконечный аудиосэмпл".into()); }
                self.received_samples = self.received_samples.checked_add(take as u64).ok_or("Слишком длинный поток")?;
                self.wave.extend_from_slice(chunk);
                samples = &samples[take..];
                if self.wave.len() == packet_samples {
                    self.process_packet(self.packet_frames, cancelled, &mut words)?;
                    let consumed = self.packet_frames * HOP;
                    self.wave.copy_within(consumed.., 0);
                    self.wave.truncate(packet_samples - consumed);
                }
            }
            self.make_update(before, false, words)
        })();
        if result.is_err() { self.finished = true; }
        result
    }

    /// Decode the actual buffered tail once; padding is applied to features,
    /// not to the audio timeline. Calling finish twice is an error.
    pub(crate) fn finish(&mut self, cancelled: &AtomicBool) -> Result<StreamUpdate, String> {
        if self.finished { return Err("Поток уже завершён".into()); }
        self.finished = true;
        let before = self.tokens.len();
        let mut words = Vec::new();
        check_cancelled(cancelled)?;
        if self.wave.len() >= FFT {
            let frames = (self.wave.len() - FFT) / HOP + 1;
            self.process_packet(frames, cancelled, &mut words)?;
        }
        self.pending_word.commit(&mut words, self.manifest.frame_shift_ms / 1000.0);
        let duration = self.received_samples as f64 / RATE as f64;
        for word in &mut words {
            word.end = word.end.min(duration);
            if word.start > word.end {
                return Err("Временная метка слова выходит за конец аудио".into());
            }
        }
        self.stable_tokens = self.tokens.len();
        self.make_update(before, true, words)
    }

    fn process_packet(&mut self, frames: usize, cancelled: &AtomicBool, words: &mut Vec<WordInfo>) -> Result<(), String> {
        check_cancelled(cancelled)?;
        if frames == 0 || frames > self.packet_frames { return Err("Неверный потоковый пакет".into()); }
        let features = self.mel.extract(&self.wave, frames, self.packet_frames, cancelled)?;
        let mut inputs = Vec::with_capacity(2 + self.front_state.len());
        inputs.push(f32_tensor(&[1, MELS, self.packet_frames], features)?);
        inputs.push(i64_tensor(&[1], vec![frames as i64])?);
        inputs.append(&mut self.front_state);
        let mut front = self.frontend.run(&inputs).map_err(|e| format!("Ошибка потокового frontend: {e}"))?;
        drop(inputs);
        if front.len() != 2 + self.manifest.frontend.states.len() { return Err("Неожиданные выходы frontend".into()); }
        let next_front = front.split_off(2);
        if next_front.iter().zip(&self.manifest.frontend.states).any(|(value, spec)| !valid_state(value, spec)) {
            return Err("Неверные состояния frontend".into());
        }
        self.front_state = next_front;
        let lengths = front.pop().ok_or("Нет длины frontend")?;
        let features = front.pop().ok_or("Нет выхода frontend")?;
        let valid = one_i64(&lengths)?;
        let shape = features.shape().dims();
        let channels = dimensions(self.model).0;
        if shape.len() != 3 || shape[0] != 1 || shape[2] != channels || valid < 0 || valid as usize > shape[1] {
            return Err("Неверный выход frontend".into());
        }
        let mut inputs = Vec::with_capacity(4 + self.layer_state.len() + 1);
        inputs.push(features);
        inputs.push(lengths);
        inputs.push(self.position.take().ok_or("Нет position")?);
        inputs.push(self.cache_valid.take().ok_or("Нет cache_valid")?);
        if self.model == ZapformerModel::MultilingualV2Rnnt {
            let mut language = vec![0.0; 32];
            language[0] = 1.0;
            inputs.push(f32_tensor(&[1, 32], language)?);
        }
        inputs.append(&mut self.layer_state);
        let mut result = self.step.run(&inputs).map_err(|e| format!("Ошибка потокового encoder: {e}"))?;
        drop(inputs);
        if result.len() != 4 + self.manifest.states.len() { return Err("Неожиданные выходы потокового encoder".into()); }
        let next_states = result.split_off(4);
        if next_states.iter().zip(&self.manifest.states).any(|(value, spec)| !valid_state(value, spec)) {
            return Err("Неверные состояния потокового encoder".into());
        }
        self.layer_state = next_states;
        let cache_valid = result.pop().ok_or("Нет cache_valid")?;
        let position = result.pop().ok_or("Нет position")?;
        if one_i64(&cache_valid)? < 0 || one_i64(&position)? < 0 { return Err("Отрицательное состояние encoder".into()); }
        self.cache_valid = Some(cache_valid);
        self.position = Some(position);
        let encoded_len = result.pop().ok_or("Нет длины encoder")?;
        let encoded = result.pop().ok_or("Нет выхода encoder")?;
        let count = one_i64(&encoded_len)?;
        let shape = encoded.shape().dims();
        if shape.len() != 3 || shape[0] != 1 || shape[1] != channels || count < 0 || count as usize > shape[2] {
            return Err("Неверный выход потокового encoder".into());
        }
        let stride = shape[2];
        let view = encoded.view();
        let data = view.data().as_f32().ok_or("Неверный тип выхода encoder")?;
        self.decode(data, stride, count as usize, cancelled, words)
    }

    fn decode(&mut self, data: &[f32], stride: usize, count: usize, cancelled: &AtomicBool, words: &mut Vec<WordInfo>) -> Result<(), String> {
        let (channels, blank, max_symbols) = dimensions(self.model);
        for frame in 0..count {
            check_cancelled(cancelled)?;
            let target = self.joint_inputs[0].as_f32_mut().ok_or("Неверный вход joint")?;
            for channel in 0..channels { target[channel] = data[channel * stride + frame]; }
            for _ in 0..max_symbols {
                check_cancelled(cancelled)?;
                if !self.have_prediction {
                    self.decoder.run_f32_into(&self.decoder_inputs, &mut [&mut self.predicted, &mut self.next_h, &mut self.next_c])
                        .map_err(|e| format!("Ошибка потокового decoder: {e}"))?;
                    if self.predicted.len() != HIDDEN || self.next_h.len() != HIDDEN || self.next_c.len() != HIDDEN {
                        return Err("Неверный выход потокового decoder".into());
                    }
                    self.joint_inputs[1].as_f32_mut().ok_or("Неверный вход joint")?.copy_from_slice(&self.predicted);
                    self.have_prediction = true;
                }
                self.joint.run_f32_into(&self.joint_inputs, &mut [&mut self.logits])
                    .map_err(|e| format!("Ошибка потокового joint: {e}"))?;
                if self.logits.len() != blank + 1 || self.logits.iter().any(|x| !x.is_finite()) { return Err("Неверный выход joint".into()); }
                let mut best = 0;
                for i in 1..self.logits.len() { if self.logits[i] > self.logits[best] { best = i; } }
                if best == blank { break; }
                let abs_frame = self.encoded_frames.checked_add(frame as u64).ok_or("Слишком длинный поток")?;
                let piece = self.tokenizer.id_to_piece(best).map_err(|e| format!("Ошибка токенизатора: {e}"))?;
                if piece.starts_with('▁') || piece == " " {
                    self.pending_word.commit(words, self.manifest.frame_shift_ms / 1000.0);
                    self.stable_tokens = if piece == " " { self.tokens.len() + 1 } else { self.tokens.len() };
                }
                if piece != " " {
                    if self.pending_word.count == 0 { self.pending_word.start = abs_frame; }
                    self.pending_word.raw.push_str(piece.strip_prefix('▁').unwrap_or(piece));
                    self.pending_word.end = abs_frame;
                    self.pending_word.confidence_sum += self.logits[best].exp();
                    self.pending_word.count += 1;
                }
                self.tokens.push(best);
                self.decoder_inputs[0].as_i64_mut().ok_or("Неверный вход decoder")?[0] = best as i64;
                self.decoder_inputs[1].as_f32_mut().ok_or("Неверный вход decoder")?.copy_from_slice(&self.next_h);
                self.decoder_inputs[2].as_f32_mut().ok_or("Неверный вход decoder")?.copy_from_slice(&self.next_c);
                self.have_prediction = false;
            }
        }
        self.encoded_frames = self.encoded_frames.checked_add(count as u64).ok_or("Слишком длинный поток")?;
        Ok(())
    }

    fn decode_fragment(&self, ids: &[usize], has_prefix: bool, prefix_space: bool) -> Result<String, String> {
        if ids.is_empty() { return Ok(String::new()); }
        let mut text = self.tokenizer.decode_ids(ids).map_err(|e| format!("Ошибка токенизатора: {e}"))?;
        let first = self.tokenizer.id_to_piece(ids[0]).map_err(|e| format!("Ошибка токенизатора: {e}"))?;
        // SentencePiece removes the first dummy-prefix space when decoding an
        // isolated fragment. Restore it only at an actual word boundary.
        if has_prefix && !prefix_space && first.starts_with('▁') && !text.is_empty() && !text.starts_with(' ') {
            text.insert(0, ' ');
        }
        Ok(text)
    }

    fn make_update(&mut self, before: usize, final_packet: bool, words: Vec<WordInfo>) -> Result<StreamUpdate, String> {
        let changed = before != self.tokens.len();
        if !changed && !final_packet { return Ok(StreamUpdate { text: String::new(), partial: None, words }); }
        let text = self.decode_fragment(&self.tokens[..self.stable_tokens], self.published_any, self.published_space)?;
        if !text.is_empty() {
            self.published_any = true;
            self.published_space = text.chars().last().is_some_and(char::is_whitespace);
        }
        let partial = if final_packet {
            Some(String::new())
        } else {
            let value = self.decode_fragment(&self.tokens[self.stable_tokens..], self.published_any, self.published_space)?;
            Some(value.trim_end_matches('\u{fffd}').to_owned())
        };
        // Committed tokens are no longer needed by the decoder state. Retain
        // only the current uncommitted word rather than re-decoding all history.
        self.tokens.drain(..self.stable_tokens);
        self.stable_tokens = 0;
        Ok(StreamUpdate { text, partial, words })
    }
}

fn dimensions(model: ZapformerModel) -> (usize, usize, usize) {
    match model {
        ZapformerModel::V4Rnnt => (768, 1024, 10),
        ZapformerModel::MultilingualV2Rnnt => (1024, 4026, 3),
    }
}

struct Mel {
    fft: Rfft,
    window: [f32; FFT],
    filters: Vec<f32>,
}

impl Mel {
    fn new() -> Self {
        let mut window = [0.0; FFT];
        for (i, weight) in window.iter_mut().enumerate() {
            *weight = 0.5 * (1.0 - (2.0 * std::f32::consts::PI * i as f32 / FFT as f32).cos());
        }
        let max_mel = 2595.0 * (1.0f32 + 8000.0 / 700.0).log10();
        let hz = |i: usize| 700.0 * (10.0f32.powf((max_mel * i as f32 / (MELS + 1) as f32) / 2595.0) - 1.0);
        let mut filters = vec![0.0; MELS * SPECTRUM];
        for mel in 0..MELS {
            let (left, center, right) = (hz(mel), hz(mel + 1), hz(mel + 2));
            for bin in 0..SPECTRUM {
                let frequency = bin as f32 * RATE as f32 / FFT as f32;
                filters[mel * SPECTRUM + bin] = ((frequency - left) / (center - left)).min((right - frequency) / (right - center)).max(0.0);
            }
        }
        Self { fft: Rfft::new(FFT), window, filters }
    }

    fn extract(&mut self, samples: &[f32], frames: usize, physical: usize, cancelled: &AtomicBool) -> Result<Vec<f32>, String> {
        if frames == 0 || frames > physical || samples.len() < FFT + (frames - 1) * HOP { return Err("Недостаточно аудиосэмплов".into()); }
        let mut output = vec![0.0; MELS * physical];
        let mut spectrum = [0.0f32; FFT];
        for frame in 0..frames {
            check_cancelled(cancelled)?;
            let start = frame * HOP;
            for i in 0..FFT { spectrum[i] = samples[start + i] * self.window[i]; }
            self.fft.compute(&mut spectrum);
            compute_power_spectrum(&mut spectrum);
            for mel in 0..MELS {
                let mut energy = 0.0;
                for bin in 0..SPECTRUM { energy += spectrum[bin] * self.filters[mel * SPECTRUM + bin]; }
                output[mel * physical + frame] = energy.clamp(1e-9, 1e9).ln();
            }
        }
        Ok(output)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tail_mel_padding_preserves_feature_frames() {
        let cancelled = AtomicBool::new(false);
        let samples = vec![0.25f32; FFT + 2 * HOP];
        let full = Mel::new().extract(&samples, 3, 3, &cancelled).unwrap();
        let padded = Mel::new().extract(&samples, 3, 8, &cancelled).unwrap();
        for mel in 0..MELS {
            assert_eq!(&full[mel * 3..mel * 3 + 3], &padded[mel * 8..mel * 8 + 3]);
            assert_eq!(&padded[mel * 8 + 3..mel * 8 + 8], &[0.0; 5]);
        }
    }

    #[test]
    fn cache_shape_overflow_and_unsupported_types_are_rejected() {
        assert!(cache_elements(&StateSpec { shape: vec![usize::MAX, 2], dtype: "float32".into() }).is_err());
        assert!(cache_elements(&StateSpec { shape: vec![1], dtype: "float16".into() }).is_err());
        assert!(zero_states(&[StateSpec { shape: vec![1, 2], dtype: "int64".into() }]).is_ok());
    }

    #[test]
    fn cancellation_stops_feature_extraction() {
        let cancelled = AtomicBool::new(true);
        assert!(Mel::new().extract(&[0.0; FFT], 1, 1, &cancelled).is_err());
    }
}
