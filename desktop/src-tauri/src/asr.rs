use std::ffi::OsString;
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::thread::{self, JoinHandle};
use std::time::Duration;

use gigastt_core::error::GigasttError;
use gigastt_core::export::{self, ExportFormat, RenderOpts, Segment};
use gigastt_core::inference::{audio, DiarizationOutcome, Engine, TranscribeRequest, TranscribeResult, TranscribeSource, WordInfo};
use gigastt_core::model::{self, ModelVariant};
use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Manager, State};

#[cfg(any(target_os = "linux", target_os = "windows"))]
#[path = "asr_long.rs"]
mod long_diarization;

const MODELS: [(&str, &str, ModelVariant); 3] = [
    ("v3_e2e_rnnt", "GigaAM v3 e2e RNNT", ModelVariant::E2eRnnt),
    ("multilingual_ctc", "GigaAM Multilingual CTC (220M)", ModelVariant::MlCtc),
    ("multilingual_large_ctc", "GigaAM Multilingual Large CTC (600M)", ModelVariant::MlCtcLarge),
];

#[derive(Default)]
pub struct AsrState {
    active: Arc<Mutex<Option<ActiveJob>>>,
    engine: Arc<Mutex<Option<CachedEngine>>>,
}

struct ActiveJob {
    id: String,
    cancelled: Arc<AtomicBool>,
}

struct CachedEngine {
    variant: ModelVariant,
    engine: Arc<Engine>,
}

struct JobGuard {
    active: Arc<Mutex<Option<ActiveJob>>>,
    cancelled: Arc<AtomicBool>,
}

pub(crate) struct LiveAsrJob {
    guard: JobGuard,
    job_id: String,
    cache: Arc<Mutex<Option<CachedEngine>>>,
}

impl LiveAsrJob {
    pub(crate) fn cancelled(&self) -> Arc<AtomicBool> {
        self.guard.cancelled.clone()
    }

    pub(crate) async fn prepare(&self, app: &AppHandle, model_name: &str) -> Result<(), String> {
        let (model_id, variant) = find_model(model_name)?;
        let model_dir = models_dir(app)?.join(model_id);
        let model_dir = model_dir.to_str().ok_or_else(|| "Путь к каталогу модели не поддерживается".to_string())?;
        check_cancelled(&self.guard.cancelled)?;
        let download = model::ensure_model_variant(Some(variant), model_dir);
        tokio::pin!(download);
        loop {
            tokio::select! {
                result = &mut download => {
                    result.map_err(|err| format!("Не удалось подготовить модель: {err:#}"))?;
                    return check_cancelled(&self.guard.cancelled);
                }
                _ = tokio::time::sleep(Duration::from_millis(250)) => {
                    check_cancelled(&self.guard.cancelled)?;
                }
            }
        }
    }

    pub(crate) fn engine(&self, app: &AppHandle, model: &str) -> Result<Arc<Engine>, String> {
        check_cancelled(&self.guard.cancelled)?;
        let (model_id, variant) = find_model(model)?;
        let model_dir = models_dir(app)?.join(model_id);
        if !model::is_usable_present(variant, &model_dir) {
            return Err("Модель для Live не установлена; сначала подготовьте её в пакетном режиме".into());
        }
        let has_speaker_file = model_dir.join(model::SPEAKER_MODEL_FILE).is_file();
        let model_dir = model_dir.to_str().ok_or_else(|| "Путь к каталогу модели не поддерживается".to_string())?;
        let engine = get_engine(app, &self.job_id, variant, has_speaker_file, model_dir, &self.cache, &self.guard.cancelled)?;
        check_cancelled(&self.guard.cancelled)?;
        Ok(engine)
    }
}

impl Drop for JobGuard {
    fn drop(&mut self) {
        let mut active = self.active.lock().unwrap_or_else(|err| err.into_inner());
        if active.as_ref().is_some_and(|job| Arc::ptr_eq(&job.cancelled, &self.cancelled)) {
            *active = None;
        }
    }
}

#[derive(Serialize)]
pub struct ModelInfo {
    id: &'static str,
    label: &'static str,
    installed: bool,
}

#[derive(Deserialize)]
pub struct JobRequest {
    job_id: String,
    inputs: Vec<String>,
    model: String,
    output_dir: String,
    formats: Vec<String>,
    #[serde(default)]
    diarization: bool,
    #[serde(default)]
    diarization_model: Option<String>,
}

