//! Experimental bounded diarization on the canonical 16 kHz timeline.
//!
//! Powerset confidence is an average rather than per-frame certainty, and
//! neither speaker identities nor overlap detection are guaranteed.
//! Callbacks are provisional until `diarize` succeeds. Cancellation is
//! cooperative between synchronous model calls.


use std::fmt;
use std::fs::File;
use std::io::Read;
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};

use polyvoice::onnx::ExecutionProvider;
use polyvoice::segmentation::{PowersetConfig, PowersetSegmenter, RawSegment, Segmenter};
use polyvoice::{EarshotVad, VoiceActivityDetector};
use sha2::{Digest, Sha256};

use super::embedder::WespeakerEmbedder;
use super::identity::{SpeakerAssignment, SpeakerEvidence, SpeakerTracker, TrackerConfig};
use super::audio::{for_each_window, DecodeError};

const SAMPLE_RATE: usize = 16_000;
const WINDOW_SAMPLES: usize = 10 * SAMPLE_RATE;
// Prefer a pause in the latter half of a window without allowing arbitrarily
// short model calls. If no suitable pause exists, keep the full window.
const MIN_WINDOW_SAMPLES: usize = WINDOW_SAMPLES / 2;
const VAD_FRAME: usize = 256;
// A sustained pause is a possible speaker boundary, not proof of one.
// Round up to whole VAD frames when identifying such a pause below.
const MIN_PAUSE_SAMPLES: usize = SAMPLE_RATE / 4;
const EMBEDDING_DIM: usize = 256;
const MIN_EMBED_SAMPLES: usize = 400 + 8 * 160;
const SEGMENT_MODEL_NAME: &str = "powerset_fp32_tract.onnx";
const SEGMENT_MODEL_SHA256: [u8; 32] = [
    0xd5, 0x33, 0x5d, 0xe3, 0x34, 0xa2, 0x44, 0x70, 0xb1, 0xf0, 0x32, 0x6f, 0x72, 0xe5,
    0x13, 0xc5, 0x9e, 0x15, 0x63, 0x08, 0xa7, 0x8b, 0xa3, 0xe1, 0x62, 0x68, 0x51, 0xf5,
    0x70, 0x69, 0x6c, 0xe4,
];

// Internal per-sample segmentation labels. Distinct solo indices are local to
// one powerset call, and are never reused directly as session speaker IDs.
const SILENCE: u8 = 0;
const AMBIGUOUS: u8 = 1;
const OVERLAP: u8 = 2;
const SOLO_BASE: u8 = 3;

#[derive(Clone, Copy, Debug)]
pub(crate) struct PipelineConfig {
    pub(crate) tracker: TrackerConfig,
    /// Minimum mean powerset segment confidence; this is NOT a frame guarantee.
    pub(crate) segment_confidence: f32,
    pub(crate) vad_threshold: f32,
    /// Minimum fraction of VAD-positive frames within a solo candidate.
    pub(crate) min_vad_fraction: f32,
    pub(crate) min_speech_samples: usize,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum RegionKind {
    Speaker(u32),
    Unknown,
    /// The supported powerset API explicitly flagged overlapping speech.
    Overlap,
}

/// Half-open interval `[start_sample, end_sample)` on the decoded 16 kHz
/// timeline. Unknown and overlap regions never contain a forced speaker ID.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct Region {
    pub(crate) start_sample: u64,
    pub(crate) end_sample: u64,
    pub(crate) kind: RegionKind,
}

#[derive(Debug)]
pub(crate) enum PipelineError {
    Cancelled,
    Invalid(String),
    Model(String),
    Decode(DecodeError),
    Stage(String),
    Sink(String),
}

impl fmt::Display for PipelineError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Cancelled => write!(f, "diarization cancelled"),
            Self::Invalid(e) => write!(f, "invalid diarization input: {e}"),
            Self::Model(e) => write!(f, "model error: {e}"),
            Self::Decode(e) => write!(f, "audio decode: {e}"),
            Self::Stage(e) => write!(f, "diarization stage: {e}"),
            Self::Sink(e) => write!(f, "diarization sink: {e}"),
        }
    }
}

impl std::error::Error for PipelineError {}

fn check_cancelled(cancelled: &AtomicBool) -> Result<(), PipelineError> {
    if cancelled.load(Ordering::Acquire) {
        Err(PipelineError::Cancelled)
    } else {
        Ok(())
    }
}

