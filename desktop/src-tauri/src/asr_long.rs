//! Experimental long-form diarization for Windows and Linux.
//!
//! A single FFmpeg decode is stored as private 16 kHz float WAVE. Recognition
//! and diarization both read that exact stream; errors never publish a partial
//! transcript. Speaker IDs are recording-local statistical estimates.

#[path = "asr_long_audio.rs"]
mod audio;
#[path = "asr_long_embedder.rs"]
mod embedder;
#[path = "asr_long_ffmpeg.rs"]
mod ffmpeg;
#[path = "asr_long_identity.rs"]
mod identity;
#[path = "asr_long_models.rs"]
mod models;
#[path = "asr_long_pipeline.rs"]
mod pipeline;
#[path = "asr_long_private_temp.rs"]
mod private_temp;

use std::fmt::Write as _;
use std::fs::{self, OpenOptions};
use std::path::{Path, PathBuf};
use std::sync::atomic::AtomicBool;
use std::sync::Arc;

use gigastt_core::export::{self, ExportFormat, Segment};
use gigastt_core::inference::{Engine, TranscribeRequest, TranscribeResult, TranscribeSource, WordInfo};
use gigastt_core::model;
use ryf::{WavWriter, WriteSpec};
use serde::Serialize;
use tauri::{AppHandle, Manager};

use self::identity::TrackerConfig;
use self::pipeline::{Pipeline, PipelineConfig, Region, RegionKind};
use self::private_temp::PrivateTempDir;

const RATE: u64 = 16_000;
const SHORT_LIMIT_SAMPLES: u64 = 1_800 * RATE;
const MANIFEST: &str = include_str!(concat!(env!("OUT_DIR"), "/gigaam_ffmpeg_sha256sums"));

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum SpeakerStatus {
    Known(u32),
    Unknown,
    Overlap,
}

impl SpeakerStatus {
    fn label(self) -> &'static str {
        match self {
            Self::Known(_) => "known",
            Self::Unknown => "unknown",
            Self::Overlap => "overlap",
        }
    }

    fn push_prefix(self, output: &mut String) {
        match self {
            Self::Known(id) => write!(output, "[SPEAKER_{id}]").expect("writing to a string cannot fail"),
            Self::Unknown => output.push_str("[SPEAKER_UNKNOWN]"),
            Self::Overlap => output.push_str("[SPEAKER_OVERLAP]"),
        }
    }
}

#[derive(Serialize)]
pub(super) struct TaggedSegment {
    #[serde(flatten)]
    segment: Segment,
    #[serde(serialize_with = "serialize_speaker_status")]
    speaker_status: SpeakerStatus,
}

fn serialize_speaker_status<S: serde::Serializer>(status: &SpeakerStatus, serializer: S) -> Result<S::Ok, S::Error> {
    serializer.serialize_str(status.label())
}

pub(super) struct LongResult {
    pub(super) transcription: TranscribeResult,
    pub(super) segments: Vec<TaggedSegment>,
    pub(super) transcript: String,
    statuses: Vec<SpeakerStatus>,
}

fn manifest_digest(manifest: &str, executable: &str) -> Result<[u8; 32], String> {
    let mut lines = manifest.lines();
    let line = lines.next().ok_or_else(|| "Для длинной диаризации не упакован проверенный FFmpeg".to_string())?;
    if lines.next().is_some() {
        return Err("Некорректный встроенный манифест FFmpeg".into());
    }
    let (hex, name) = line.split_once("  ").ok_or_else(|| "Некорректный встроенный манифест FFmpeg".to_string())?;
    if name != executable || hex.len() != 64 || !hex.bytes().all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)) {
        return Err("Некорректный встроенный манифест FFmpeg".into());
    }
    let mut digest = [0u8; 32];
    for (index, byte) in digest.iter_mut().enumerate() {
        *byte = u8::from_str_radix(&hex[index * 2..index * 2 + 2], 16)
            .map_err(|_| "Некорректная контрольная сумма FFmpeg".to_string())?;
    }
    Ok(digest)
}

struct CanonicalAudio {
    directory: PrivateTempDir,
    path: PathBuf,
    samples: u64,
}

fn remove_private(directory: PrivateTempDir, cause: String) -> String {
    let path = directory.path().to_path_buf();
    match directory.close() {
        Ok(()) => cause,
        Err(error) => format!("{cause}; не удалось удалить временный каталог {}: {error}", path.display()),
    }
}