struct ValidatedRequest {
    job_id: String,
    inputs: Vec<String>,
    variant: ModelVariant,
    model_id: &'static str,
    output_dir: PathBuf,
    formats: Vec<ExportFormat>,
    diarization: bool,
}

#[derive(Serialize)]
pub struct JobResult {
    files: Vec<FileResult>,
}

#[derive(Serialize)]
struct FileResult {
    input_path: String,
    transcript: String,
    outputs: Vec<OutputFile>,
    #[serde(skip_serializing_if = "Option::is_none")]
    segments: Option<ResultSegments>,
}

#[derive(Serialize)]
#[serde(untagged)]
enum ResultSegments {
    Regular(Vec<Segment>),
    #[cfg(any(target_os = "linux", target_os = "windows"))]
    Long(Vec<long_diarization::TaggedSegment>),
}

enum ProcessedFile {
    Regular {
        transcription: TranscribeResult,
        segments: Option<Vec<Segment>>,
        diarized_text: Option<String>,
    },
    #[cfg(any(target_os = "linux", target_os = "windows"))]
    Long(long_diarization::LongResult),
}

#[derive(Serialize)]
struct OutputFile {
    format: String,
    path: String,
}

#[derive(Clone, Serialize)]
struct Progress {
    job_id: String,
    stage: &'static str,
    percent: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    input_path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    message: Option<String>,
}

fn emit_progress(app: &AppHandle, job_id: &str, stage: &'static str, percent: Option<f64>, input: Option<&str>, message: Option<&str>) {
    let _ = app.emit("transcription-progress", Progress {
        job_id: job_id.to_owned(),
        stage,
        percent,
        input_path: input.map(str::to_owned),
        message: message.map(str::to_owned),
    });
}

fn models_dir(app: &AppHandle) -> Result<PathBuf, String> {
    app.path().app_data_dir().map(|path| path.join("models"))
        .map_err(|err| format!("Не удалось определить каталог моделей: {err}"))
}

#[tauri::command]
pub fn list_models(app: AppHandle) -> Result<Vec<ModelInfo>, String> {
    let dir = models_dir(&app)?;
    Ok(MODELS.iter().map(|(id, label, variant)| ModelInfo {
        id: *id,
        label: *label,
        installed: model::is_usable_present(*variant, &dir.join(*id)),
    }).collect())
}

fn validate_job_id(id: &str) -> Result<(), String> {
    if id.is_empty() || id.len() > 128 || !id.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.')) {
        return Err("Недопустимый идентификатор задания".into());
    }
    Ok(())
}

fn find_model(id: &str) -> Result<(&'static str, ModelVariant), String> {
    MODELS.iter()
        .find(|(model_id, _, _)| *model_id == id)
        .map(|(model_id, _, variant)| (*model_id, *variant))
        .ok_or_else(|| format!("Неизвестная модель: {id}"))
}

fn validate_request(request: JobRequest) -> Result<ValidatedRequest, String> {
    validate_job_id(&request.job_id)?;
    let (model_id, variant) = find_model(&request.model)?;
    if let Some(model) = &request.diarization_model {
        if model != "wespeaker_resnet34" {
            return Err(format!("Неподдерживаемая модель диаризации: {model}. Доступна только wespeaker_resnet34"));
        }
    }
    if request.inputs.is_empty() {
        return Err("Выберите хотя бы один файл".into());
    }
    for input in &request.inputs {
        let path = Path::new(input);
        if !path.is_absolute() {
            return Err(format!("Путь к файлу должен быть абсолютным: {input}"));
        }
        let metadata = path.metadata().map_err(|err| format!("Не удалось открыть {input}: {err}"))?;
        if !metadata.is_file() || path.file_stem().is_none() {
            return Err(format!("Необычный или недопустимый файл: {input}"));
        }
    }
    let output_dir = PathBuf::from(&request.output_dir);
    if !output_dir.is_absolute() || !output_dir.is_dir() {
        return Err("Укажите существующую выходную папку с абсолютным путём".into());
    }
    // Check permission before downloading a model or spending time on inference.
    tempfile::NamedTempFile::new_in(&output_dir)
        .map_err(|err| format!("Не удалось записать в выходную папку: {err}"))?;
    if request.formats.is_empty() {
        return Err("Выберите хотя бы один формат результата".into());
    }
    let mut formats = Vec::with_capacity(request.formats.len());
    for name in &request.formats {
        let format = match name.as_str() {
            "txt" => ExportFormat::Txt,
            "json" => ExportFormat::Json,
            "srt" => ExportFormat::Srt,
            "vtt" => ExportFormat::Vtt,
            "md" => ExportFormat::Md,
            _ => return Err(format!("Неподдерживаемый формат: {name}")),
        };
        if formats.contains(&format) {
            return Err(format!("Формат выбран повторно: {name}"));
        }
        formats.push(format);
    }
    Ok(ValidatedRequest {
        job_id: request.job_id,
        inputs: request.inputs,
        variant,
        model_id,
        output_dir,
        formats,
        diarization: request.diarization,
    })
}