fn verify_segment_model(path: &Path, cancelled: &AtomicBool) -> Result<(), PipelineError> {
    // Polyvoice may silently substitute a tract sibling for other powerset
    // filenames. Restricting the name also ensures it loads the file we hash.
    if path.file_name().and_then(|n| n.to_str()) != Some(SEGMENT_MODEL_NAME) {
        return Err(PipelineError::Invalid(format!(
            "segmentation model must be named {SEGMENT_MODEL_NAME}"
        )));
    }
    let mut file = File::open(path).map_err(|e| PipelineError::Model(e.to_string()))?;
    let mut hasher = Sha256::new();
    let mut bytes = [0u8; 64 * 1024];
    loop {
        check_cancelled(cancelled)?;
        let n = file.read(&mut bytes).map_err(|e| PipelineError::Model(e.to_string()))?;
        if n == 0 {
            break;
        }
        hasher.update(&bytes[..n]);
    }
    if hasher.finalize()[..] != SEGMENT_MODEL_SHA256 {
        return Err(PipelineError::Model("unexpected powerset model SHA-256".into()));
    }
    Ok(())
}

fn check_pool_bound() -> Result<(), PipelineError> {
    // Polyvoice permits an environment variable to override config.pool_size.
    // Reject it rather than silently multiplying the fixed model allocation.
    if polyvoice::onnx::resolve_session_pool_size(1) != 1 {
        return Err(PipelineError::Invalid(
            "POLYVOICE_SESSION_POOL_SIZE must resolve to 1 for this bounded proof".into(),
        ));
    }
    Ok(())
}

pub(crate) struct Pipeline {
    segmenter: PowersetSegmenter,
    embedder: WespeakerEmbedder,
}

impl Pipeline {
    pub(crate) fn load(
        segment_model: &Path,
        speaker_model: &Path,
        cancelled: &AtomicBool,
    ) -> Result<Self, PipelineError> {
        check_cancelled(cancelled)?;
        check_pool_bound()?;
        verify_segment_model(segment_model, cancelled)?;
        // One 10-second input and one session per call. A 10-second hop avoids
        // building extra partial windows; cross-window identity uses embeddings.
        let config = PowersetConfig {
            window_secs: 10.0,
            hop_secs: 10.0,
            sample_rate: SAMPLE_RATE as u32,
            pool_size: 1,
            batch_size: 1,
            ..PowersetConfig::default()
        };
        let loaded = PowersetSegmenter::with_config(segment_model, config, ExecutionProvider::Cpu);
        check_cancelled(cancelled)?;
        let segmenter = loaded.map_err(|e| PipelineError::Model(e.to_string()))?;
        if !segmenter.supports_overlap() || segmenter.max_local_speakers() != 3 {
            return Err(PipelineError::Model("segmenter lacks expected overlap support".into()));
        }
        let loaded = WespeakerEmbedder::load(speaker_model);
        check_cancelled(cancelled)?;
        let embedder = loaded.map_err(PipelineError::Model)?;
        Ok(Self { segmenter, embedder })
    }