impl CanonicalAudio {
    fn create(app: &AppHandle, input: &Path, cancelled: &AtomicBool) -> Result<Self, String> {
        super::check_cancelled(cancelled)?;
        let executable = if cfg!(windows) { "ffmpeg.exe" } else { "ffmpeg" };
        let digest = manifest_digest(MANIFEST, executable)?;
        let binary_path = app.path().resource_dir().map_err(|error| format!("Не найден каталог ресурсов: {error}"))?
            .join("ffmpeg").join(executable);
        let binary = ffmpeg::FfmpegBinary { path: &binary_path, sha256: digest };
        let parent = app.path().app_cache_dir().map_err(|error| format!("Не найден каталог временных данных: {error}"))?;
        fs::create_dir_all(&parent).map_err(|error| format!("Не удалось создать каталог временных данных: {error}"))?;
        let directory = PrivateTempDir::new_in(parent)
            .map_err(|error| format!("Не удалось создать защищённый временный каталог: {error}"))?;
        let path = directory.path().join("canonical.wav");
        let result = (|| -> Result<u64, String> {
            let mut options = OpenOptions::new();
            options.read(true).write(true).create_new(true);
            #[cfg(unix)]
            {
                use std::os::unix::fs::OpenOptionsExt;
                options.mode(0o600);
            }
            let file = options.open(&path).map_err(|error| format!("Не удалось создать временное аудио: {error}"))?;
            // Starting with RF64 avoids a non-cancellable whole-file RIFF promotion.
            let mut writer = WavWriter::new_rf64(file, WriteSpec::f32(RATE as u32, 1))
                .map_err(|error| format!("Не удалось создать WAVE: {error}"))?;
            let mut next_sample = 0u64;
            let mut write_error = None;
            let decoded = ffmpeg::for_each_window(input, &binary, 4096, cancelled, |start, samples| {
                let step = (|| -> Result<(), String> {
                    super::check_cancelled(cancelled)?;
                    if start != next_sample {
                        return Err("Непоследовательная временная шкала аудио".into());
                    }
                    let end = start.checked_add(samples.len() as u64).ok_or_else(|| "Слишком длинное аудио".to_string())?;
                    writer.write_f32_samples(samples).map_err(|error| format!("Не удалось записать временное аудио: {error}"))?;
                    next_sample = end;
                    super::check_cancelled(cancelled)
                })();
                if let Err(error) = step {
                    write_error = Some(error);
                    Err("canonical audio write failed".into())
                } else {
                    Ok(())
                }
            });
            if let Some(error) = write_error {
                return Err(error);
            }
            decoded.map_err(|error| super::check_cancelled(cancelled).err()
                .unwrap_or_else(|| format!("Не удалось декодировать аудио: {error}")))?;
            super::check_cancelled(cancelled)?;
            if next_sample == 0 {
                return Err("В файле нет декодированного аудио".into());
            }
            writer.finalize().map_err(|error| format!("Не удалось завершить временное аудио: {error}"))?;
            super::check_cancelled(cancelled)?;
            Ok(next_sample)
        })();
        match result {
            Ok(samples) => Ok(Self { directory, path, samples }),
            Err(error) => Err(remove_private(directory, error)),
        }
    }

    fn close(self) -> Result<(), String> {
        let path = self.directory.path().to_path_buf();
        self.directory.close().map_err(|error| format!("Не удалось удалить временный каталог {}: {error}", path.display()))
    }
}

/// Decode first so a missing or inaccurate container duration never truncates a
/// long input. A genuinely short input returns None for the original path.
pub(super) fn try_transcribe(
    app: &AppHandle,
    job_id: &str,
    input: &str,
    model_dir: &str,
    engine: &Engine,
    cancelled: &Arc<AtomicBool>,
) -> Result<Option<LongResult>, String> {
    super::emit_progress(app, job_id, "preparing", None, Some(input), Some("Подготовка длинной записи"));
    let canonical = CanonicalAudio::create(app, Path::new(input), cancelled)?;
    let result = if canonical.samples <= SHORT_LIMIT_SAMPLES {
        Ok(None)
    } else {
        process(app, job_id, input, model_dir, engine, cancelled, &canonical).map(Some)
    };
    let cleanup = canonical.close();
    match (result, cleanup) {
        (Ok(value), Ok(())) => Ok(value),
        (Err(error), Ok(())) => Err(error),
        (Ok(_), Err(error)) => Err(error),
        (Err(error), Err(cleanup)) => Err(format!("{error}; {cleanup}")),
    }
}

