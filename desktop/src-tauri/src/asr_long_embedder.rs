//! WeSpeaker ResNet34-LM embedding with the model's Kaldi-style frontend.
//! Embeddings are not identities. The caller must handle uncertainty and
//! overlap and discard provisional results when inference fails.


use std::fs::File;
use std::io::Read;
use std::path::Path;
use std::sync::Mutex;

use kaldi_fbank::{
    process_window, FbankComputer, FbankOptions, FeatureWindowFunction, FrameExtractionOptions,
    WindowType,
};
use polyvoice::onnx::{ExecutionProvider, InferenceRuntime, InferenceTensor, RuntimeSession};
use polyvoice::{Embedder, EmbedderError, FbankOnnxExtractor};
use sha2::{Digest, Sha256};

const SAMPLE_RATE: usize = 16_000;
const FRAME_SAMPLES: usize = 400;
const FRAME_SHIFT: usize = 160;
const FFT_SAMPLES: usize = 512;
const MEL_BINS: usize = 80;
const EMBEDDING_DIM: usize = 256;
// The model's published preprocessor_config.json declares min_num_frames = 9.
const MIN_SAMPLES: usize = FRAME_SAMPLES + 8 * FRAME_SHIFT;
const MAX_SAMPLES: usize = SAMPLE_RATE * 60;
const MODEL_SHA256: [u8; 32] = [
    0x39, 0x55, 0x44, 0x7b, 0x04, 0x99, 0xdc, 0x9e, 0x0a, 0x45, 0x41, 0xa8, 0x95, 0xdf,
    0x08, 0xb0, 0x3c, 0x69, 0x09, 0x8e, 0xba, 0x4e, 0x56, 0xc0, 0x2b, 0x56, 0x03, 0xe9,
    0xf7, 0xf4, 0xfc, 0xbb,
];

fn verify_model(path: &Path) -> Result<(), String> {
    let mut file = File::open(path).map_err(|e| format!("open speaker model: {e}"))?;
    let mut hash = Sha256::new();
    let mut buffer = [0u8; 64 * 1024];
    loop {
        let count = file.read(&mut buffer).map_err(|e| format!("read speaker model: {e}"))?;
        if count == 0 {
            break;
        }
        hash.update(&buffer[..count]);
    }
    if hash.finalize()[..] != MODEL_SHA256 {
        return Err("speaker model does not match the gigastt ResNet34-LM SHA-256".into());
    }
    Ok(())
}

fn fbank_options() -> FbankOptions {
    let mut opts = FbankOptions::default();
    opts.frame_opts.samp_freq = SAMPLE_RATE as f32;
    opts.frame_opts.frame_length_ms = 25.0;
    opts.frame_opts.frame_shift_ms = 10.0;
    opts.frame_opts.dither = 0.0;
    opts.frame_opts.preemph_coeff = 0.97;
    opts.frame_opts.remove_dc_offset = true;
    opts.frame_opts.window_type = WindowType::Hamming;
    opts.frame_opts.round_to_power_of_two = true;
    opts.frame_opts.snip_edges = true;
    opts.frame_opts.center = false;
    opts.mel_opts.num_bins = MEL_BINS as i32;
    opts.mel_opts.low_freq = 20.0;
    opts.mel_opts.high_freq = 0.0; // Nyquist = 8 kHz
    opts.mel_opts.htk_mode = false;
    opts.mel_opts.is_librosa = false;
    opts.use_energy = false;
    opts.use_log_fbank = true;
    opts.use_power = true;
    opts
}

/// Reusable Kaldi frontend, exposed so a reference implementation can compare
/// the centered `[frames, 80]` matrix before ONNX inference.
pub struct WespeakerFrontend {
    computer: FbankComputer,
    frame_options: FrameExtractionOptions,
    window: FeatureWindowFunction,
}

impl WespeakerFrontend {
    pub fn new() -> Self {
        let options = fbank_options();
        let frame_options = options.frame_opts.clone();
        let window = FeatureWindowFunction::from_frame_opts(&frame_options);
        Self { computer: FbankComputer::new(options), frame_options, window }
    }