    /// Runs a fresh tracker and VAD session. All callback outputs are provisional
    /// until `Ok(())`; the caller must roll them back on *any* error.
    pub(crate) fn diarize<F>(
        &self,
        path: &Path,
        config: PipelineConfig,
        cancelled: &AtomicBool,
        mut emit: F,
    ) -> Result<(), PipelineError>
    where
        F: FnMut(Region) -> Result<(), String>,
    {
        check_cancelled(cancelled)?;
        check_pool_bound()?;
        if config.tracker.embedding_dim != EMBEDDING_DIM
            || !(0.5..=1.0).contains(&config.segment_confidence)
            || !(0.0..=1.0).contains(&config.vad_threshold)
            || config.vad_threshold == 0.0
            || !(0.0..=1.0).contains(&config.min_vad_fraction)
            || config.min_vad_fraction == 0.0
            || !(MIN_EMBED_SAMPLES..=WINDOW_SAMPLES).contains(&config.min_speech_samples)
        {
            return Err(PipelineError::Invalid("invalid pipeline thresholds or dimensions".into()));
        }
        let mut tracker = SpeakerTracker::new(config.tracker)
            .map_err(|e| PipelineError::Invalid(format!("tracker configuration: {e:?}")))?;
        let mut vad = EarshotVad::new();
        let mut next_start = 0u64;
        let mut buffer_start = 0u64;
        let mut buffer = Vec::with_capacity(2 * WINDOW_SAMPLES);
        let mut padded = Vec::new();
        let mut first = 0usize;
        let mut callback_error = None;
        let decoded = for_each_window(path, WINDOW_SAMPLES, cancelled, |start, samples| {
            let step = (|| {
                check_cancelled(cancelled)?;
                if start != next_start || samples.is_empty() || samples.len() > WINDOW_SAMPLES {
                    return Err(PipelineError::Invalid("non-contiguous decoder window".into()));
                }
                next_start = start.checked_add(samples.len() as u64)
                    .ok_or_else(|| PipelineError::Invalid("sample timeline overflow".into()))?;

                // At most one full undecided window remains between decoder
                // callbacks. Compact only when the bounded buffer needs space.
                if buffer.len() + samples.len() > 2 * WINDOW_SAMPLES {
                    let remaining = buffer.len() - first;
                    buffer.copy_within(first.., 0);
                    buffer.truncate(remaining);
                    first = 0;
                }
                buffer.extend_from_slice(samples);
                while buffer.len() - first >= WINDOW_SAMPLES {
                    let window = &buffer[first..first + WINDOW_SAMPLES];
                    let boundary = choose_window_boundary_with_vad(window, config.vad_threshold, cancelled)?;
                    self.process_window(buffer_start, &window[..boundary], &mut padded, config, &mut vad, &mut tracker, cancelled, &mut emit)?;
                    buffer_start = buffer_start.checked_add(boundary as u64)
                        .ok_or_else(|| PipelineError::Invalid("sample timeline overflow".into()))?;
                    first += boundary;
                }
                Ok(())
            })();
            match step {
                Ok(()) => Ok(()),
                Err(error) => {
                    callback_error = Some(error);
                    Err("diarization window failed".into())
                }
            }
        });
        if let Some(error) = callback_error {
            return Err(error);
        }
        decoded.map_err(|e| match e {
            DecodeError::Cancelled => PipelineError::Cancelled,
            other => PipelineError::Decode(other),
        })?;
        if buffer_start.checked_add((buffer.len() - first) as u64) != Some(next_start) {
            return Err(PipelineError::Invalid("non-contiguous buffered timeline".into()));
        }
        if first < buffer.len() {
            self.process_window(buffer_start, &buffer[first..], &mut padded, config, &mut vad, &mut tracker, cancelled, &mut emit)?;
        }
        check_cancelled(cancelled)
    }

    fn process_window<F>(
        &self,
        start: u64,
        samples: &[f32],
        padded: &mut Vec<f32>,
        config: PipelineConfig,
        vad: &mut EarshotVad,
        tracker: &mut SpeakerTracker,
        cancelled: &AtomicBool,
        emit: &mut F,
    ) -> Result<(), PipelineError>
    where
        F: FnMut(Region) -> Result<(), String>,
    {
        check_pool_bound()?;
        // Every variable-length window is padded only for powerset. No result
        // outside the actual input is ever emitted or embedded.
        let input = if samples.len() == WINDOW_SAMPLES {
            samples
        } else {
            padded.resize(WINDOW_SAMPLES, 0.0);
            padded[..samples.len()].copy_from_slice(samples);
            padded[samples.len()..].fill(0.0);
            padded.as_slice()
        };
        // Do not feed the next real window's VAD session the model's padding.
        // Only its final partial VAD frame needs zero padding.
        let vad_frames = samples.len().div_ceil(VAD_FRAME);
        check_cancelled(cancelled)?;
        let scored = vad.process(&input[..vad_frames * VAD_FRAME]);
        check_cancelled(cancelled)?;
        let scores = scored.map_err(|e| PipelineError::Stage(format!("VAD: {e}")))?;
        if scores.len() != vad_frames
            || scores.iter().any(|p| !(0.0..=1.0).contains(p))
        {
            return Err(PipelineError::Stage("invalid VAD frame output".into()));
        }
        let segmented = self.segmenter.segment(input);
        check_cancelled(cancelled)?;
        let segments = segmented.map_err(|e| PipelineError::Stage(format!("segmentation: {e}")))?;
        let mut labels = vec![SILENCE; samples.len()];
        for segment in &segments {
            check_cancelled(cancelled)?;
            mark_segment(&mut labels, segment, config.segment_confidence)?;
        }

        // Reduce to VAD frames. Any frame straddling a powerset transition is
        // unknown, rather than embedding audio that may contain two speakers.
        let mut frames = Vec::with_capacity(samples.len().div_ceil(VAD_FRAME));
        for (i, chunk) in labels.chunks(VAD_FRAME).enumerate() {
            let mut class = chunk[0];
            for &label in &chunk[1..] {
                if label == OVERLAP || class == OVERLAP {
                    class = OVERLAP;
                } else if label != class {
                    class = AMBIGUOUS;
                }
            }
            let voiced = scores[i] >= config.vad_threshold;
            if class == SILENCE && voiced {
                class = AMBIGUOUS;
            }
            // A partial VAD frame or a chunk boundary has incomplete context.
            if class >= SOLO_BASE && (i == 0 || (i + 1) * VAD_FRAME >= samples.len()) {
                class = AMBIGUOUS;
            }
            frames.push((class, voiced));
        }

        // A local powerset label can persist across a pause and encompass
        // different speakers. Do not make one embedding from both sides.
        split_solo_at_pauses(&mut frames, samples);

        let mut first = 0;
        while first < frames.len() {
            check_cancelled(cancelled)?;
            let class = frames[first].0;
            let mut last = first + 1;
            while last < frames.len() && frames[last].0 == class {
                last += 1;
            }
            let local_start = first * VAD_FRAME;
            let local_end = (last * VAD_FRAME).min(samples.len());
            let kind = if class == SILENCE {
                None
            } else if class == OVERLAP {
                Some(RegionKind::Overlap)
            } else if class == AMBIGUOUS {
                Some(RegionKind::Unknown)
            } else {
                let voiced = frames[first..last].iter().filter(|(_, voiced)| *voiced).count();
                let fraction = voiced as f32 / (last - first) as f32;
                if local_end - local_start < config.min_speech_samples
                    || fraction < config.min_vad_fraction
                {
                    Some(RegionKind::Unknown)
                } else {
                    check_cancelled(cancelled)?;
                    let result = self.embedder.embed(&samples[local_start..local_end]);
                    check_cancelled(cancelled)?;
                    let embedding = result.map_err(|e| PipelineError::Stage(format!("embedding: {e}")))?;
                    let assignment = tracker.assign(SpeakerEvidence::SingleSpeaker(&embedding))
                        .map_err(|e| PipelineError::Stage(format!("tracker: {e:?}")))?;
                    Some(match assignment {
                        SpeakerAssignment::Known(id) => RegionKind::Speaker(id),
                        SpeakerAssignment::Unknown => RegionKind::Unknown,
                    })
                }
            };
            if let Some(kind) = kind {
                let start_sample = start.checked_add(local_start as u64)
                    .ok_or_else(|| PipelineError::Invalid("sample timeline overflow".into()))?;
                let end_sample = start.checked_add(local_end as u64)
                    .ok_or_else(|| PipelineError::Invalid("sample timeline overflow".into()))?;
                check_cancelled(cancelled)?;
                let result = emit(Region { start_sample, end_sample, kind });
                check_cancelled(cancelled)?;
                result.map_err(PipelineError::Sink)?;
            }
            first = last;
        }
        Ok(())
    }
}

