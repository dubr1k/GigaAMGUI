//! Stream audio through the packaged, hash-verified FFmpeg executable.
//!
//! Only local file and pipe protocols are enabled. Both recognition and
//! diarization must use the resulting 16 kHz PCM timeline. Timestamp gaps are
//! compensated by FFmpeg; source-container timing can be imprecise. Callbacks
//! remain provisional until decoding and child cleanup both succeed.


use std::fmt;
use std::fs::{self, File};
use std::io::{self, Read};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, RecvTimeoutError};
use std::thread;
use std::time::Duration;

use sha2::{Digest, Sha256};

pub const OUTPUT_RATE: u32 = 16_000;
pub const MAX_WINDOW_SAMPLES: usize = OUTPUT_RATE as usize * 60;
const PIPE_BLOCK_BYTES: usize = 16 * 1024;
const PIPE_BLOCKS: usize = 3;
const STDERR_LIMIT: usize = 8 * 1024;
const CANCEL_INTERVAL: Duration = Duration::from_millis(25);

/// The digest must come from a trusted manifest, not from the executable itself.
pub struct FfmpegBinary<'a> {
    pub path: &'a Path,
    pub sha256: [u8; 32],
}

#[derive(Debug)]
pub enum DecodeError {
    Cancelled,
    Invalid(String),
    Io(io::Error),
    Process(String),
    Callback(String),
    Cleanup { cause: String, cleanup: io::Error },
}

impl fmt::Display for DecodeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Cancelled => write!(f, "audio decoding cancelled"),
            Self::Invalid(message) => write!(f, "invalid audio or configuration: {message}"),
            Self::Io(error) => write!(f, "audio I/O: {error}"),
            Self::Process(message) => write!(f, "FFmpeg: {message}"),
            Self::Callback(message) => write!(f, "audio callback: {message}"),
            Self::Cleanup { cause, cleanup } => {
                write!(f, "{cause}; failed to stop or reap FFmpeg: {cleanup}")
            }
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

fn verify_binary(binary: &FfmpegBinary<'_>, cancelled: &AtomicBool) -> Result<(), DecodeError> {
    check_cancelled(cancelled)?;
    if !binary.path.is_absolute() {
        return Err(DecodeError::Invalid("FFmpeg path must be absolute".into()));
    }
    let metadata = fs::symlink_metadata(binary.path).map_err(DecodeError::Io)?;
    if !metadata.file_type().is_file() {
        return Err(DecodeError::Invalid(
            "FFmpeg must be a regular file, not a symlink".into(),
        ));
    }
    let mut file = File::open(binary.path).map_err(DecodeError::Io)?;
    let mut hash = Sha256::new();
    let mut bytes = [0u8; 64 * 1024];
    loop {
        check_cancelled(cancelled)?;
        let count = file.read(&mut bytes).map_err(DecodeError::Io)?;
        if count == 0 {
            break;
        }
        hash.update(&bytes[..count]);
    }
    if hash.finalize()[..] != binary.sha256 {
        return Err(DecodeError::Invalid("FFmpeg SHA-256 mismatch".into()));
    }
    check_cancelled(cancelled)
}

struct PcmSink<'a, F> {
    cancelled: &'a AtomicBool,
    callback: &'a mut F,
    window: Vec<f32>,
    window_samples: usize,
    next_start: u64,
    carry: [u8; 4],
    carry_len: usize,
}