    /// Mono PCM at 16 kHz on the usual normalized f32 scale. Mild resampler
    /// overshoot is allowed; NaNs, infinities and literal silence are rejected.
    /// Returns the frame count and a row-major, globally mean-centered matrix.
    pub fn extract(&mut self, samples: &[f32]) -> Result<(usize, Vec<f32>), EmbedderError> {
        if samples.len() < MIN_SAMPLES {
            return Err(EmbedderError::AudioTooShort {
                actual_secs: samples.len() as f32 / SAMPLE_RATE as f32,
                min_secs: MIN_SAMPLES as f32 / SAMPLE_RATE as f32,
            });
        }
        if samples.len() > MAX_SAMPLES {
            return Err(failed("speaker window exceeds the 60-second proof bound"));
        }
        if samples.iter().any(|sample| !sample.is_finite()) {
            return Err(failed("speaker audio contains non-finite samples"));
        }
        if samples.iter().all(|sample| *sample == 0.0) {
            return Err(failed("speaker audio is silent"));
        }

        let frames = 1 + (samples.len() - FRAME_SAMPLES) / FRAME_SHIFT;
        let mut features = vec![0.0; frames * MEL_BINS];
        let mut frame = [0.0f32; FFT_SAMPLES];
        let mut means = [0.0f64; MEL_BINS];

        for (index, row) in features.chunks_exact_mut(MEL_BINS).enumerate() {
            let start = index * FRAME_SHIFT;
            for (dest, sample) in frame[..FRAME_SAMPLES]
                .iter_mut()
                .zip(&samples[start..start + FRAME_SAMPLES])
            {
                *dest = *sample * 32_768.0;
            }
            frame[FRAME_SAMPLES..].fill(0.0);
            process_window(&self.frame_options, &self.window, &mut frame, None);
            self.computer.compute(0.0, 1.0, &mut frame, row);
            for (sum, value) in means.iter_mut().zip(row.iter()) {
                if !value.is_finite() {
                    return Err(failed("speaker frontend produced non-finite features"));
                }
                *sum += f64::from(*value);
            }
        }
        for mean in &mut means {
            *mean /= frames as f64;
        }
        for row in features.chunks_exact_mut(MEL_BINS) {
            for (value, mean) in row.iter_mut().zip(means.iter()) {
                *value -= *mean as f32;
            }
        }
        Ok((frames, features))
    }
}

impl Default for WespeakerFrontend {
    fn default() -> Self {
        Self::new()
    }
}

fn failed(detail: &str) -> EmbedderError {
    EmbedderError::InferenceFailed { detail: detail.into() }
}

struct State {
    session: RuntimeSession,
    frontend: WespeakerFrontend,
}

/// A single shared CPU session; it serializes calls and reuses its FFT plan.
pub struct WespeakerEmbedder {
    state: Mutex<State>,
}

impl WespeakerEmbedder {
    /// Verify and load the exact gigastt WeSpeaker model from a local file.
    pub fn load(path: &Path) -> Result<Self, String> {
        verify_model(path)?;
        let session = RuntimeSession::from_path(path, ExecutionProvider::Cpu, None)
            .map_err(|e| format!("load WeSpeaker model: {e}"))?;
        if session.input_names().len() != 1 {
            return Err("WeSpeaker model must have exactly one input".into());
        }
        Ok(Self {
            state: Mutex::new(State { session, frontend: WespeakerFrontend::new() }),
        })
    }
}

impl Embedder for WespeakerEmbedder {
    fn dim(&self) -> usize {
        EMBEDDING_DIM
    }

    fn embed(&self, samples: &[f32]) -> Result<Vec<f32>, EmbedderError> {
        let mut state = self.state.lock().map_err(|_| failed("speaker session lock poisoned"))?;
        let (frames, features) = state.frontend.extract(samples)?;
        let input = InferenceTensor::f32(vec![1, frames, MEL_BINS], features);
        let output = state.session.run_ordered(&[&input])
            .map_err(|e| failed(&format!("WeSpeaker inference: {e}")))?
            .into_iter().next().ok_or_else(|| failed("WeSpeaker returned no output"))?;
        let mut embedding = output.into_f32()
            .map_err(|e| failed(&format!("WeSpeaker output type: {e}")))?;
        if embedding.len() != EMBEDDING_DIM {
            return Err(EmbedderError::DimMismatch { expected: EMBEDDING_DIM, actual: embedding.len() });
        }
        if embedding.iter().any(|value| !value.is_finite()) {
            return Err(failed("WeSpeaker returned a non-finite embedding"));
        }
        let norm = embedding.iter().map(|value| f64::from(*value).powi(2)).sum::<f64>().sqrt();
        if !norm.is_finite() || norm == 0.0 {
            return Err(failed("WeSpeaker returned a zero or invalid embedding"));
        }
        for value in &mut embedding {
            *value = (f64::from(*value) / norm) as f32;
        }
        Ok(embedding)
    }
}