fn frame_is_pause(samples: &[f32], frame: usize, voiced: bool) -> bool {
    if !voiced {
        return true;
    }
    let start = frame * VAD_FRAME;
    let end = (start + VAD_FRAME).min(samples.len());
    samples[start..end].iter().all(|sample| *sample == 0.0)
}

/// Prefer the last sustained pause in the latter half of a full window.
/// A pause is only a safe place to cut audio; it does not assign a speaker.
fn choose_window_boundary(samples: &[f32], scores: &[f32], threshold: f32) -> usize {
    let mut cursor = MIN_WINDOW_SAMPLES.div_ceil(VAD_FRAME);
    let mut boundary = WINDOW_SAMPLES;
    while cursor < scores.len() {
        if !frame_is_pause(samples, cursor, scores[cursor] >= threshold) {
            cursor += 1;
            continue;
        }
        let pause_start = cursor;
        while cursor < scores.len() && frame_is_pause(samples, cursor, scores[cursor] >= threshold) {
            cursor += 1;
        }
        // A pause that reaches the window end needs no early cut.
        if cursor < scores.len() && (cursor - pause_start) * VAD_FRAME >= MIN_PAUSE_SAMPLES {
            boundary = ((pause_start + cursor) / 2) * VAD_FRAME;
        }
    }
    boundary
}

fn choose_window_boundary_with_vad(
    samples: &[f32],
    threshold: f32,
    cancelled: &AtomicBool,
) -> Result<usize, PipelineError> {
    // The lookahead detector has separate state: the session VAD must only
    // consume real samples through the boundary actually selected.
    let mut lookahead = EarshotVad::new();
    check_cancelled(cancelled)?;
    let scored = lookahead.process(samples);
    check_cancelled(cancelled)?;
    let scores = scored.map_err(|e| PipelineError::Stage(format!("boundary VAD: {e}")))?;
    if scores.len() != WINDOW_SAMPLES / VAD_FRAME
        || scores.iter().any(|p| !(0.0..=1.0).contains(p))
    {
        return Err(PipelineError::Stage("invalid boundary VAD frame output".into()));
    }
    Ok(choose_window_boundary(samples, &scores, threshold))
}

