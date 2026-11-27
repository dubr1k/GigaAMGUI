//! Full-context GigaAM v4 / Multilingual v2 RNNT inference.
//!
//! The caller must first verify the complete bundle with `asr_zapformer_assets`
//! and keep its application-controlled directory immutable while sessions load.

use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};

use gigastt_core::inference::WordInfo;
use gigastt_core::runtime_api::{cpu_factory, RuntimeSession, Shape, Tensor, TensorData};
use kaldi_fbank::fft::{compute_power_spectrum, Rfft};
use sentencepiece_rs::SentencePieceProcessor;

const RATE: usize = 16_000;
const FFT: usize = 320;
const HOP: usize = 160;
const MELS: usize = 64;
const SPECTRUM: usize = FFT / 2 + 1;
const HIDDEN: usize = 320;
const MAX_SAMPLES: usize = 30 * RATE;
const GRAPHS: [&str; 3] = ["rnnt_encoder.onnx", "rnnt_e2e_decoder.onnx", "rnnt_e2e_joint.onnx"];
const TOKENIZER: &str = "rnnt_e2e_tokenizer.model";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum ZapformerModel {
    V4Rnnt,
    MultilingualV2Rnnt,
}

impl ZapformerModel {
    pub(crate) fn id(self) -> &'static str {
        match self {
            Self::V4Rnnt => "v4_rnnt",
            Self::MultilingualV2Rnnt => "multilingual_v2_rnnt",
        }
    }


    fn dimensions(self) -> (usize, usize, usize) {
        match self {
            Self::V4Rnnt => (768, 1024, 10),
            Self::MultilingualV2Rnnt => (1024, 4026, 3),
        }
    }
}


fn check_cancelled(cancelled: &AtomicBool) -> Result<(), String> {
    if cancelled.load(Ordering::Relaxed) {
        Err("Задание отменено".into())
    } else {
        Ok(())
    }
}


pub(crate) struct Zapformer {
    model: ZapformerModel,
    encoder: Box<dyn RuntimeSession>,
    decoder: Box<dyn RuntimeSession>,
    joint: Box<dyn RuntimeSession>,
    tokenizer: SentencePieceProcessor,
    mel: Mel,
}

pub(crate) struct ZapformerResult {
    pub(crate) text: String,
    pub(crate) words: Vec<WordInfo>,
}

impl Zapformer {
    pub(crate) fn load(dir: &Path, model: ZapformerModel, threads: usize, cancelled: &AtomicBool) -> Result<Self, String> {
        check_cancelled(cancelled)?;
        let tokenizer = SentencePieceProcessor::open(dir.join(TOKENIZER)).map_err(|e| format!("Не удалось загрузить токенизатор: {e}"))?;
        if tokenizer.model().vocab_size() != model.dimensions().1 {
            return Err("Размер словаря не соответствует модели".into());
        }
        let runtime = cpu_factory().create(threads.max(1)).map_err(|e| format!("Не удалось запустить ONNX Runtime: {e}"))?;
        // Avoid ORT's optimized-encoder cache: the multilingual graph has
        // external data, and writing a second 3 GB copy is unnecessary here.
        let load = |name: &str| runtime.load_session(&dir.join(name), false).map_err(|e| format!("Не удалось загрузить {name}: {e}"));
        let encoder = load(GRAPHS[0])?;
        check_cancelled(cancelled)?;
        let decoder = load(GRAPHS[1])?;
        let joint = load(GRAPHS[2])?;
        check_cancelled(cancelled)?;
        Ok(Self { model, encoder, decoder, joint, tokenizer, mel: Mel::new() })
    }