/// Load gigastt's upstream polyvoice frontend + model for an explicit baseline.
/// Its preprocessing is different from the Kaldi candidate above.
pub fn load_polyvoice_baseline(path: &Path) -> Result<FbankOnnxExtractor, String> {
    verify_model(path)?;
    FbankOnnxExtractor::new(path, EMBEDDING_DIM, 1, ExecutionProvider::Cpu)
        .map_err(|e| format!("load upstream polyvoice extractor: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn frontend_matches_batch_kaldi_api() {
        let samples: Vec<f32> = (0..SAMPLE_RATE)
            .map(|i| ((i as f32 * 0.071).sin() + 0.2 * (i as f32 * 0.013).cos()) * 0.2)
            .collect();
        let scaled: Vec<f32> = samples.iter().map(|sample| sample * 32_768.0).collect();
        let mut reference = kaldi_fbank::compute_fbank(&fbank_options(), &scaled);
        let frames = reference.len();
        let mut means = [0.0f64; MEL_BINS];
        for row in &reference {
            for (sum, value) in means.iter_mut().zip(row) {
                *sum += f64::from(*value);
            }
        }
        for mean in &mut means {
            *mean /= frames as f64;
        }
        for row in &mut reference {
            for (value, mean) in row.iter_mut().zip(means.iter()) {
                *value -= *mean as f32;
            }
        }
        let (actual_frames, actual) = WespeakerFrontend::new().extract(&samples).unwrap();
        assert_eq!(actual_frames, frames);
        for (value, expected) in actual.iter().zip(reference.iter().flatten()) {
            assert!((value - expected).abs() < 1e-4);
        }
    }

    /// Synthetic input only checks real model execution, not speaker quality.
    #[test]
    #[ignore = "requires the pinned model in WESPEAKER_MODEL"]
    fn real_model_produces_a_finite_unit_embedding() {
        let model = std::env::var("WESPEAKER_MODEL").expect("set WESPEAKER_MODEL");
        let embedder = WespeakerEmbedder::load(Path::new(&model)).unwrap();
        let samples: Vec<f32> = (0..SAMPLE_RATE)
            .map(|i| (i as f32 * 0.071).sin() * 0.2)
            .collect();
        let embedding = embedder.embed(&samples).unwrap();
        assert_eq!(embedding.len(), EMBEDDING_DIM);
        assert!(embedding.iter().all(|value| value.is_finite()));
        let norm = embedding.iter().map(|value| value * value).sum::<f32>().sqrt();
        assert!((norm - 1.0).abs() < 1e-3);
    }

    #[test]
    #[ignore = "requires pinned model and local public LibriSpeech samples"]
    fn real_speech_embeddings_are_finite() {
        let model = std::env::var("WESPEAKER_MODEL").expect("set WESPEAKER_MODEL");
        let embedder = WespeakerEmbedder::load(Path::new(&model)).unwrap();
        let samples = |path: &str| {
            hound::WavReader::open(path).unwrap().into_samples::<i16>()
                .map(|sample| sample.unwrap() as f32 / 32768.0).collect::<Vec<_>>()
        };
        let first = embedder.embed(&samples("speaker-251.wav")).unwrap();
        let second = embedder.embed(&samples("speaker-3536.wav")).unwrap();
        let repeated = embedder.embed(&samples("speaker-251-second.wav")).unwrap();
        eprintln!("same-speaker cosine: {}", cosine_similarity(&first, &repeated).unwrap());
        let similarity = cosine_similarity(&first, &second).unwrap();
        assert!(similarity.is_finite());
        eprintln!("different-speaker cosine: {similarity}");
    }
}

impl WespeakerEmbedder {
    /// Extract a model embedding without importing the `polyvoice::Embedder` trait.
    pub fn embed(&self, samples: &[f32]) -> Result<Vec<f32>, EmbedderError> {
        <Self as Embedder>::embed(self, samples)
    }
}

/// Cosine similarity of two real 256-dimensional model embeddings. This does
/// not classify speakers or provide a calibrated same-speaker threshold.
pub fn cosine_similarity(first: &[f32], second: &[f32]) -> Result<f32, EmbedderError> {
    for embedding in [first, second] {
        if embedding.len() != EMBEDDING_DIM {
            return Err(EmbedderError::DimMismatch {
                expected: EMBEDDING_DIM,
                actual: embedding.len(),
            });
        }
    }
    let (mut dot, mut first_norm, mut second_norm) = (0.0f64, 0.0f64, 0.0f64);
    for (&first, &second) in first.iter().zip(second) {
        if !first.is_finite() || !second.is_finite() {
            return Err(failed("cannot compare non-finite embeddings"));
        }
        let first = f64::from(first);
        let second = f64::from(second);
        dot += first * second;
        first_norm += first * first;
        second_norm += second * second;
    }
    if first_norm == 0.0 || second_norm == 0.0 {
        return Err(failed("cannot compare zero embeddings"));
    }
    Ok((dot / (first_norm.sqrt() * second_norm.sqrt())) as f32)
}

#[cfg(test)]
#[test]
fn cosine_similarity_checks_dimensions_and_zero_vectors() {
    let mut first = vec![0.0; EMBEDDING_DIM];
    let mut second = vec![0.0; EMBEDDING_DIM];
    first[0] = 1.0;
    second[1] = 1.0;
    assert_eq!(cosine_similarity(&first, &first).unwrap(), 1.0);
    assert_eq!(cosine_similarity(&first, &second).unwrap(), 0.0);
    assert!(cosine_similarity(&first, &[]).is_err());
    assert!(cosine_similarity(&first, &[0.0; EMBEDDING_DIM]).is_err());
}