/// Split a local solo label at an interior sustained non-speech interval.
///
/// VAD disagreement is kept as Unknown rather than being silently removed.
/// Digital silence is also a pause even if the stateful VAD hangs over it.
/// Neither a pause nor a local speaker label establishes speaker identity.
fn split_solo_at_pauses(frames: &mut [(u8, bool)], samples: &[f32]) {
    fn is_pause(frames: &[(u8, bool)], samples: &[f32], frame: usize) -> bool {
        frame_is_pause(samples, frame, frames[frame].1)
    }

    let mut first = 0;
    while first < frames.len() {
        let class = frames[first].0;
        let mut last = first + 1;
        while last < frames.len() && frames[last].0 == class {
            last += 1;
        }
        if class >= SOLO_BASE {
            let mut cursor = first;
            while cursor < last {
                if !is_pause(frames, samples, cursor) {
                    cursor += 1;
                    continue;
                }
                let pause_start = cursor;
                while cursor < last && is_pause(frames, samples, cursor) {
                    cursor += 1;
                }
                if pause_start > first && cursor < last
                    && (cursor - pause_start) * VAD_FRAME >= MIN_PAUSE_SAMPLES
                {
                    for frame in &mut frames[pause_start..cursor] {
                        frame.0 = AMBIGUOUS;
                    }
                }
            }
        }
        first = last;
    }
}