    /// PCM must be mono f32 at 16 kHz. Long files must be segmented by the
    /// caller; one full-context model invocation is limited to 30 seconds.
    pub(crate) fn transcribe(&mut self, samples: &[f32], cancelled: &AtomicBool) -> Result<ZapformerResult, String> {
        check_cancelled(cancelled)?;
        if samples.is_empty() {
            return Ok(ZapformerResult { text: String::new(), words: Vec::new() });
        }
        if samples.len() < FFT || samples.len() > MAX_SAMPLES || samples.iter().any(|s| !s.is_finite()) {
            return Err("Нужен конечный моно PCM 16 кГц продолжительностью от 20 мс до 30 с".into());
        }
        let (features, frames) = self.mel.extract(samples, cancelled)?;
        let mut inputs = vec![f32_tensor(&[1, MELS, frames], features)?, i64_tensor(&[1], vec![frames as i64])?];
        if self.model == ZapformerModel::MultilingualV2Rnnt {
            let mut language = vec![0.0; 32];
            language[0] = 1.0; // Official "auto" slot.
            inputs.push(f32_tensor(&[1, 32], language)?);
        }
        let mut outputs = self.encoder.run(&inputs).map_err(|e| format!("Ошибка encoder: {e}"))?;
        if outputs.len() != 2 { return Err("Неожиданные выходы encoder".into()); }
        let lengths = outputs.pop().unwrap();
        let encoded = outputs.pop().unwrap();
        let (channels, blank, max_symbols) = self.model.dimensions();
        let shape = encoded.shape().dims();
        if shape.len() != 3 || shape[0] != 1 || shape[1] != channels {
            return Err("Неожиданная форма выхода encoder".into());
        }
        let stride = shape[2];
        let data = encoded.view();
        let encoded_data = data.data().as_f32().ok_or("Неверный тип выхода encoder")?;
        let lengths_view = lengths.view();
        let length = lengths_view.data().as_i64().and_then(|v| if v.len() == 1 { usize::try_from(v[0]).ok() } else { None }).ok_or("Неверная длина encoder")?;
        if length == 0 || length > stride { return Err("Неверная длина encoder".into()); }
        check_cancelled(cancelled)?;
        let mut decoder_inputs = [i64_tensor(&[1, 1], vec![blank as i64])?, f32_tensor(&[1, 1, HIDDEN], vec![0.0; HIDDEN])?, f32_tensor(&[1, 1, HIDDEN], vec![0.0; HIDDEN])?];
        let mut joint_inputs = [f32_tensor(&[1, channels, 1], vec![0.0; channels])?, f32_tensor(&[1, HIDDEN, 1], vec![0.0; HIDDEN])?];
        let mut predicted = Vec::with_capacity(HIDDEN);
        let mut next_h = Vec::with_capacity(HIDDEN);
        let mut next_c = Vec::with_capacity(HIDDEN);
        let mut logits = Vec::with_capacity(blank + 1);
        let mut tokens = Vec::new();
        let mut token_frames = Vec::new();
        let mut confidences = Vec::new();
        let mut have_prediction = false;
        for frame in 0..length {
            check_cancelled(cancelled)?;
            let enc_frame = joint_inputs[0].as_f32_mut().ok_or("Неверный вход joint")?;
            for channel in 0..channels { enc_frame[channel] = encoded_data[channel * stride + frame]; }
            for _ in 0..max_symbols {
                check_cancelled(cancelled)?;
                if !have_prediction {
                    self.decoder.run_f32_into(&decoder_inputs, &mut [&mut predicted, &mut next_h, &mut next_c]).map_err(|e| format!("Ошибка decoder: {e}"))?;
                    if predicted.len() != HIDDEN || next_h.len() != HIDDEN || next_c.len() != HIDDEN {
                        return Err("Неожиданная форма выхода decoder".into());
                    }
                    joint_inputs[1].as_f32_mut().ok_or("Неверный вход joint")?.copy_from_slice(&predicted);
                    have_prediction = true;
                }
                self.joint.run_f32_into(&joint_inputs, &mut [&mut logits]).map_err(|e| format!("Ошибка joint: {e}"))?;
                if logits.len() != blank + 1 || logits.iter().any(|v| !v.is_finite()) {
                    return Err("Некорректный выход joint".into());
                }
                let mut best = 0;
                for i in 1..logits.len() { if logits[i] > logits[best] { best = i; } }
                if best == blank { break; }
                tokens.push(best);
                token_frames.push(frame);
                confidences.push(logits[best].exp()); // joint emits log-softmax.
                decoder_inputs[0].as_i64_mut().ok_or("Неверный вход decoder")?[0] = best as i64;
                decoder_inputs[1].as_f32_mut().ok_or("Неверный вход decoder")?.copy_from_slice(&next_h);
                decoder_inputs[2].as_f32_mut().ok_or("Неверный вход decoder")?.copy_from_slice(&next_c);
                have_prediction = false;
            }
        }
        let text = self.tokenizer.decode_ids(&tokens).map_err(|e| format!("Ошибка токенизатора: {e}"))?;
        let words = self.words(&tokens, &token_frames, &confidences, samples.len() as f64 / RATE as f64 / length as f64)?;
        Ok(ZapformerResult { text, words })
    }