fn begin_job(state: &AsrState, id: &str) -> Result<JobGuard, String> {
    let mut active = state.active.lock().map_err(|_| "Не удалось открыть список заданий".to_string())?;
    if active.is_some() {
        return Err("Другое задание распознавания ещё выполняется".into());
    }
    let cancelled = Arc::new(AtomicBool::new(false));
    *active = Some(ActiveJob { id: id.to_owned(), cancelled: cancelled.clone() });
    Ok(JobGuard { active: state.active.clone(), cancelled })
}

pub(crate) fn begin_live_job(state: &AsrState, id: &str) -> Result<LiveAsrJob, String> {
    validate_job_id(id)?;
    Ok(LiveAsrJob {
        guard: begin_job(state, id)?,
        job_id: id.to_owned(),
        cache: state.engine.clone(),
    })
}

fn check_cancelled(cancelled: &AtomicBool) -> Result<(), String> {
    if cancelled.load(Ordering::Relaxed) {
        Err("Задание отменено".into())
    } else {
        Ok(())
    }
}

#[tauri::command]
pub fn cancel_transcription(state: State<'_, AsrState>, job_id: String) -> Result<bool, String> {
    let active = state.active.lock().map_err(|_| "Не удалось открыть список заданий".to_string())?;
    if let Some(job) = active.as_ref().filter(|job| job.id == job_id) {
        job.cancelled.store(true, Ordering::Relaxed);
        Ok(true)
    } else {
        Ok(false)
    }
}

#[tauri::command]
pub async fn transcribe(app: AppHandle, state: State<'_, AsrState>, request: JobRequest) -> Result<JobResult, String> {
    let request = validate_request(request)?;
    let guard = begin_job(&state, &request.job_id)?;
    let cancelled = guard.cancelled.clone();
    let engine_cache = state.engine.clone();
    let model_dir = models_dir(&app)?.join(request.model_id);
    let model_dir = model_dir.to_str().ok_or_else(|| "Путь к каталогу модели не поддерживается".to_string())?.to_owned();

    emit_progress(&app, &request.job_id, "preparing", None, None, Some("Подготовка распознавания"));
    if !model::is_usable_present(request.variant, Path::new(&model_dir)) {
        emit_progress(&app, &request.job_id, "downloading_model", None, None, Some("Загрузка модели; требуется подключение к интернету"));
    }
    {
        let download = model::ensure_model_variant(Some(request.variant), &model_dir);
        tokio::pin!(download);
        loop {
            tokio::select! {
                result = &mut download => {
                    result.map_err(|err| format!("Не удалось подготовить модель: {err:#}"))?;
                    break;
                }
                _ = tokio::time::sleep(Duration::from_millis(250)) => {
                    check_cancelled(&cancelled)?;
                }
            }
        }
    }
    if request.diarization {
        check_cancelled(&cancelled)?;
        if !Path::new(&model_dir).join(model::SPEAKER_MODEL_FILE).is_file() {
            emit_progress(&app, &request.job_id, "downloading_speaker_model", None, None, Some("Загрузка модели распознавания говорящих"));
        }
        let download = model::ensure_speaker_model(&model_dir);
        tokio::pin!(download);
        loop {
            tokio::select! {
                result = &mut download => {
                    result.map_err(|err| format!("Не удалось подготовить модель говорящих: {err:#}"))?;
                    break;
                }
                _ = tokio::time::sleep(Duration::from_millis(250)) => {
                    check_cancelled(&cancelled)?;
                }
            }
        }
    }
    check_cancelled(&cancelled)?;

    let saved_paths = Arc::new(Mutex::new(Vec::<String>::new()));
    let saved_in_worker = saved_paths.clone();
    let job = tauri::async_runtime::spawn_blocking(move || {
        // Keep the job registered until the blocking worker actually exits, even
        // if the invoking webview goes away while native inference is running.
        let _guard = guard;
        run_job(&app, request, &model_dir, &engine_cache, &cancelled, &saved_in_worker)
    });
    let result = match job.await {
        Ok(result) => result,
        Err(err) => Err(format!("Ошибка фонового задания: {err}")),
    };
    result.map_err(|error| {
        let paths = saved_paths.lock().unwrap_or_else(|err| err.into_inner());
        if paths.is_empty() { error } else {
            format!("{error}\nУже сохранённые файлы (не удалены):\n{}", paths.join("\n"))
        }
    })
}