fn process(
    app: &AppHandle,
    job_id: &str,
    input: &str,
    model_dir: &str,
    engine: &Engine,
    cancelled: &Arc<AtomicBool>,
    canonical: &CanonicalAudio,
) -> Result<LongResult, String> {
    super::check_cancelled(cancelled)?;
    super::emit_progress(app, job_id, "downloading_speaker_model", None, Some(input), Some("Подготовка модели сегментации"));
    let segmentation = tauri::async_runtime::block_on(models::ensure_segmentation_model(Path::new(model_dir), cancelled))?;
    super::check_cancelled(cancelled)?;
    let path = canonical.path.to_str().ok_or_else(|| "Путь к временному аудио не поддерживается".to_string())?;
    super::emit_progress(app, job_id, "transcribing", None, Some(input), Some("Распознавание длинной записи"));
    let mut session = engine.pool.checkout_blocking().map_err(|error| format!("Не удалось занять сессию модели: {error}"))?;
    let transcription = engine.transcribe_request(
        TranscribeRequest::new(TranscribeSource::Path(path))
            .with_abort(Some(cancelled.clone()))
            .with_diarization(false),
        &mut session,
    );
    drop(session);
    let mut transcription = transcription.map_err(|error| super::check_cancelled(cancelled).err()
        .unwrap_or_else(|| format!("Не удалось распознать {input}: {error}")))?;
    super::check_cancelled(cancelled)?;
    let expected_duration = canonical.samples as f64 / RATE as f64;
    if !transcription.duration_s.is_finite() || (transcription.duration_s - expected_duration).abs() > 1.0 / RATE as f64 {
        return Err("Временная шкала распознавания не совпадает с декодированным аудио".into());
    }
    super::emit_progress(app, job_id, "diarizing", None, Some(input), Some("Определение говорящих в длинной записи"));
    let speaker_model = Path::new(model_dir).join(model::SPEAKER_MODEL_FILE);
    let pipeline = Pipeline::load(&segmentation, &speaker_model, cancelled)
        .map_err(|error| super::check_cancelled(cancelled).err()
            .unwrap_or_else(|| format!("Не удалось загрузить модели диаризации: {error}")))?;
    let config = PipelineConfig {
        tracker: TrackerConfig {
            max_speakers: 256,
            embedding_dim: 256,
            match_threshold: 0.50,
            new_speaker_threshold: 0.25,
            ambiguity_margin: 0.05,
        },
        segment_confidence: 0.55,
        vad_threshold: 0.5,
        min_vad_fraction: 0.2,
        min_speech_samples: 8_000,
    };
    let mut alignment = Alignment::new(&transcription.words, canonical.samples)?;
    let mut last_end = 0u64;
    pipeline.diarize(&canonical.path, config, cancelled, |region| {
        if region.start_sample < last_end || region.end_sample <= region.start_sample || region.end_sample > canonical.samples {
            return Err("Некорректная временная шкала диаризации".into());
        }
        last_end = region.end_sample;
        alignment.add(region);
        Ok(())
    }).map_err(|error| super::check_cancelled(cancelled).err()
        .unwrap_or_else(|| format!("Не удалось выполнить диаризацию {input}: {error}")))?;
    super::check_cancelled(cancelled)?;
    let statuses = alignment.finish();
    for (word, status) in transcription.words.iter_mut().zip(&statuses) {
        word.speaker = match status { SpeakerStatus::Known(id) => Some(*id), _ => None };
    }
    let segments = tagged_segments(&transcription.words, &statuses);
    let mut transcript = String::new();
    for segment in &segments {
        if !transcript.is_empty() { transcript.push_str("\n\n"); }
        segment.speaker_status.push_prefix(&mut transcript);
        transcript.push(' ');
        transcript.push_str(&segment.segment.text);
    }
    Ok(LongResult { transcription, segments, transcript, statuses })
}

#[derive(Clone, Copy, Default)]
struct Evidence {
    covered: u64,
    speaker: Option<u32>,
    unknown: bool,
    overlap: bool,
}

struct Alignment {
    intervals: Vec<Option<(u64, u64)>>,
    evidence: Vec<Evidence>,
    first: usize,
}

impl Alignment {
    fn new(words: &[WordInfo], samples: u64) -> Result<Self, String> {
        let mut previous_start = None;
        let mut intervals = Vec::with_capacity(words.len());
        for word in words {
            if !word.start.is_finite() || !word.end.is_finite() || word.start < 0.0 || word.end < word.start {
                intervals.push(None);
                continue;
            }
            let start = (word.start * RATE as f64).floor() as u64;
            let mut end = (word.end * RATE as f64).ceil() as u64;
            if start >= samples || end > samples {
                intervals.push(None);
                continue;
            }
            if end == start { end = (start + 1).min(samples); }
            if previous_start.is_some_and(|previous| start < previous) {
                return Err("Слова распознавания выданы не в хронологическом порядке".into());
            }
            previous_start = Some(start);
            intervals.push(Some((start, end)));
        }
        Ok(Self { intervals, evidence: vec![Evidence::default(); words.len()], first: 0 })
    }