fn mark_segment(labels: &mut [u8], segment: &RawSegment, threshold: f32) -> Result<(), PipelineError> {
    let start = segment.time.start;
    let end = segment.time.end;
    // Polyvoice's grid can round the padded window's last frame slightly past
    // 10 s. Permit at most 100 ms of grid rounding, never negative timestamps.
    if !start.is_finite() || !end.is_finite() || start < 0.0 || end <= start
        || start > 10.0 || end > 10.1 || segment.local_speaker_idx >= 3
        || !(0.0..=1.0).contains(&segment.confidence.get())
    {
        return Err(PipelineError::Stage("invalid powerset segment".into()));
    }
    let first = ((start * SAMPLE_RATE as f64).floor() as usize).min(labels.len());
    let last = ((end * SAMPLE_RATE as f64).ceil() as usize).min(labels.len());
    let incoming = if segment.is_overlap {
        OVERLAP
    } else if segment.confidence.get() < threshold {
        AMBIGUOUS
    } else {
        SOLO_BASE + segment.local_speaker_idx
    };
    for label in &mut labels[first..last] {
        *label = match (*label, incoming) {
            (OVERLAP, _) | (_, OVERLAP) => OVERLAP,
            (AMBIGUOUS, _) | (_, AMBIGUOUS) => AMBIGUOUS,
            (SILENCE, value) => value,
            (old, value) if old == value => old,
            // Two conflicting solo labels are not a supported overlap report.
            _ => AMBIGUOUS,
        };
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use polyvoice::types::{Confidence, TimeRange};

    fn raw(start: f64, end: f64, speaker: u8, overlap: bool, confidence: f32) -> RawSegment {
        RawSegment {
            time: TimeRange { start, end },
            local_speaker_idx: speaker,
            is_overlap: overlap,
            confidence: Confidence::new(confidence).unwrap(),
        }
    }

    #[test]
    fn supported_overlap_and_conflicting_solos_never_become_clean_evidence() {
        let mut labels = vec![SILENCE; SAMPLE_RATE];
        mark_segment(&mut labels, &raw(0.0, 0.75, 0, false, 0.99), 0.8).unwrap();
        mark_segment(&mut labels, &raw(0.25, 0.5, 1, false, 0.99), 0.8).unwrap();
        mark_segment(&mut labels, &raw(0.5, 0.75, 1, true, 0.99), 0.8).unwrap();
        mark_segment(&mut labels, &raw(0.75, 1.0, 2, false, 0.7), 0.8).unwrap();
        assert!(labels[..4000].iter().all(|x| *x == SOLO_BASE));
        assert!(labels[4000..8000].iter().all(|x| *x == AMBIGUOUS));
        assert!(labels[8000..12000].iter().all(|x| *x == OVERLAP));
        assert!(labels[12000..].iter().all(|x| *x == AMBIGUOUS));
    }

    #[test]
    fn invalid_segment_times_are_errors_and_padded_tail_is_clipped() {
        let mut labels = vec![SILENCE; SAMPLE_RATE];
        assert!(mark_segment(&mut labels, &raw(-0.1, 0.5, 0, false, 0.9), 0.8).is_err());
        assert!(mark_segment(&mut labels, &raw(0.0, 10.2, 0, false, 0.9), 0.8).is_err());
        mark_segment(&mut labels, &raw(0.5, 9.0, 0, false, 0.9), 0.8).unwrap();
        assert!(labels[..8000].iter().all(|x| *x == SILENCE));
        assert!(labels[8000..].iter().all(|x| *x == SOLO_BASE));
    }

    #[test]
    fn solo_label_across_sustained_silence_is_not_one_speaker_embedding() {
        let speech_frames = 40;
        let pause_frames = MIN_PAUSE_SAMPLES.div_ceil(VAD_FRAME);
        let total = 2 * speech_frames + pause_frames;
        let mut frames = vec![(SOLO_BASE, true); total];
        let mut samples = vec![0.25; total * VAD_FRAME];
        samples[speech_frames * VAD_FRAME..(speech_frames + pause_frames) * VAD_FRAME]
            .fill(0.0);

        // Even with VAD hangover, the exact digital silence separates the
        // observations. Preserve both speech sides and mark only the pause.
        split_solo_at_pauses(&mut frames, &samples);
        assert!(frames[..speech_frames].iter().all(|frame| *frame == (SOLO_BASE, true)));
        assert!(frames[speech_frames..speech_frames + pause_frames]
            .iter().all(|frame| *frame == (AMBIGUOUS, true)));
        assert!(frames[speech_frames + pause_frames..]
            .iter().all(|frame| *frame == (SOLO_BASE, true)));
    }

    #[test]
    fn sustained_vad_gap_splits_but_short_or_non_solo_gaps_do_not() {
        let pause_frames = MIN_PAUSE_SAMPLES.div_ceil(VAD_FRAME);
        let samples = vec![0.01; (pause_frames + 2) * VAD_FRAME];
        let mut frames = vec![(SOLO_BASE, false); pause_frames + 2];
        frames[0].1 = true;
        frames[pause_frames + 1].1 = true;
        split_solo_at_pauses(&mut frames, &samples);
        assert_eq!(frames[0].0, SOLO_BASE);
        assert!(frames[1..=pause_frames].iter().all(|frame| frame.0 == AMBIGUOUS));
        assert_eq!(frames[pause_frames + 1].0, SOLO_BASE);

        let mut short = vec![(SOLO_BASE, false); pause_frames + 1];
        short[0].1 = true;
        short[pause_frames].1 = true;
        let before = short.clone();
        split_solo_at_pauses(&mut short, &samples);
        assert_eq!(short, before);

        let mut overlap = vec![(OVERLAP, false); pause_frames + 2];
        let before = overlap.clone();
        split_solo_at_pauses(&mut overlap, &samples);
        assert_eq!(overlap, before);
    }

    #[test]
    fn full_windows_end_inside_a_sustained_pause_not_mid_speech() {
        let mut samples = vec![0.25; WINDOW_SAMPLES];
        let scores = vec![1.0; WINDOW_SAMPLES / VAD_FRAME];
        let pause_start = MIN_WINDOW_SAMPLES.div_ceil(VAD_FRAME) + 40;
        let pause_end = pause_start + MIN_PAUSE_SAMPLES.div_ceil(VAD_FRAME) + 4;
        samples[pause_start * VAD_FRAME..pause_end * VAD_FRAME].fill(0.0);
        let boundary = choose_window_boundary(&samples, &scores, 0.5);
        assert_eq!(boundary, ((pause_start + pause_end) / 2) * VAD_FRAME);
        assert!(boundary >= MIN_WINDOW_SAMPLES && boundary < WINDOW_SAMPLES);

        // A VAD-negative pause with nonzero room noise is also usable.
        samples.fill(0.25);
        let mut scores = scores;
        scores[pause_start..pause_end].fill(0.0);
        assert_eq!(choose_window_boundary(&samples, &scores, 0.5), boundary);
    }

    #[test]
    fn windows_without_a_complete_long_late_pause_keep_their_full_length() {
        let samples = vec![0.25; WINDOW_SAMPLES];
        let mut scores = vec![1.0; WINDOW_SAMPLES / VAD_FRAME];
        assert_eq!(choose_window_boundary(&samples, &scores, 0.5), WINDOW_SAMPLES);
        let start = MIN_WINDOW_SAMPLES.div_ceil(VAD_FRAME) + 10;
        let short = MIN_PAUSE_SAMPLES.div_ceil(VAD_FRAME) - 1;
        scores[start..start + short].fill(0.0);
        assert_eq!(choose_window_boundary(&samples, &scores, 0.5), WINDOW_SAMPLES);
        scores.fill(1.0);
        scores[start..].fill(0.0);
        assert_eq!(choose_window_boundary(&samples, &scores, 0.5), WINDOW_SAMPLES);
    }

    fn fixture(name: &str) -> std::path::PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).join(name)
    }

    fn read_public_sample(name: &str) -> Vec<i16> {
        let reader = hound::WavReader::open(fixture(name)).unwrap();
        let spec = reader.spec();
        assert_eq!(spec.channels, 1);
        assert_eq!(spec.sample_rate, SAMPLE_RATE as u32);
        assert_eq!(spec.sample_format, hound::SampleFormat::Int);
        assert_eq!(spec.bits_per_sample, 16);
        reader.into_samples::<i16>().map(Result::unwrap).collect()
    }

    fn fixture_config() -> PipelineConfig {
        // Fixture-only hypotheses, not calibrated production thresholds.
        PipelineConfig {
            tracker: TrackerConfig {
                max_speakers: 8,
                embedding_dim: EMBEDDING_DIM,
                match_threshold: 0.50,
                new_speaker_threshold: 0.25,
                ambiguity_margin: 0.05,
            },
            segment_confidence: 0.55,
            vad_threshold: 0.5,
            min_vad_fraction: 0.2,
            min_speech_samples: SAMPLE_RATE / 2,
        }
    }

    fn main_speaker(regions: &[Region], from: usize, to: usize) -> Option<u32> {
        let mut durations = std::collections::HashMap::<u32, u64>::new();
        for region in regions {
            if let RegionKind::Speaker(id) = region.kind {
                let lo = region.start_sample.max(from as u64);
                let hi = region.end_sample.min(to as u64);
                if hi > lo {
                    *durations.entry(id).or_default() += hi - lo;
                }
            }
        }
        durations.into_iter().max_by_key(|(id, duration)| (*duration, std::cmp::Reverse(*id)))
            .map(|(id, _)| id)
    }

    fn speaker_samples(regions: &[Region], id: u32, from: usize, to: usize) -> u64 {
        regions.iter().filter(|region| region.kind == RegionKind::Speaker(id))
            .map(|region| region.end_sample.min(to as u64)
                .saturating_sub(region.start_sample.max(from as u64)))
            .sum()
    }

    #[test]
    #[ignore = "requires local models and quality-evaluation/input/holdout.wav"]
    fn heldout_pause_must_not_merge_speakers_or_split_the_returning_speaker() {
        // This example has been inspected for development and is no longer
        // an independent holdout for quality or threshold selection.
        let cancel = AtomicBool::new(false);
        let pipeline = Pipeline::load(&fixture(SEGMENT_MODEL_NAME), &fixture("model.onnx"), &cancel).unwrap();
        let mut regions = Vec::new();
        pipeline.diarize(&fixture("quality-evaluation/input/holdout.wav"), fixture_config(), &cancel, |region| {
            regions.push(region);
            Ok(())
        }).unwrap();

        // Source clips are 5536 at 266560..330560 and 6295 at
        // 336960..400960, with 400 ms of digital silence between them.
        // The old output contained one speaker region at 320256..401664.
        assert!(regions.iter().all(|region| region.start_sample < region.end_sample
            && region.end_sample <= 525760));
        assert!(regions.windows(2).all(|pair| pair[0].end_sample <= pair[1].start_sample));
        assert!(regions.iter().all(|region| !matches!(region.kind, RegionKind::Speaker(_))
            || region.start_sample >= 330560 || region.end_sample <= 336960));
        let a = main_speaker(&regions, 266560, 320000).expect("returning 5536 unidentified");
        let a_tail = main_speaker(&regions, 320256, 330560).unwrap_or_else(|| {
            let nearby: Vec<_> = regions.iter()
                .filter(|region| region.start_sample < 336960 && region.end_sample > 320000)
                .collect();
            panic!("5536 window tail unidentified; nearby regions: {nearby:?}");
        });
        let b = main_speaker(&regions, 141440, 205440).expect("first 6295 utterance unidentified");
        let b_return = main_speaker(&regions, 336960, 400960).expect("returning 6295 unidentified");
        let c = main_speaker(&regions, 6400, 64640).expect("first 777 utterance unidentified");
        let c_return = main_speaker(&regions, 211840, 260160).expect("returning 777 unidentified");
        assert_eq!(a, a_tail);
        assert_eq!(b, b_return);
        assert_eq!(c, c_return);
        assert_ne!(a, b);
        assert_ne!(a, c);
        assert_ne!(b, c);
        for (id, start, end) in [
            (a, 266560, 320000), (b, 141440, 205440), (b, 336960, 400960),
            (c, 6400, 64640), (c, 211840, 260160),
        ] {
            assert!(speaker_samples(&regions, id, start, end) >= SAMPLE_RATE as u64);
        }
        assert!(speaker_samples(&regions, a, 320256, 330560) >= (SAMPLE_RATE / 5) as u64);
    }

    #[test]
    #[ignore = "requires local pinned powerset and WeSpeaker models and public speech fixtures"]
    fn real_models_process_two_speakers_repeat_and_overlap_with_absolute_times() {
        let cancel = AtomicBool::new(false);
        let pipeline = Pipeline::load(&fixture(SEGMENT_MODEL_NAME), &fixture("model.onnx"), &cancel).unwrap();
        let a = read_public_sample("speaker-251.wav");
        let b = read_public_sample("speaker-3536.wav");
        let a2 = read_public_sample("speaker-251-second.wav");
        // The overlap fixture is CC BY-NC: local test only, do not distribute.
        let mix = read_public_sample("overlap-noncommercial.wav");
        let mut audio = vec![0i16; SAMPLE_RATE / 2];
        let a_start = audio.len();
        audio.extend_from_slice(&a);
        let a_end = audio.len();
        audio.resize(audio.len() + SAMPLE_RATE / 2, 0);
        let b_start = audio.len();
        audio.extend_from_slice(&b);
        let b_end = audio.len();
        assert!(audio.len() < WINDOW_SAMPLES);
        audio.resize(WINDOW_SAMPLES + SAMPLE_RATE / 2, 0);
        let a2_start = audio.len();
        audio.extend_from_slice(&a2);
        let a2_end = audio.len();
        audio.resize(audio.len() + SAMPLE_RATE / 2, 0);
        let mix_start = audio.len();
        audio.extend_from_slice(&mix);
        let mix_end = audio.len();

        let mut file = tempfile::NamedTempFile::new().unwrap();
        {
            let spec = hound::WavSpec { channels: 1, sample_rate: SAMPLE_RATE as u32,
                bits_per_sample: 16, sample_format: hound::SampleFormat::Int };
            let mut writer = hound::WavWriter::new(file.as_file_mut(), spec).unwrap();
            for sample in &audio { writer.write_sample(*sample).unwrap(); }
            writer.finalize().unwrap();
        }
        let mut regions = Vec::new();
        pipeline.diarize(file.path(), fixture_config(), &cancel, |r| {
            regions.push(r);
            Ok(())
        }).unwrap();
        assert!(!regions.is_empty());
        assert!(regions.iter().all(|r| r.start_sample < r.end_sample && r.end_sample <= audio.len() as u64));
        assert!(regions.windows(2).all(|w| w[0].end_sample <= w[1].start_sample));
        eprintln!("fixture ranges: first={a_start}..{a_end}, second={b_start}..{b_end}, repeated={a2_start}..{a2_end}, overlap={mix_start}..{mix_end}; regions={regions:?}");
        let first = main_speaker(&regions, a_start, a_end).expect("first speaker was not identified");
        let second = main_speaker(&regions, b_start, b_end).expect("second speaker was not identified");
        let repeated = main_speaker(&regions, a2_start, a2_end).expect("repeat speaker was not identified");
        assert_ne!(first, second);
        assert_eq!(first, repeated);
        assert!(regions.iter().any(|r| r.kind == RegionKind::Overlap
            && r.start_sample < mix_end as u64 && r.end_sample > mix_start as u64),
            "the real segmenter did not detect overlap in the fixture");

        // The same real second speaker must not be forced into the only
        // occupied slot. The deliberately strict thresholds make this a
        // capacity/fail-closed check, not a speaker-quality calibration.
        let mut capped = fixture_config();
        capped.tracker.max_speakers = 1;
        capped.tracker.match_threshold = 0.999_999_94;
        capped.tracker.new_speaker_threshold = 0.999_999_9;
        let mut capped_regions = Vec::new();
        pipeline.diarize(file.path(), capped, &cancel, |r| {
            capped_regions.push(r);
            Ok(())
        }).unwrap();
        assert!(main_speaker(&capped_regions, a_start, a_end).is_some());
        assert!(main_speaker(&capped_regions, b_start, b_end).is_none());
        let unknown_on_b: u64 = capped_regions.iter().filter(|r| r.kind == RegionKind::Unknown)
            .map(|r| r.end_sample.min(b_end as u64).saturating_sub(r.start_sample.max(b_start as u64)))
            .sum();
        assert!(unknown_on_b >= (SAMPLE_RATE / 4) as u64);

        // The first callback cancels the session: no later result may escape,
        // and the previously delivered provisional region must be discarded.
        let mut callbacks = 0;
        let result = pipeline.diarize(file.path(), fixture_config(), &cancel, |_| {
            callbacks += 1;
            cancel.store(true, Ordering::Release);
            Ok(())
        });
        assert!(matches!(result, Err(PipelineError::Cancelled)));
        assert_eq!(callbacks, 1);
    }
}