fn get_engine(app: &AppHandle, job_id: &str, variant: ModelVariant, needs_speaker: bool, model_dir: &str, cache: &Mutex<Option<CachedEngine>>, cancelled: &AtomicBool) -> Result<Arc<Engine>, String> {
    check_cancelled(cancelled)?;
    let previous = cache.lock().map_err(|_| "Не удалось открыть кэш модели".to_string())?.take();
    if let Some(entry) = previous {
        if entry.variant == variant && (!needs_speaker || entry.engine.has_speaker_encoder()) {
            let engine = entry.engine.clone();
            *cache.lock().map_err(|_| "Не удалось сохранить кэш модели".to_string())? = Some(entry);
            return Ok(engine);
        }
        drop(entry);
    }
    check_cancelled(cancelled)?;
    emit_progress(app, job_id, "loading_model", None, None, Some("Загрузка модели в память"));
    let threads = thread::available_parallelism().map(|n| n.get().min(4)).unwrap_or(1);
    let engine = Arc::new(Engine::load_with_execution_provider(model_dir, Some(variant), 1, 1, 0, threads, None, gigastt_core::ExecutionProviderChoice::Cpu)
        .map_err(|err| format!("Не удалось загрузить модель: {err}"))?);
    if needs_speaker && !engine.has_speaker_encoder() {
        return Err("Модель говорящих не найдена после подготовки".into());
    }
    *cache.lock().map_err(|_| "Не удалось сохранить кэш модели".to_string())? = Some(CachedEngine {
        variant,
        engine: engine.clone(),
    });
    Ok(engine)
}

