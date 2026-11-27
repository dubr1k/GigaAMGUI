//! Streaming WAVE reader used for the shared canonical 16 kHz timeline.
//! Callbacks are provisional until decoding completes successfully.


use std::fmt;
use std::fs::File;
use std::io::{self, Read, Seek, SeekFrom};
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};

use audioadapter_buffers::direct::InterleavedSlice;
use rubato::{
    Async, FixedAsync, Indexing, Resampler, SincInterpolationParameters, WindowFunction,
};
use ryf::{ByteSource, ChannelMode, DecodeOptions, WavError};

pub const OUTPUT_RATE: u32 = 16_000;
pub const MAX_WINDOW_SAMPLES: usize = OUTPUT_RATE as usize * 60;
const MIN_INPUT_RATE: u32 = 8_000;
const MAX_INPUT_RATE: u32 = 192_000;
const RESAMPLE_CHUNK: usize = 1024;
const MAX_READ: usize = 64 * 1024;

#[derive(Debug)]
pub enum DecodeError {
    Cancelled,
    Unsupported(String),
    Io(io::Error),
    Invalid(String),
    Decode(String),
    Callback(String),
}

impl fmt::Display for DecodeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Cancelled => write!(f, "audio decoding cancelled"),
            Self::Unsupported(reason) => write!(f, "unsupported audio: {reason}"),
            Self::Io(error) => write!(f, "audio I/O error: {error}"),
            Self::Invalid(reason) => write!(f, "invalid audio: {reason}"),
            Self::Decode(reason) => write!(f, "audio decode error: {reason}"),
            Self::Callback(reason) => write!(f, "audio window callback failed: {reason}"),
        }
    }
}

impl std::error::Error for DecodeError {}

fn check_cancelled(cancelled: &AtomicBool) -> Result<(), DecodeError> {
    if cancelled.load(Ordering::Acquire) {
        Err(DecodeError::Cancelled)
    } else {
        Ok(())
    }
}

// `Interrupted` is intentionally not used: Read::read_exact is allowed to
// retry Interrupted forever after the cancellation flag has been set.
struct CancellableFile<'a> {
    file: File,
    cancelled: &'a AtomicBool,
}

impl Read for CancellableFile<'_> {
    fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
        if self.cancelled.load(Ordering::Acquire) {
            return Err(io::Error::other("audio decoding cancelled"));
        }
        let len = buffer.len().min(MAX_READ);
        self.file.read(&mut buffer[..len])
    }
}

impl Seek for CancellableFile<'_> {
    fn seek(&mut self, pos: SeekFrom) -> io::Result<u64> {
        if self.cancelled.load(Ordering::Acquire) {
            return Err(io::Error::other("audio decoding cancelled"));
        }
        self.file.seek(pos)
    }
}

struct WindowSink<'a, F> {
    cancelled: &'a AtomicBool,
    callback: &'a mut F,
    window: Vec<f32>,
    window_samples: usize,
    next_start: u64,
}

impl<F> WindowSink<'_, F>
where
    F: FnMut(u64, &[f32]) -> Result<(), String>,
{
    fn append(&mut self, mut samples: &[f32]) -> Result<(), DecodeError> {
        while !samples.is_empty() {
            check_cancelled(self.cancelled)?;
            let count = samples.len().min(self.window_samples - self.window.len());
            self.window.extend_from_slice(&samples[..count]);
            samples = &samples[count..];
            if self.window.len() == self.window_samples {
                self.flush()?;
            }
        }
        Ok(())
    }

    fn flush(&mut self) -> Result<(), DecodeError> {
        if self.window.is_empty() {
            return check_cancelled(self.cancelled);
        }
        check_cancelled(self.cancelled)?;
        let next_start = self
            .next_start
            .checked_add(self.window.len() as u64)
            .ok_or_else(|| DecodeError::Invalid("sample index overflow".into()))?;
        (self.callback)(self.next_start, &self.window).map_err(DecodeError::Callback)?;
        self.next_start = next_start;
        self.window.clear();
        check_cancelled(self.cancelled)
    }
}