    fn add(&mut self, region: Region) {
        // Starts were validated as monotonic, although word durations may overlap.
        while self.first < self.intervals.len()
            && self.intervals[self.first].is_none_or(|(_, end)| end <= region.start_sample) {
            self.first += 1;
        }
        for index in self.first..self.intervals.len() {
            let Some((start, end)) = self.intervals[index] else { continue };
            if start >= region.end_sample { break; }
            let left = start.max(region.start_sample);
            let right = end.min(region.end_sample);
            if right <= left { continue; }
            let evidence = &mut self.evidence[index];
            evidence.covered = evidence.covered.saturating_add(right - left);
            match region.kind {
                RegionKind::Overlap => evidence.overlap = true,
                RegionKind::Unknown => evidence.unknown = true,
                RegionKind::Speaker(id) => match evidence.speaker {
                    Some(other) if other != id => evidence.unknown = true,
                    None => evidence.speaker = Some(id),
                    _ => {}
                },
            }
        }
    }

    fn finish(self) -> Vec<SpeakerStatus> {
        self.intervals.into_iter().zip(self.evidence).map(|(interval, evidence)| {
            if evidence.overlap { return SpeakerStatus::Overlap; }
            match (interval, evidence.speaker) {
                (Some((start, end)), Some(id)) if !evidence.unknown && evidence.covered == end - start => SpeakerStatus::Known(id),
                _ => SpeakerStatus::Unknown,
            }
        }).collect()
    }
}

fn each_status_run(statuses: &[SpeakerStatus], mut f: impl FnMut(usize, usize, SpeakerStatus)) {
    let mut start = 0;
    while start < statuses.len() {
        let status = statuses[start];
        let mut end = start + 1;
        while end < statuses.len() && statuses[end] == status { end += 1; }
        f(start, end, status);
        start = end;
    }
}

fn tagged_segments(words: &[WordInfo], statuses: &[SpeakerStatus]) -> Vec<TaggedSegment> {
    let mut result = Vec::new();
    each_status_run(statuses, |start, end, status| {
        result.extend(export::to_transcript_segments(&words[start..end]).into_iter()
            .map(|segment| TaggedSegment { segment, speaker_status: status }));
    });
    result
}

fn push_subtitle_time(output: &mut String, seconds: f64, separator: char) {
    let millis = if seconds.is_finite() && seconds > 0.0 { (seconds * 1000.0).round() as u64 } else { 0 };
    write!(output, "{:02}:{:02}:{:02}{}{:03}", millis / 3_600_000, millis / 60_000 % 60, millis / 1000 % 60, separator, millis % 1000)
        .expect("writing to a string cannot fail");
}

fn subtitles(words: &[WordInfo], statuses: &[SpeakerStatus], vtt: bool) -> String {
    let mut output = if vtt { "WEBVTT\n\n".to_owned() } else { String::new() };
    let mut count = 0usize;
    each_status_run(statuses, |start, end, status| {
        for cue in export::to_segments(&words[start..end], 80, 14) {
            count += 1;
            if !vtt { writeln!(output, "{count}").expect("writing to a string cannot fail"); }
            let separator = if vtt { '.' } else { ',' };
            push_subtitle_time(&mut output, cue.start, separator);
            output.push_str(" --> ");
            push_subtitle_time(&mut output, cue.end, separator);
            output.push('\n');
            if !matches!(status, SpeakerStatus::Known(_)) {
                status.push_prefix(&mut output);
                output.push(' ');
            }
            output.push_str(&cue.text);
            output.push_str("\n\n");
        }
    });
    output
}