fn run_job(app: &AppHandle, request: ValidatedRequest, model_dir: &str, cache: &Mutex<Option<CachedEngine>>, cancelled: &Arc<AtomicBool>, saved_paths: &Mutex<Vec<String>>) -> Result<JobResult, String> {
    let engine = get_engine(app, &request.job_id, request.variant, request.diarization, model_dir, cache, cancelled)?;
    check_cancelled(cancelled)?;
    let mut files = Vec::with_capacity(request.inputs.len());
    for (index, input) in request.inputs.iter().enumerate() {
        check_cancelled(cancelled)?;
        let duration = audio::probe_duration_file(input).ok().flatten().filter(|d| d.is_finite() && *d > 0.0);
        let mut processed = None;

        #[cfg(any(target_os = "linux", target_os = "windows"))]
        if request.diarization && duration.is_some_and(|seconds| seconds > 1_800.0) {
            processed = long_diarization::try_transcribe(app, &request.job_id, input, model_dir, &engine, cancelled)?
                .map(ProcessedFile::Long);
        }

        if processed.is_none() {
            let counter = Arc::new(AtomicU64::new(0));
            let message = if request.diarization { "Распознавание и диаризация файла" } else { "Распознавание файла" };
            let initial_percent = if cfg!(any(target_os = "linux", target_os = "windows")) && request.diarization && duration.is_none() {
                None
            } else {
                Some(index as f64 / request.inputs.len() as f64 * 100.0)
            };
            emit_progress(app, &request.job_id, "transcribing", initial_percent, Some(input), Some(message));
            let watcher = ProgressWatcher::start(app.clone(), request.job_id.clone(), input.clone(), index, request.inputs.len(), duration, counter.clone());
            let mut session = engine.pool.checkout_blocking().map_err(|err| format!("Не удалось занять сессию модели: {err}"))?;
            let outcome = request.diarization.then(|| Arc::new(OnceLock::new()));
            let transcription = engine.transcribe_request(
                TranscribeRequest::new(TranscribeSource::Path(input))
                    .with_abort(Some(cancelled.clone()))
                    .with_progress(Some(counter))
                    .with_diarization(request.diarization)
                    .with_diarization_outcome(outcome.clone()),
                &mut session,
            );
            drop(session);
            drop(watcher);
            let transcription = match transcription {
                Ok(transcription) => Some(transcription),
                Err(error) => {
                    #[cfg(any(target_os = "linux", target_os = "windows"))]
                    {
                        let may_be_long = request.diarization
                            && matches!(&error, GigasttError::AudioTooLong { .. } | GigasttError::InvalidAudio { .. });
                        let original = match &error {
                            GigasttError::AudioTooLong { limit_secs, .. } if request.diarization =>
                                format!("Стандартная диаризация файла {input} ограничена {limit_secs:.0} с"),
                            _ => format!("Не удалось распознать {input}: {error}"),
                        };
                        if may_be_long {
                            match long_diarization::try_transcribe(app, &request.job_id, input, model_dir, &engine, cancelled) {
                                Ok(Some(long)) => { processed = Some(ProcessedFile::Long(long)); None }
                                Ok(None) => return Err(original),
                                Err(error) => return Err(format!("{original}\nПодготовка длинной записи: {error}")),
                            }
                        } else {
                            return Err(original);
                        }
                    }
                    #[cfg(not(any(target_os = "linux", target_os = "windows")))]
                    return Err(format_transcription_error(input, request.diarization, error));
                }
            };
            check_cancelled(cancelled)?;
            if let Some(transcription) = transcription {
                let diarization_outcome = outcome.as_ref().and_then(|sink| sink.get()).copied();
                #[cfg(any(target_os = "linux", target_os = "windows"))]
                if request.diarization && matches!(diarization_outcome, Some(DiarizationOutcome::DurationCeiling { .. })) {
                    processed = long_diarization::try_transcribe(app, &request.job_id, input, model_dir, &engine, cancelled)?
                        .map(ProcessedFile::Long);
                }
                if processed.is_none() {
                    let segments = if request.diarization {
                        if matches!(diarization_outcome, Some(DiarizationOutcome::NoSpeakerModel)) {
                            let mut cached = cache.lock().map_err(|_| "Не удалось открыть кэш модели".to_string())?;
                            if cached.as_ref().is_some_and(|entry| Arc::ptr_eq(&entry.engine, &engine)) {
                                *cached = None;
                            }
                        }
                        validate_diarization_outcome(diarization_outcome, &transcription.words)
                            .map_err(|err| format!("Не удалось выполнить диаризацию {input}: {err}"))?;
                        Some(export::to_transcript_segments(&transcription.words))
                    } else {
                        None
                    };
                    let diarized_text = segments.as_ref().map(|segments| render_speaker_text(segments));
                    processed = Some(ProcessedFile::Regular { transcription, segments, diarized_text });
                }
            }
        }
        let processed = processed.ok_or_else(|| "Не получен результат распознавания".to_string())?;
        let saving_percent = match &processed {
            ProcessedFile::Regular { .. } => Some((index + 1) as f64 / request.inputs.len() as f64 * 100.0),
            #[cfg(any(target_os = "linux", target_os = "windows"))]
            ProcessedFile::Long(_) => None,
        };
        emit_progress(app, &request.job_id, "saving", saving_percent, Some(input), Some("Сохранение результата"));
        let mut outputs = Vec::with_capacity(request.formats.len());
        for format in &request.formats {
            check_cancelled(cancelled)?;
            let content = match &processed {
                ProcessedFile::Regular { transcription, diarized_text, .. } => match (format, diarized_text) {
                    (ExportFormat::Txt, Some(text)) => text.clone(),
                    (ExportFormat::Md, Some(text)) => format!("# Transcript\n\n{text}\n"),
                    (ExportFormat::Md, None) => format!("# Transcript\n\n{}\n", transcription.text),
                    _ => format.render(transcription, &RenderOpts::default()),
                },
                #[cfg(any(target_os = "linux", target_os = "windows"))]
                ProcessedFile::Long(result) => long_diarization::render(*format, result)?,
            };
            let path = save_output(&request.output_dir, Path::new(input), *format, content.as_bytes())
                .map_err(|err| format!("Не удалось сохранить результат для {input}: {err}"))?;
            let path = path.to_string_lossy().into_owned();
            saved_paths.lock().map_err(|_| "Не удалось записать список сохранённых файлов".to_string())?.push(path.clone());
            outputs.push(OutputFile { format: format.to_string(), path });
        }
        let (transcript, segments) = match processed {
            ProcessedFile::Regular { transcription, segments, diarized_text } =>
                (diarized_text.unwrap_or(transcription.text), segments.map(ResultSegments::Regular)),
            #[cfg(any(target_os = "linux", target_os = "windows"))]
            ProcessedFile::Long(result) => (result.transcript, Some(ResultSegments::Long(result.segments))),
        };
        files.push(FileResult { input_path: input.clone(), transcript, outputs, segments });
    }
    emit_progress(app, &request.job_id, "completed", Some(100.0), None, Some("Распознавание завершено"));
    Ok(JobResult { files })
}