fn sinc_resampler(input_rate: u32) -> Result<Async<f32>, DecodeError> {
    let params = SincInterpolationParameters::new(256, WindowFunction::BlackmanHarris2);
    Async::<f32>::new_sinc(
        f64::from(OUTPUT_RATE) / f64::from(input_rate),
        1.0,
        &params,
        RESAMPLE_CHUNK,
        1,
        FixedAsync::Input,
    )
    .map_err(|error| DecodeError::Decode(format!("resampler setup: {error:?}")))
}

fn expected_samples(input_samples: u64, input_rate: u32) -> Result<u64, DecodeError> {
    let numerator = u128::from(input_samples) * u128::from(OUTPUT_RATE);
    let denominator = u128::from(input_rate);
    let count = numerator.div_ceil(denominator);
    u64::try_from(count).map_err(|_| DecodeError::Invalid("sample count overflow".into()))
}

struct StreamingResampler {
    rate: u32,
    resampler: Async<f32>,
    input: Vec<f32>,
    filled: usize,
    output: Vec<f32>,
    delay_left: usize,
    input_processed: u64,
    output_written: u64,
}

impl StreamingResampler {
    fn new(rate: u32) -> Result<Self, DecodeError> {
        let resampler = sinc_resampler(rate)?;
        if resampler.input_frames_next() != RESAMPLE_CHUNK {
            return Err(DecodeError::Decode("unexpected resampler input size".into()));
        }
        let delay_left = resampler.output_delay();
        let output = vec![0.0; resampler.output_frames_max()];
        Ok(Self {
            rate,
            resampler,
            input: vec![0.0; RESAMPLE_CHUNK],
            filled: 0,
            output,
            delay_left,
            input_processed: 0,
            output_written: 0,
        })
    }

    fn append<F>(
        &mut self,
        mut samples: &[f32],
        sink: &mut WindowSink<'_, F>,
    ) -> Result<(), DecodeError>
    where
        F: FnMut(u64, &[f32]) -> Result<(), String>,
    {
        while !samples.is_empty() {
            check_cancelled(sink.cancelled)?;
            let count = samples.len().min(RESAMPLE_CHUNK - self.filled);
            self.input[self.filled..self.filled + count].copy_from_slice(&samples[..count]);
            self.filled += count;
            samples = &samples[count..];
            if self.filled == RESAMPLE_CHUNK {
                self.process(RESAMPLE_CHUNK, false, sink)?;
                self.filled = 0;
            }
        }
        Ok(())
    }

    // A full call consumes RESAMPLE_CHUNK real input frames. A partial call
    // supplies `valid_input` real frames and lets Rubato zero-pad the rest.
    // The same zero-padding is used to pump out the final filter tail.
    fn process<F>(
        &mut self,
        valid_input: usize,
        finishing: bool,
        sink: &mut WindowSink<'_, F>,
    ) -> Result<(), DecodeError>
    where
        F: FnMut(u64, &[f32]) -> Result<(), String>,
    {
        check_cancelled(sink.cancelled)?;
        if valid_input > RESAMPLE_CHUNK || self.resampler.input_frames_next() != RESAMPLE_CHUNK {
            return Err(DecodeError::Decode("unexpected resampler input size".into()));
        }
        if self.resampler.output_frames_next() > self.output.len() {
            return Err(DecodeError::Decode("unexpected resampler output size".into()));
        }
        let input = InterleavedSlice::new(&self.input, 1, self.input.len())
            .map_err(|error| DecodeError::Decode(format!("resampler input: {error:?}")))?;
        let output_len = self.output.len();
        let mut output = InterleavedSlice::new_mut(&mut self.output, 1, output_len)
            .map_err(|error| DecodeError::Decode(format!("resampler output: {error:?}")))?;
        let indexing = if valid_input == RESAMPLE_CHUNK {
            None
        } else {
            Some(Indexing::new().partial_len(valid_input))
        };
        let (consumed, produced) = self
            .resampler
            .process_into_buffer(&input, &mut output, indexing.as_ref())
            .map_err(|error| DecodeError::Decode(format!("resampling: {error:?}")))?;
        if consumed != RESAMPLE_CHUNK || produced > self.output.len() {
            return Err(DecodeError::Decode("unexpected resampler frame count".into()));
        }
        self.input_processed = self
            .input_processed
            .checked_add(valid_input as u64)
            .ok_or_else(|| DecodeError::Invalid("input sample count overflow".into()))?;
        let trim = self.delay_left.min(produced);
        self.delay_left -= trim;
        let available = produced - trim;
        let expected = expected_samples(self.input_processed, self.rate)?;
        let remaining = expected
            .checked_sub(self.output_written)
            .ok_or_else(|| DecodeError::Decode("resampler produced too many samples".into()))?;
        if !finishing && available as u64 > remaining {
            return Err(DecodeError::Decode(
                "resampler output exceeds the current input duration".into(),
            ));
        }
        let take = available.min(usize::try_from(remaining).unwrap_or(usize::MAX));
        let samples = &self.output[trim..trim + take];
        if samples.iter().any(|sample| !sample.is_finite()) {
            return Err(DecodeError::Invalid("resampler produced non-finite samples".into()));
        }
        sink.append(samples)?;
        self.output_written = self
            .output_written
            .checked_add(take as u64)
            .ok_or_else(|| DecodeError::Invalid("output sample count overflow".into()))?;
        Ok(())
    }