impl<F> PcmSink<'_, F>
where
    F: FnMut(u64, &[f32]) -> Result<(), String>,
{
    fn push_sample(&mut self, bytes: [u8; 4]) -> Result<(), DecodeError> {
        let sample = f32::from_le_bytes(bytes);
        if !sample.is_finite() {
            return Err(DecodeError::Invalid("non-finite decoded PCM".into()));
        }
        self.window.push(sample);
        if self.window.len() == self.window_samples {
            self.flush()?;
        }
        Ok(())
    }

    fn push(&mut self, mut bytes: &[u8]) -> Result<(), DecodeError> {
        check_cancelled(self.cancelled)?;
        if self.carry_len != 0 {
            let take = (4 - self.carry_len).min(bytes.len());
            self.carry[self.carry_len..self.carry_len + take].copy_from_slice(&bytes[..take]);
            self.carry_len += take;
            bytes = &bytes[take..];
            if self.carry_len < 4 {
                return check_cancelled(self.cancelled);
            }
            if self.carry_len == 4 {
                self.push_sample(self.carry)?;
                self.carry_len = 0;
            }
        }
        let mut samples = bytes.chunks_exact(4);
        for sample in &mut samples {
            self.push_sample([sample[0], sample[1], sample[2], sample[3]])?;
        }
        let remainder = samples.remainder();
        self.carry[..remainder.len()].copy_from_slice(remainder);
        self.carry_len = remainder.len();
        check_cancelled(self.cancelled)
    }

    fn flush(&mut self) -> Result<(), DecodeError> {
        check_cancelled(self.cancelled)?;
        if self.window.is_empty() {
            return Ok(());
        }
        let end = self.next_start.checked_add(self.window.len() as u64)
            .ok_or_else(|| DecodeError::Invalid("sample index overflow".into()))?;
        (self.callback)(self.next_start, &self.window).map_err(DecodeError::Callback)?;
        self.next_start = end;
        self.window.clear();
        check_cancelled(self.cancelled)
    }

    fn finish(&mut self) -> Result<(), DecodeError> {
        check_cancelled(self.cancelled)?;
        if self.carry_len != 0 {
            return Err(DecodeError::Invalid("truncated f32 PCM sample".into()));
        }
        self.flush()
    }
}

struct ChildGuard(Child);

impl ChildGuard {
    fn terminate(&mut self) -> io::Result<ExitStatus> {
        if let Some(status) = self.0.try_wait()? {
            return Ok(status);
        }
        if let Err(error) = self.0.kill() {
            // The process may have exited between try_wait and kill.
            return match self.0.try_wait()? {
                Some(status) => Ok(status),
                None => Err(error),
            };
        }
        self.0.wait()
    }
}

impl Drop for ChildGuard {
    fn drop(&mut self) {
        // Best effort only; normal error paths report termination failures.
        if matches!(self.0.try_wait(), Ok(None)) && self.0.kill().is_ok() {
            let _ = self.0.wait();
        }
    }
}

type PipeBlock = Box<[u8; PIPE_BLOCK_BYTES]>;

enum PipeEvent {
    Data(PipeBlock, usize),
    Eof,
    Error(io::Error),
}

fn drain_stderr(mut stderr: impl Read) -> io::Result<Vec<u8>> {
    let mut kept = Vec::new();
    let mut bytes = [0u8; 4096];
    loop {
        match stderr.read(&mut bytes) {
            Ok(0) => return Ok(kept),
            Ok(count) => {
                let take = count.min(STDERR_LIMIT - kept.len());
                kept.extend_from_slice(&bytes[..take]);
            }
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) => return Err(error),
        }
    }
}

fn wait_cancellable(child: &mut Child, cancelled: &AtomicBool) -> Result<ExitStatus, DecodeError> {
    loop {
        check_cancelled(cancelled)?;
        if let Some(status) = child.try_wait().map_err(DecodeError::Io)? {
            return Ok(status);
        }
        thread::sleep(CANCEL_INTERVAL);
    }
}