fn format_transcription_error(input: &str, diarization: bool, error: GigasttError) -> String {
    match error {
        GigasttError::AudioTooLong { limit_secs, .. } if diarization =>
            format!("Диаризация файла {input} поддерживает записи не длиннее {limit_secs:.0} с. Предел превышен; результат для этого файла не сохранён."),
        err => format!("Не удалось распознать {input}: {err}"),
    }
}

fn validate_diarization_outcome(outcome: Option<DiarizationOutcome>, words: &[WordInfo]) -> Result<(), String> {
    match outcome {
        Some(DiarizationOutcome::Applied) if words.is_empty() || words.iter().any(|word| word.speaker.is_some()) => Ok(()),
        Some(DiarizationOutcome::Applied) => Err("Речь распознана, но модель не определила ни одного говорящего".into()),
        Some(DiarizationOutcome::NoSpeakerModel) => Err("Модель говорящих отсутствует или не загрузилась; проверьте файл модели".into()),
        Some(DiarizationOutcome::DurationCeiling { input_secs, ceiling_secs }) =>
            Err(format!("Длительность {input_secs:.0} с превышает предел диаризации {ceiling_secs:.0} с")),
        Some(DiarizationOutcome::Failed) => Err("Ошибка модели или алгоритма диаризации".into()),
        _ => Err("Не получен результат диаризации".into()),
    }
}

fn render_speaker_text(segments: &[Segment]) -> String {
    let mut text = String::new();
    for segment in segments {
        if !text.is_empty() {
            text.push_str("\n\n");
        }
        match segment.speaker {
            Some(speaker) => text.push_str(&format!("[SPEAKER_{speaker}] ")),
            None => text.push_str("[SPEAKER_UNKNOWN] "),
        }
        text.push_str(&segment.text);
    }
    text
}