    fn finish<F>(&mut self, sink: &mut WindowSink<'_, F>) -> Result<(), DecodeError>
    where
        F: FnMut(u64, &[f32]) -> Result<(), String>,
    {
        if self.filled > 0 {
            self.process(self.filled, true, sink)?;
            self.filled = 0;
        }
        let expected = expected_samples(self.input_processed, self.rate)?;
        // Rubato's complete-clip path also removes output_delay and pumps
        // zero input until ceil(input_frames * ratio) frames are available.
        let mut pumps = 0;
        while self.output_written < expected {
            if pumps == 1024 {
                return Err(DecodeError::Decode("resampler tail did not converge".into()));
            }
            self.process(0, true, sink)?;
            pumps += 1;
        }
        Ok(())
    }
}

fn map_wav_error(error: WavError, cancelled: &AtomicBool) -> DecodeError {
    if cancelled.load(Ordering::Acquire) {
        return DecodeError::Cancelled;
    }
    match error {
        unsupported @ (WavError::NotWave
        | WavError::UnsupportedCodec { .. }
        | WavError::UnsupportedSampleRate { .. }
        | WavError::FeatureDisabled { .. }) => {
            DecodeError::Unsupported(unsupported.to_string())
        }
        WavError::Io(error) => DecodeError::Io(error),
        other => DecodeError::Decode(other.to_string()),
    }
}

/// Decode a WAVE file into non-overlapping mono 16 kHz windows.
///
/// Input is read incrementally; buffers owned by this module are bounded by
/// `window_samples`, the fixed resampler chunk, and its configured rate range.
/// `ryf` documents O(block) scratch for its WAVE decode path. A callback error,
/// cancellation, unsupported encoding, malformed file, or resampling failure
/// is returned to the caller; windows already delivered must then be discarded.
pub fn for_each_window<F>(
    path: impl AsRef<Path>,
    window_samples: usize,
    cancelled: &AtomicBool,
    mut on_window: F,
) -> Result<(), DecodeError>
where
    F: FnMut(u64, &[f32]) -> Result<(), String>,
{
    check_cancelled(cancelled)?;
    if window_samples == 0 || window_samples > MAX_WINDOW_SAMPLES {
        return Err(DecodeError::Invalid(format!(
            "window size must be between 1 and {MAX_WINDOW_SAMPLES} samples"
        )));
    }
    let file = File::open(path).map_err(DecodeError::Io)?;
    let metadata = file.metadata().map_err(DecodeError::Io)?;
    if !metadata.is_file() {
        return Err(DecodeError::Unsupported("only regular files are supported".into()));
    }
    let reader = CancellableFile { file, cancelled };
    let mut source = ByteSource::try_from_read_seek(reader, Some(metadata.len()))
        .map_err(|error| {
            if cancelled.load(Ordering::Acquire) {
                DecodeError::Cancelled
            } else {
                DecodeError::Io(error)
            }
        })?;
    let is_wave = ryf::sniff_is_riff_wave(&mut source)
        .map_err(|error| map_wav_error(error, cancelled))?;
    if !is_wave {
        return Err(DecodeError::Unsupported(
            "this isolated experiment supports WAVE containers only".into(),
        ));
    }

    let options = DecodeOptions::unbounded()
        .with_channel_mode(ChannelMode::Mono)
        .with_max_sample_rate(MAX_INPUT_RATE)
        .with_max_decode_sample_rate(MAX_INPUT_RATE);
    let mut sink = WindowSink {
        cancelled,
        callback: &mut on_window,
        window: Vec::with_capacity(window_samples),
        window_samples,
        next_start: 0,
    };
    let mut source_rate = None;
    let mut resampler: Option<StreamingResampler> = None;
    let mut callback_failure = None;
    let result = ryf::decode_streaming(&mut source, &options, |block| {
        let step = (|| -> Result<(), DecodeError> {
            check_cancelled(cancelled)?;
            if !(MIN_INPUT_RATE..=MAX_INPUT_RATE).contains(&block.sample_rate) {
                return Err(DecodeError::Unsupported(format!(
                    "sample rate {} Hz is outside {MIN_INPUT_RATE}..={MAX_INPUT_RATE} Hz",
                    block.sample_rate
                )));
            }
            if block.planar.len() != 1 || block.planar[0].len() != block.frames {
                return Err(DecodeError::Decode("unexpected WAVE block layout".into()));
            }
            if block.planar[0].iter().any(|sample| !sample.is_finite()) {
                return Err(DecodeError::Invalid("non-finite input sample".into()));
            }
            match source_rate {
                None => {
                    source_rate = Some(block.sample_rate);
                    if block.sample_rate != OUTPUT_RATE {
                        resampler = Some(StreamingResampler::new(block.sample_rate)?);
                    }
                }
                Some(rate) if rate != block.sample_rate => {
                    return Err(DecodeError::Unsupported("sample rate changed within WAVE".into()));
                }
                Some(_) => {}
            }
            match resampler.as_mut() {
                Some(resampler) => resampler.append(block.planar[0], &mut sink),
                None => sink.append(block.planar[0]),
            }
        })();
        match step {
            Ok(()) => Ok(()),
            Err(error) => {
                callback_failure = Some(error);
                Err(WavError::Io(io::Error::other("audio window processing failed")))
            }
        }
    });
    if let Some(error) = callback_failure {
        return Err(error);
    }
    result.map_err(|error| map_wav_error(error, cancelled))?;
    check_cancelled(cancelled)?;
    if let Some(resampler) = resampler.as_mut() {
        resampler.finish(&mut sink)?;
    }
    sink.flush()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::AtomicBool;

    fn wave_file(rate: u32, channels: u16, samples: &[i16]) -> tempfile::NamedTempFile {
        let mut file = tempfile::NamedTempFile::new().unwrap();
        {
            let spec = hound::WavSpec {
                channels,
                sample_rate: rate,
                bits_per_sample: 16,
                sample_format: hound::SampleFormat::Int,
            };
            let mut writer = hound::WavWriter::new(file.as_file_mut(), spec).unwrap();
            for sample in samples {
                writer.write_sample(*sample).unwrap();
            }
            writer.finalize().unwrap();
        }
        file
    }

    fn sample_value(value: i16) -> f32 {
        f32::from(value) / 32768.0
    }

    #[test]
    fn windows_are_mono_contiguous_and_preserve_the_short_tail() {
        let samples = [-32768, -10000, 0, 10000, 32767];
        let file = wave_file(16_000, 1, &samples);
        let cancelled = AtomicBool::new(false);
        let mut windows = Vec::new();
        for_each_window(file.path(), 2, &cancelled, |start, pcm| {
            windows.push((start, pcm.to_vec()));
            Ok(())
        })
        .unwrap();
        assert_eq!(windows.len(), 3);
        assert_eq!(windows[0], (0, samples[..2].iter().copied().map(sample_value).collect::<Vec<_>>()));
        assert_eq!(windows[1], (2, samples[2..4].iter().copied().map(sample_value).collect::<Vec<_>>()));
        assert_eq!(windows[2], (4, vec![sample_value(samples[4])]));
    }

    #[test]
    fn stereo_channels_are_mixed_before_windowing() {
        let file = wave_file(16_000, 2, &[10000, -10000, 8000, 4000, -32768, 0]);
        let mut output = Vec::new();
        for_each_window(file.path(), 2, &AtomicBool::new(false), |_, pcm| {
            output.extend_from_slice(pcm);
            Ok(())
        })
        .unwrap();
        assert_eq!(output.len(), 3);
        assert!((output[0] - 0.0).abs() < 1e-6);
        assert!((output[1] - sample_value(6000)).abs() < 1e-6);
        assert!((output[2] - sample_value(-16384)).abs() < 1e-6);
    }

    #[test]
    fn streaming_resampling_matches_rubato_full_clip_including_tail() {
        for rate in [8_000, 44_100, 48_000, 192_000] {
            for len in [1_usize, 1024, 1025, 6144] {
                let samples: Vec<i16> = (0..len)
                    .map(|i| (((i * 701) % 20_000) as i32 - 10_000) as i16)
                    .collect();
                let file = wave_file(rate, 1, &samples);
                let input: Vec<f32> = samples.iter().copied().map(sample_value).collect();
                let input_adapter = InterleavedSlice::new(&input, 1, input.len()).unwrap();
                let mut oracle = sinc_resampler(rate).unwrap();
                let expected = oracle.process_all(&input_adapter, input.len(), None).unwrap().take_data();
                let mut actual = Vec::new();
                let mut spans = Vec::new();
                for_each_window(file.path(), 37, &AtomicBool::new(false), |start, pcm| {
                    spans.push((start, pcm.len()));
                    actual.extend_from_slice(pcm);
                    Ok(())
                })
                .unwrap();
                assert_eq!(actual.len(), expected.len(), "rate={rate} len={len}");
                assert_eq!(actual.len() as u64, expected_samples(len as u64, rate).unwrap());
                for (index, (got, want)) in actual.iter().zip(&expected).enumerate() {
                    assert!((got - want).abs() < 2e-6, "rate={rate} len={len} sample={index}");
                }
                let mut next = 0_u64;
                for (start, length) in spans {
                    assert_eq!(start, next);
                    assert!(length > 0 && length <= 37);
                    next += length as u64;
                }
                assert_eq!(next, actual.len() as u64);
            }
        }
    }

    #[test]
    fn rejects_non_wave_and_invalid_sample_rate() {
        let mut not_wave = tempfile::NamedTempFile::new().unwrap();
        use std::io::Write;
        not_wave.write_all(b"not a WAVE file").unwrap();
        let err = for_each_window(not_wave.path(), 5, &AtomicBool::new(false), |_, _| Ok(()))
            .unwrap_err();
        assert!(matches!(err, DecodeError::Unsupported(_)));

        let file = wave_file(4_000, 1, &[1, 2]);
        let err = for_each_window(file.path(), 5, &AtomicBool::new(false), |_, _| Ok(()))
            .unwrap_err();
        assert!(matches!(err, DecodeError::Unsupported(_)));
    }

    #[test]
    fn cancellation_before_start_or_inside_callback_is_reported() {
        let file = wave_file(16_000, 1, &[1, 2, 3, 4]);
        let cancelled = AtomicBool::new(true);
        let mut calls = 0;
        let err = for_each_window(file.path(), 2, &cancelled, |_, _| {
            calls += 1;
            Ok(())
        })
        .unwrap_err();
        assert!(matches!(err, DecodeError::Cancelled));
        assert_eq!(calls, 0);

        cancelled.store(false, Ordering::Release);
        let err = for_each_window(file.path(), 2, &cancelled, |_, _| {
            calls += 1;
            cancelled.store(true, Ordering::Release);
            Ok(())
        })
        .unwrap_err();
        assert!(matches!(err, DecodeError::Cancelled));
        assert_eq!(calls, 1);
    }

    #[test]
    fn propagates_callback_errors_and_rejects_invalid_window_sizes() {
        let file = wave_file(16_000, 1, &[1, 2, 3]);
        let err = for_each_window(file.path(), 2, &AtomicBool::new(false), |_, _| {
            Err("consumer failed".into())
        })
        .unwrap_err();
        assert!(matches!(&err, DecodeError::Callback(msg) if msg == "consumer failed"));
        for size in [0, MAX_WINDOW_SAMPLES + 1] {
            let err = for_each_window(file.path(), size, &AtomicBool::new(false), |_, _| Ok(()))
                .unwrap_err();
            assert!(matches!(err, DecodeError::Invalid(_)));
        }
    }
}