/// Decode incrementally into consecutive mono 16 kHz windows.
///
/// `binary.sha256` must be the digest of a pinned FFmpeg obtained from a
/// trusted manifest. Its shared libraries and containing directory must also be
/// trusted. See module documentation for the stronger packaging requirements
/// and for the timeline and transactional-callback limitations.
pub fn for_each_window<F>(
    input: impl AsRef<Path>,
    binary: &FfmpegBinary<'_>,
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
    verify_binary(binary, cancelled)?;
    let input = input.as_ref();
    if !fs::metadata(input).map_err(DecodeError::Io)?.is_file() {
        return Err(DecodeError::Invalid("input must be a regular file".into()));
    }
    let absolute_input: PathBuf;
    let input = if input.is_absolute() {
        input
    } else {
        absolute_input = std::env::current_dir().map_err(DecodeError::Io)?.join(input);
        absolute_input.as_path()
    };
    check_cancelled(cancelled)?;

    let mut command = Command::new(binary.path);
    command
        .args(["-nostdin", "-hide_banner", "-nostats", "-loglevel", "error", "-xerror"])
        .args(["-copyts", "-start_at_zero", "-err_detect", "explode"])
        .args(["-protocol_whitelist", "file,pipe", "-i"])
        .arg(input)
        .args(["-map", "0:a:0", "-vn", "-sn", "-dn"])
        .args(["-af", "aresample=16000:async=1:first_pts=0:min_hard_comp=0:min_comp=0"])
        .args(["-ac", "1", "-ar", "16000", "-c:a", "pcm_f32le", "-f", "f32le", "pipe:1"])
        .env_remove("LD_PRELOAD")
        .env_remove("LD_LIBRARY_PATH")
        .env_remove("DYLD_INSERT_LIBRARIES")
        .env_remove("DYLD_LIBRARY_PATH")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    let mut child = ChildGuard(command.spawn().map_err(DecodeError::Io)?);
    let stdout = child.0.stdout.take().ok_or_else(|| DecodeError::Process("missing stdout pipe".into()))?;
    let stderr = child.0.stderr.take().ok_or_else(|| DecodeError::Process("missing stderr pipe".into()))?;

    let stderr_task = thread::Builder::new().name("audio-ffmpeg-stderr".into())
        .spawn(move || drain_stderr(stderr)).map_err(DecodeError::Io)?;
    let (event_tx, event_rx) = mpsc::sync_channel::<PipeEvent>(PIPE_BLOCKS);
    let (free_tx, free_rx) = mpsc::sync_channel::<PipeBlock>(PIPE_BLOCKS);
    for _ in 0..PIPE_BLOCKS {
        // The receiver exists and the channel has exactly this capacity.
        free_tx.send(Box::new([0; PIPE_BLOCK_BYTES]))
            .map_err(|_| DecodeError::Process("pipe buffer pool disconnected".into()))?;
    }
    let stdout_task = match thread::Builder::new().name("audio-ffmpeg-stdout".into()).spawn(move || {
        let mut stdout = stdout;
        while let Ok(mut block) = free_rx.recv() {
            let result = loop {
                match stdout.read(&mut block[..]) {
                    Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                    result => break result,
                }
            };
            let event = match result {
                Ok(0) => PipeEvent::Eof,
                Ok(count) => PipeEvent::Data(block, count),
                Err(error) => PipeEvent::Error(error),
            };
            let finished = matches!(event, PipeEvent::Eof | PipeEvent::Error(_));
            if event_tx.send(event).is_err() || finished {
                break;
            }
        }
    }) {
        Ok(task) => task,
        Err(error) => {
            drop(event_rx);
            drop(free_tx);
            return match child.terminate() {
                Ok(_) => {
                    let _ = stderr_task.join();
                    Err(DecodeError::Io(error))
                }
                Err(cleanup) => Err(DecodeError::Cleanup { cause: error.to_string(), cleanup }),
            };
        }
    };

    let mut sink = PcmSink {
        cancelled,
        callback: &mut on_window,
        window: Vec::with_capacity(window_samples),
        window_samples,
        next_start: 0,
        carry: [0; 4],
        carry_len: 0,
    };
    let stream_result = loop {
        if let Err(error) = check_cancelled(cancelled) {
            break Err(error);
        }
        match event_rx.recv_timeout(CANCEL_INTERVAL) {
            Ok(PipeEvent::Data(block, count)) => {
                let result = sink.push(&block[..count]);
                // It is harmless if the reader already sent EOF and exited.
                let _ = free_tx.send(block);
                if let Err(error) = result {
                    break Err(error);
                }
            }
            Ok(PipeEvent::Eof) => break Ok(()),
            Ok(PipeEvent::Error(error)) => break Err(DecodeError::Io(error)),
            Err(RecvTimeoutError::Timeout) => continue,
            Err(RecvTimeoutError::Disconnected) => {
                break Err(DecodeError::Process("stdout reader stopped before EOF".into()));
            }
        }
    };
    // Unblock a reader waiting to send a block or receive a recycled one.
    drop(event_rx);
    drop(free_tx);

    let mut failure = stream_result.err();
    let status = if failure.is_some() {
        child.terminate()
    } else {
        match wait_cancellable(&mut child.0, cancelled) {
            Ok(status) => Ok(status),
            Err(error) => {
                failure = Some(error);
                child.terminate()
            }
        }
    };
    let status = match status {
        Ok(status) => status,
        Err(cleanup) => {
            // Do not block joining readers while the child could still be alive.
            let cause = failure.map_or_else(|| "audio decoding failed".into(), |e| e.to_string());
            return Err(DecodeError::Cleanup { cause, cleanup });
        }
    };
    let stdout_result = stdout_task.join();
    let stderr_result = stderr_task.join();
    if let Some(error) = failure {
        return Err(error);
    }
    if stdout_result.is_err() {
        return Err(DecodeError::Process("stdout reader panicked".into()));
    }
    let stderr = stderr_result
        .map_err(|_| DecodeError::Process("stderr reader panicked".into()))?
        .map_err(DecodeError::Io)?;
    if !status.success() {
        let diagnostic = String::from_utf8_lossy(&stderr);
        return Err(DecodeError::Process(format!("exit {status}: {diagnostic}")));
    }
    sink.finish()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_sink<'a, F>(cancelled: &'a AtomicBool, callback: &'a mut F, size: usize) -> PcmSink<'a, F>
    where
        F: FnMut(u64, &[f32]) -> Result<(), String>,
    {
        PcmSink {
            cancelled,
            callback,
            window: Vec::with_capacity(size),
            window_samples: size,
            next_start: 0,
            carry: [0; 4],
            carry_len: 0,
        }
    }

    #[test]
    fn arbitrary_pipe_boundaries_preserve_pcm_and_short_tail() {
        let values = [0.0f32, -0.5, 0.25, 1.0, -1.0, 0.125, 0.75];
        let bytes: Vec<u8> = values.iter().flat_map(|value| value.to_le_bytes()).collect();
        for width in 1..=13 {
            let cancelled = AtomicBool::new(false);
            let mut windows = Vec::new();
            let mut callback = |start, samples: &[f32]| {
                windows.push((start, samples.to_vec()));
                Ok(())
            };
            {
                let mut sink = make_sink(&cancelled, &mut callback, 3);
                for fragment in bytes.chunks(width) {
                    sink.push(fragment).unwrap();
                }
                sink.finish().unwrap();
            }
            assert_eq!(windows, vec![
                (0, values[0..3].to_vec()),
                (3, values[3..6].to_vec()),
                (6, values[6..7].to_vec()),
            ]);
        }
    }

    #[test]
    fn incomplete_pcm_and_nonfinite_samples_are_errors() {
        let cancelled = AtomicBool::new(false);
        let mut callback = |_, _: &[f32]| Ok(());
        let mut sink = make_sink(&cancelled, &mut callback, 10);
        sink.push(&[1, 2, 3]).unwrap();
        assert!(matches!(sink.finish(), Err(DecodeError::Invalid(_))));
        let mut sink = make_sink(&cancelled, &mut callback, 10);
        assert!(matches!(sink.push(&f32::NAN.to_le_bytes()), Err(DecodeError::Invalid(_))));
    }

    #[test]
    fn cancellation_during_callback_stops_further_windows() {
        let cancelled = AtomicBool::new(false);
        let mut calls = 0;
        let mut callback = |_, _: &[f32]| {
            calls += 1;
            cancelled.store(true, Ordering::Release);
            Ok(())
        };
        let bytes: Vec<u8> = [0.0f32; 12].iter().flat_map(|value| value.to_le_bytes()).collect();
        {
            let mut sink = make_sink(&cancelled, &mut callback, 3);
            assert!(matches!(sink.push(&bytes), Err(DecodeError::Cancelled)));
        }
        assert_eq!(calls, 1);
    }

    #[test]
    fn mismatched_binary_hash_is_rejected() {
        let file = tempfile::NamedTempFile::new().unwrap();
        let binary = FfmpegBinary { path: file.path(), sha256: [0; 32] };
        assert!(matches!(verify_binary(&binary, &AtomicBool::new(false)), Err(DecodeError::Invalid(_))));
    }

    // These tests intentionally require an explicitly supplied real executable.
    // Example: GIGAAM_TEST_FFMPEG=/absolute/path/to/ffmpeg cargo test -- --ignored
    // The test computes its own hash solely to exercise the experiment; a real
    // caller must use an authenticated build manifest instead.
    fn test_binary() -> (PathBuf, [u8; 32]) {
        let path = PathBuf::from(std::env::var_os("GIGAAM_TEST_FFMPEG").expect("set GIGAAM_TEST_FFMPEG"));
        assert!(path.is_absolute());
        let mut file = File::open(&path).unwrap();
        let mut hash = Sha256::new();
        let mut block = [0u8; 65536];
        loop {
            let n = file.read(&mut block).unwrap();
            if n == 0 { break; }
            hash.update(&block[..n]);
        }
        (path, hash.finalize().into())
    }

    fn write_fixture(path: &Path, rate: u32, frames: usize, constant: bool) {
        let spec = hound::WavSpec { channels: 1, sample_rate: rate, bits_per_sample: 16, sample_format: hound::SampleFormat::Int };
        let mut writer = hound::WavWriter::create(path, spec).unwrap();
        for index in 0..frames {
            let value: i16 = if constant || index == frames / 10 || index == frames - rate as usize / 100 {
                16384
            } else {
                0
            };
            writer.write_sample(value).unwrap();
        }
        writer.finalize().unwrap();
    }

    fn decode_fixture(path: &Path, binary: &FfmpegBinary<'_>) -> Vec<f32> {
        let mut result = Vec::new();
        for_each_window(path, binary, 503, &AtomicBool::new(false), |start, samples| {
            assert_eq!(start as usize, result.len());
            result.extend_from_slice(samples);
            Ok(())
        }).unwrap();
        result
    }

    #[test]
    #[ignore = "requires GIGAAM_TEST_FFMPEG with AAC, MP3, Vorbis, Opus and WMA encoders"]
    fn real_advertised_container_and_codec_matrix() {
        let (path, sha256) = test_binary();
        let binary = FfmpegBinary { path: &path, sha256 };
        let dir = tempfile::tempdir().unwrap();
        let wav = dir.path().join("tone.wav");
        let spec = hound::WavSpec {
            channels: 1,
            sample_rate: 44_100,
            bits_per_sample: 16,
            sample_format: hound::SampleFormat::Int,
        };
        let mut writer = hound::WavWriter::create(&wav, spec).unwrap();
        for index in 0..44_100 {
            let phase = index as f32 * 2.0 * std::f32::consts::PI * 440.0 / 44_100.0;
            writer.write_sample((phase.sin() * 12_000.0) as i16).unwrap();
        }
        writer.finalize().unwrap();
        // Encoders are required only for generating test fixtures; production
        // needs corresponding demuxers/decoders, not these encoding libraries.
        let cases = [
            ("clip.mp3", "libmp3lame", "mp3"),
            ("clip.m4a", "aac", "ipod"),
            ("clip.aac", "aac", "adts"),
            ("clip.mp4", "aac", "mp4"),
            ("clip.mov", "aac", "mov"),
            ("clip.qta", "aac", "mov"),
            ("clip.3gp", "aac", "3gp"),
            ("clip.avi", "pcm_s16le", "avi"),
            ("clip.mkv", "pcm_s16le", "matroska"),
            ("clip.webm", "libopus", "webm"),
            ("clip.ogg", "libvorbis", "ogg"),
            ("clip.opus", "libopus", "opus"),
            ("clip.wma", "wmav2", "asf"),
        ];
        for (name, codec, muxer) in cases {
            let encoded = dir.path().join(name);
            let output = Command::new(&path)
                .args(["-nostdin", "-hide_banner", "-loglevel", "error", "-i"])
                .arg(&wav)
                .args(["-map", "0:a:0", "-c:a", codec, "-f", muxer])
                .arg(&encoded)
                .output()
                .unwrap();
            assert!(output.status.success(), "{name}: {}", String::from_utf8_lossy(&output.stderr));
            let samples = decode_fixture(&encoded, &binary);
            // Container/encoder priming differs. This is a smoke bound, not
            // proof of sample-exact trim or timestamp alignment.
            assert!((13_000..=19_000).contains(&samples.len()), "{name}: {}", samples.len());
            assert!(samples[3_000..13_000].iter().any(|sample| sample.abs() > 0.05), "{name}");
        }
    }

    #[test]
    #[ignore = "requires GIGAAM_TEST_FFMPEG and a build with FLAC encoding"]
    fn real_wav_and_flac_packetization_preserve_resampled_timeline_and_tail() {
        let (path, sha256) = test_binary();
        let binary = FfmpegBinary { path: &path, sha256 };
        let dir = tempfile::tempdir().unwrap();
        let wav = dir.path().join("source.wav");
        let flac = dir.path().join("source.flac");
        write_fixture(&wav, 44_100, 44_100, false);
        let status = Command::new(&path).args(["-nostdin", "-loglevel", "error", "-i"])
            .arg(&wav).args(["-c:a", "flac"]).arg(&flac).status().unwrap();
        assert!(status.success());
        let from_wav = decode_fixture(&wav, &binary);
        let from_flac = decode_fixture(&flac, &binary);
        assert_eq!(from_wav.len(), 16_000);
        assert_eq!(from_flac.len(), from_wav.len());
        assert!(from_wav.iter().zip(&from_flac).all(|(a, b)| (a - b).abs() < 0.000_01));
        for center in [1_600usize, 15_840] {
            assert!(from_wav[center - 4..=center + 4].iter().any(|x| x.abs() > 0.05));
        }
    }

    #[test]
    #[ignore = "requires GIGAAM_TEST_FFMPEG and the Matroska/PCM encoder and asetpts filter"]
    fn real_timestamp_gap_is_represented_on_output_timeline() {
        let (path, sha256) = test_binary();
        let binary = FfmpegBinary { path: &path, sha256 };
        let dir = tempfile::tempdir().unwrap();
        let wav = dir.path().join("source.wav");
        let mkv = dir.path().join("gap.mkv");
        write_fixture(&wav, 16_000, 16_000, true);
        let status = Command::new(&path).args(["-nostdin", "-loglevel", "error", "-i"])
            .arg(&wav)
            .args(["-af", "asetpts=PTS+if(gte(T\\,0.5)\\,0.25/TB\\,0)", "-c:a", "pcm_s16le"])
            .arg(&mkv).status().unwrap();
        assert!(status.success());
        let samples = decode_fixture(&mkv, &binary);
        assert!((19_900..=20_100).contains(&samples.len()), "{}", samples.len());
        let longest_silence = samples.split(|x| x.abs() > 0.01).map(|s| s.len()).max().unwrap_or(0);
        assert!((3_900..=4_100).contains(&longest_silence), "{longest_silence}");
    }

    #[test]
    #[ignore = "requires GIGAAM_TEST_FFMPEG"]
    fn cancelling_during_real_decode_returns_cancelled() {
        let (path, sha256) = test_binary();
        let binary = FfmpegBinary { path: &path, sha256 };
        let dir = tempfile::tempdir().unwrap();
        let wav = dir.path().join("source.wav");
        write_fixture(&wav, 16_000, 32_000, true);
        let cancelled = AtomicBool::new(false);
        let mut calls = 0;
        let result = for_each_window(&wav, &binary, 256, &cancelled, |_, _| {
            calls += 1;
            cancelled.store(true, Ordering::Release);
            Ok(())
        });
        assert!(matches!(result, Err(DecodeError::Cancelled)));
        assert_eq!(calls, 1);
    }
}