pub(super) fn render(format: ExportFormat, result: &LongResult) -> Result<String, String> {
    match format {
        ExportFormat::Txt => Ok(result.transcript.clone()),
        ExportFormat::Md => Ok(format!("# Transcript\n\n{}\n", result.transcript)),
        ExportFormat::Srt => Ok(subtitles(&result.transcription.words, &result.statuses, false)),
        ExportFormat::Vtt => Ok(subtitles(&result.transcription.words, &result.statuses, true)),
        ExportFormat::Json => {
            let mut words = serde_json::to_value(&result.transcription.words).map_err(|error| error.to_string())?;
            let entries = words.as_array_mut().ok_or_else(|| "Не удалось сериализовать слова".to_string())?;
            for (entry, status) in entries.iter_mut().zip(&result.statuses) {
                entry.as_object_mut().ok_or_else(|| "Не удалось сериализовать слово".to_string())?
                    .insert("speaker_status".into(), serde_json::Value::String(status.label().into()));
            }
            Ok(serde_json::json!({ "text": result.transcription.text, "words": words, "duration": result.transcription.duration_s }).to_string())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn manifest_requires_exactly_the_packaged_binary() {
        let hash = "a".repeat(64);
        assert_eq!(manifest_digest(&format!("{hash}  ffmpeg\n"), "ffmpeg").unwrap(), [0xaa; 32]);
        assert!(manifest_digest("", "ffmpeg").is_err());
        assert!(manifest_digest(&format!("{hash}  ffmpeg.exe\n"), "ffmpeg").is_err());
        assert!(manifest_digest(&format!("{hash}  ffmpeg\n{hash}  ffmpeg\n"), "ffmpeg").is_err());
    }

    #[test]
    fn overlap_and_uncovered_words_are_not_forced_to_a_speaker() {
        let words = vec![
            WordInfo::new("known", 0.0, 1.0, 0.9, None),
            WordInfo::new("overlap", 1.0, 2.0, 0.9, None),
            WordInfo::new("gap", 2.0, 3.0, 0.9, None),
        ];
        let mut alignment = Alignment::new(&words, 3 * RATE).unwrap();
        alignment.add(Region { start_sample: 0, end_sample: RATE, kind: RegionKind::Speaker(4) });
        alignment.add(Region { start_sample: RATE, end_sample: 2 * RATE, kind: RegionKind::Overlap });
        alignment.add(Region { start_sample: 2 * RATE, end_sample: 3 * RATE - 1, kind: RegionKind::Speaker(4) });
        assert_eq!(alignment.finish(), vec![SpeakerStatus::Known(4), SpeakerStatus::Overlap, SpeakerStatus::Unknown]);
    }

    #[test]
    fn captions_distinguish_unknown_and_overlap() {
        let words = vec![WordInfo::new("a", 0.0, 0.4, 0.9, None), WordInfo::new("b", 0.5, 0.9, 0.9, None)];
        let statuses = [SpeakerStatus::Unknown, SpeakerStatus::Overlap];
        for text in [subtitles(&words, &statuses, false), subtitles(&words, &statuses, true)] {
            assert!(text.contains("[SPEAKER_UNKNOWN] a"));
            assert!(text.contains("[SPEAKER_OVERLAP] b"));
        }
    }

    #[test]
    fn absolute_word_timing_and_status_survive_after_thirty_minutes() {
        let mut words = vec![
            WordInfo::new("one", 1801.0, 1802.0, 0.9, None),
            WordInfo::new("two", 1802.0, 1803.0, 0.9, None),
        ];
        let mut alignment = Alignment::new(&words, 1804 * RATE).unwrap();
        alignment.add(Region { start_sample: 1801 * RATE, end_sample: 1802 * RATE, kind: RegionKind::Speaker(7) });
        alignment.add(Region { start_sample: 1802 * RATE, end_sample: 1803 * RATE, kind: RegionKind::Overlap });
        let statuses = alignment.finish();
        assert_eq!(statuses, vec![SpeakerStatus::Known(7), SpeakerStatus::Overlap]);
        words[0].speaker = Some(7);
        let segments = tagged_segments(&words, &statuses);
        let serialized = serde_json::to_value(&segments).unwrap();
        assert_eq!(serialized[0]["start"], 1801.0);
        assert_eq!(serialized[0]["speaker"], 7);
        assert_eq!(serialized[0]["speaker_status"], "known");
        assert_eq!(serialized[1]["speaker_status"], "overlap");
        assert!(serialized[1].get("speaker").is_none());
        let result = LongResult {
            transcription: TranscribeResult { text: "one two".into(), words, duration_s: 1804.0, confidence: None },
            segments,
            transcript: "[SPEAKER_7] one\n\n[SPEAKER_OVERLAP] two".into(),
            statuses,
        };
        let json: serde_json::Value = serde_json::from_str(&render(ExportFormat::Json, &result).unwrap()).unwrap();
        assert_eq!(json["words"][0]["speaker_status"], "known");
        assert_eq!(json["words"][1]["speaker_status"], "overlap");
        assert!(render(ExportFormat::Srt, &result).unwrap().contains("00:30:02,000 --> 00:30:03,000"));
    }

    #[test]
    fn out_of_order_word_times_fail_instead_of_mislabeling() {
        let words = vec![
            WordInfo::new("later", 2.0, 3.0, 0.9, None),
            WordInfo::new("earlier", 1.0, 1.5, 0.9, None),
        ];
        assert!(Alignment::new(&words, 4 * RATE).is_err());
    }
}