// Poll only real engine sample counts. Missing container duration is reported
// as indeterminate rather than an invented percentage.
struct ProgressWatcher {
    done: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

impl ProgressWatcher {
    fn start(app: AppHandle, job_id: String, input: String, index: usize, total: usize, duration: Option<f64>, counter: Arc<AtomicU64>) -> Self {
        let done = Arc::new(AtomicBool::new(false));
        let stopped = done.clone();
        let thread = thread::spawn(move || {
            let mut last = 0;
            while !stopped.load(Ordering::Relaxed) {
                thread::park_timeout(Duration::from_millis(300));
                if stopped.load(Ordering::Relaxed) { break; }
                let samples = counter.load(Ordering::Acquire);
                if samples == last { continue; }
                last = samples;
                let percent = duration.map(|seconds| {
                    let fraction = (samples as f64 / (seconds * 16_000.0)).clamp(0.0, 1.0);
                    (index as f64 + fraction) / total as f64 * 100.0
                });
                emit_progress(&app, &job_id, "transcribing", percent, Some(&input), Some("Распознавание файла"));
            }
        });
        Self { done, thread: Some(thread) }
    }
}

impl Drop for ProgressWatcher {
    fn drop(&mut self) {
        self.done.store(true, Ordering::Relaxed);
        if let Some(thread) = self.thread.take() {
            thread.thread().unpark();
            let _ = thread.join();
        }
    }
}

fn save_output(directory: &Path, input: &Path, format: ExportFormat, bytes: &[u8]) -> io::Result<PathBuf> {
    let stem = input.file_stem().ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "input filename has no stem"))?;
    let mut staged = tempfile::NamedTempFile::new_in(directory)?;
    staged.write_all(bytes)?;
    staged.as_file().sync_all()?;
    for suffix in 0..=u32::MAX {
        let mut name = OsString::from(stem);
        name.push(".gigaam");
        if suffix != 0 { name.push(format!(" ({suffix})")); }
        name.push(".");
        name.push(format.extension());
        let destination = directory.join(name);
        match staged.persist_noclobber(&destination) {
            Ok(_) => return Ok(destination),
            Err(err) if err.error.kind() == io::ErrorKind::AlreadyExists => staged = err.file,
            Err(err) => return Err(err.error),
        }
    }
    Err(io::Error::new(io::ErrorKind::AlreadyExists, "no available output filename"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn saves_without_overwriting_existing_outputs() {
        let dir = tempfile::tempdir().unwrap();
        let input = dir.path().join("audio.wav");
        let existing = dir.path().join("audio.gigaam.txt");
        std::fs::write(&existing, b"keep me").unwrap();
        let output = save_output(dir.path(), &input, ExportFormat::Txt, b"new transcript").unwrap();
        assert_eq!(std::fs::read(existing).unwrap(), b"keep me");
        assert_eq!(output.file_name().unwrap(), "audio.gigaam (1).txt");
        assert_eq!(std::fs::read(output).unwrap(), b"new transcript");
    }

    #[test]
    fn validates_models_formats_and_paths() {
        let dir = tempfile::tempdir().unwrap();
        let input = dir.path().join("audio.wav");
        std::fs::write(&input, b"audio").unwrap();
        let request = |model: &str, formats: Vec<&str>| JobRequest {
            job_id: "job-1".into(),
            inputs: vec![input.to_string_lossy().into_owned()],
            model: model.into(),
            output_dir: dir.path().to_string_lossy().into_owned(),
            formats: formats.into_iter().map(str::to_owned).collect(),
            diarization: false,
            diarization_model: None,
        };
        for (id, _, _) in MODELS {
            assert!(validate_request(request(id, vec!["txt", "srt"])).is_ok());
        }
        assert!(validate_request(request("unknown", vec!["txt"])).is_err());
        assert!(validate_request(request("v3_e2e_rnnt", vec!["txt", "txt"])).is_err());
        assert!(validate_request(request("v3_e2e_rnnt", vec!["exe"])).is_err());
    }

    #[test]
    fn diarization_ipc_is_optional_and_validated() {
        let dir = tempfile::tempdir().unwrap();
        let input = dir.path().join("audio.wav");
        std::fs::write(&input, b"audio").unwrap();
        let mut request = serde_json::json!({
            "job_id": "job-1",
            "inputs": [input],
            "model": "v3_e2e_rnnt",
            "output_dir": dir.path(),
            "formats": ["txt"]
        });
        let plain: JobRequest = serde_json::from_value(request.clone()).unwrap();
        assert!(!validate_request(plain).unwrap().diarization);
        request["diarization"] = serde_json::json!(true);
        request["diarization_model"] = serde_json::json!("wespeaker_resnet34");
        let diarized: JobRequest = serde_json::from_value(request.clone()).unwrap();
        assert!(validate_request(diarized).unwrap().diarization);
        request["diarization_model"] = serde_json::json!("pyannote");
        let unsupported: JobRequest = serde_json::from_value(request).unwrap();
        assert!(validate_request(unsupported).is_err());
    }

    #[test]
    fn diarization_failures_are_not_silently_saved() {
        let labeled = vec![WordInfo::new("hello", 0.0, 0.4, 0.9, Some(0))];
        let unknown = vec![WordInfo::new("hello", 0.0, 0.4, 0.9, None)];
        assert!(validate_diarization_outcome(Some(DiarizationOutcome::Applied), &labeled).is_ok());
        assert!(validate_diarization_outcome(Some(DiarizationOutcome::Applied), &[]).is_ok());
        assert!(validate_diarization_outcome(Some(DiarizationOutcome::Applied), &unknown).is_err());
        assert!(validate_diarization_outcome(Some(DiarizationOutcome::NoSpeakerModel), &labeled).is_err());
        assert!(validate_diarization_outcome(Some(DiarizationOutcome::Failed), &labeled).is_err());
        assert!(validate_diarization_outcome(Some(DiarizationOutcome::DurationCeiling { input_secs: 4000.0, ceiling_secs: 3600.0 }), &labeled).is_err());
        assert!(validate_diarization_outcome(None, &labeled).is_err());
    }

    #[test]
    fn diarization_duration_error_does_not_claim_observed_length_is_total() {
        let error = GigasttError::AudioTooLong { observed_secs: 1800.01, limit_secs: 1800.0 };
        assert_eq!(
            format_transcription_error("meeting.wav", true, error),
            "Диаризация файла meeting.wav поддерживает записи не длиннее 1800 с. Предел превышен; результат для этого файла не сохранён."
        );
    }

    #[test]
    fn diarized_exports_keep_speakers_and_do_not_guess_uncovered_words() {
        let words = vec![
            WordInfo::new("hello", 0.0, 0.4, 0.9, Some(0)),
            WordInfo::new("unknown", 0.5, 0.8, 0.9, None),
            WordInfo::new("goodbye", 1.0, 1.4, 0.9, Some(1)),
        ];
        let segments = export::to_transcript_segments(&words);
        let text = render_speaker_text(&segments);
        assert_eq!(text, "[SPEAKER_0] hello\n\n[SPEAKER_UNKNOWN] unknown\n\n[SPEAKER_1] goodbye");
        let result = gigastt_core::inference::TranscribeResult {
            text: "hello unknown goodbye".into(),
            words,
            duration_s: 1.4,
            confidence: Some(0.9),
        };
        let json: serde_json::Value = serde_json::from_str(&ExportFormat::Json.render(&result, &RenderOpts::default())).unwrap();
        assert_eq!(json["words"][0]["speaker"], 0);
        assert!(json["words"][1].get("speaker").is_none());
        assert_eq!(json["words"][2]["speaker"], 1);
        for format in [ExportFormat::Srt, ExportFormat::Vtt] {
            let output = format.render(&result, &RenderOpts::default());
            assert!(output.contains("[SPEAKER_0] hello"));
            assert!(output.contains("[SPEAKER_1] goodbye"));
        }
    }

    #[test]
    fn already_attributed_words_keep_long_timestamps_and_unknown_speakers_in_exports() {
        // This exercises exports only: it does not establish speaker identity.
        let words = vec![
            WordInfo::new("before", 1799.5, 1799.75, 0.9, Some(0)),
            WordInfo::new("unknown", 1800.0, 1800.25, 0.9, None),
            WordInfo::new("other", 1800.5, 1801.0, 0.9, Some(1)),
            WordInfo::new("returns", 3600.5, 3601.0, 0.9, Some(0)),
        ];
        assert!(validate_diarization_outcome(Some(DiarizationOutcome::Applied), &words).is_ok());
        let segments = export::to_transcript_segments(&words);
        assert_eq!(
            render_speaker_text(&segments),
            "[SPEAKER_0] before\n\n[SPEAKER_UNKNOWN] unknown\n\n[SPEAKER_1] other\n\n[SPEAKER_0] returns"
        );
        let result = gigastt_core::inference::TranscribeResult {
            text: "before unknown other returns".into(),
            words,
            duration_s: 3601.0,
            confidence: Some(0.9),
        };
        let json: serde_json::Value = serde_json::from_str(
            &ExportFormat::Json.render(&result, &RenderOpts::default()),
        ).unwrap();
        assert_eq!(json["words"][0]["speaker"], 0);
        assert!(json["words"][1].get("speaker").is_none());
        assert_eq!(json["words"][2]["speaker"], 1);
        assert_eq!(json["words"][3]["speaker"], 0);
        assert_eq!(json["words"][0]["start"], 1799.5);
        assert_eq!(json["words"][1]["start"], 1800.0);
        assert_eq!(json["words"][1]["end"], 1800.25);
        assert_eq!(json["words"][3]["start"], 3600.5);
        assert_eq!(json["words"][3]["end"], 3601.0);
        let srt = ExportFormat::Srt.render(&result, &RenderOpts::default());
        assert!(srt.contains("00:29:59,500"));
        assert!(srt.contains("01:00:00,500"));
        let vtt = ExportFormat::Vtt.render(&result, &RenderOpts::default());
        assert!(vtt.contains("00:29:59.500"));
        assert!(vtt.contains("01:00:00.500"));
    }

    #[test]
    fn live_job_shares_exclusion_and_cancellation() {
        let state = AsrState::default();
        let live = begin_live_job(&state, "live-1").unwrap();
        assert!(begin_job(&state, "batch-1").is_err());
        let cancelled = live.cancelled();
        cancelled.store(true, Ordering::Relaxed);
        assert!(check_cancelled(&live.guard.cancelled).is_err());
        drop(live);
        assert!(begin_job(&state, "batch-1").is_ok());
        assert!(begin_live_job(&state, "invalid id").is_err());
    }

    #[test]
    fn cancelled_job_is_released_only_when_guard_drops() {
        let state = AsrState::default();
        let guard = begin_job(&state, "one").unwrap();
        guard.cancelled.store(true, Ordering::Relaxed);
        assert!(check_cancelled(&guard.cancelled).is_err());
        assert!(begin_job(&state, "two").is_err());
        drop(guard);
        assert!(begin_job(&state, "two").is_ok());
    }
}