    fn words(&self, tokens: &[usize], frames: &[usize], confidence: &[f32], frame_shift: f64) -> Result<Vec<WordInfo>, String> {
        let mut words = Vec::new();
        let mut word = String::new();
        let mut start = 0usize;
        let mut end = 0usize;
        let mut sum = 0.0f32;
        let mut count = 0usize;
        let commit = |words: &mut Vec<WordInfo>, word: &mut String, start, end, sum, count| {
            let value = word.trim();
            if !value.is_empty() {
                words.push(WordInfo::new(value, start as f64 * frame_shift, (end + 1) as f64 * frame_shift, sum / count as f32, None));
            }
            word.clear();
        };
        for ((&token, &frame), &score) in tokens.iter().zip(frames).zip(confidence) {
            let piece = self.tokenizer.id_to_piece(token).map_err(|e| format!("Ошибка токенизатора: {e}"))?;
            if piece.starts_with('▁') || piece == " " {
                if count != 0 { commit(&mut words, &mut word, start, end, sum, count); }
                count = 0;
                sum = 0.0;
                if piece == " " { continue; }
            }
            if count == 0 { start = frame; }
            word.push_str(piece.strip_prefix('▁').unwrap_or(piece));
            end = frame;
            sum += score;
            count += 1;
        }
        if count != 0 { commit(&mut words, &mut word, start, end, sum, count); }
        Ok(words)
    }
}

fn f32_tensor(dims: &[usize], values: Vec<f32>) -> Result<Tensor, String> {
    Tensor::new(Shape::new(dims.to_vec()), TensorData::F32(values)).map_err(|e| e.to_string())
}

fn i64_tensor(dims: &[usize], values: Vec<i64>) -> Result<Tensor, String> {
    Tensor::new(Shape::new(dims.to_vec()), TensorData::I64(values)).map_err(|e| e.to_string())
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
                let frequency = bin as f32 * (RATE as f32 / FFT as f32);
                filters[mel * SPECTRUM + bin] = ((frequency - left) / (center - left)).min((right - frequency) / (right - center)).max(0.0);
            }
        }
        Self { fft: Rfft::new(FFT), window, filters }
    }

    fn extract(&mut self, samples: &[f32], cancelled: &AtomicBool) -> Result<(Vec<f32>, usize), String> {
        let frames = (samples.len() - FFT) / HOP + 1;
        let mut output = vec![0.0; MELS * frames];
        let mut spectrum = [0.0f32; FFT];
        for frame in 0..frames {
            if frame % 100 == 0 { check_cancelled(cancelled)?; }
            let start = frame * HOP;
            for i in 0..FFT { spectrum[i] = samples[start + i] * self.window[i]; }
            self.fft.compute(&mut spectrum);
            compute_power_spectrum(&mut spectrum);
            for mel in 0..MELS {
                let mut energy = 0.0;
                for bin in 0..SPECTRUM { energy += spectrum[bin] * self.filters[mel * SPECTRUM + bin]; }
                output[mel * frames + frame] = energy.clamp(1e-9, 1e9).ln();
            }
        }
        Ok((output, frames))
    }
}
