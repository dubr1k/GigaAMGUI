//! Native live capture and GigaAM streaming recognition.
//!
//! Capture runs independently of inference so a stop request closes the device
//! even when an ONNX call is still running. Recording to WAV is opt-in.

use std::fs::File;
use std::io::BufWriter;
use std::path::Path;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::Duration;

use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::{FromSample, Sample, SampleFormat, SizedSample};
use gigastt_core::inference::{
    audio::{self, SampleRate}, Engine, SessionTriplet, StreamingState, TranscriptSegment,
};
use rtrb::{Consumer, Producer, RingBuffer};
use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, State};

use crate::asr::{self, AsrState, LiveAsrJob};

const TARGET_RATE: u32 = 16_000;
const BUFFER_SECONDS: usize = 8;
const CAPTURE_POLL: Duration = Duration::from_millis(20);

#[derive(Default)]
pub struct LiveState {
    active: Arc<Mutex<Option<ActiveSession>>>,
}

impl Drop for LiveState {
    fn drop(&mut self) {
        let active = self.active.lock().unwrap_or_else(|error| error.into_inner());
        if let Some(session) = active.as_ref() {
            session.control.cancelled.store(true, Ordering::Release);
            session.control.stop.store(true, Ordering::Release);
        }
    }
}

struct ActiveSession {
    id: String,
    control: Arc<CaptureControl>,
}

struct CaptureControl {
    stop: AtomicBool,
    cancelled: Arc<AtomicBool>,
    finished: AtomicBool,
    overflow: AtomicBool,
    bad_frames: AtomicBool,
    stream_failed: AtomicBool,
    stream_error: Mutex<Option<String>>,
}

impl CaptureControl {
    fn new(cancelled: Arc<AtomicBool>) -> Self {
        Self {
            stop: AtomicBool::new(false),
            cancelled,
            finished: AtomicBool::new(false),
            overflow: AtomicBool::new(false),
            bad_frames: AtomicBool::new(false),
            stream_failed: AtomicBool::new(false),
            stream_error: Mutex::new(None),
        }
    }

    fn cancelled(&self) -> bool {
        self.cancelled.load(Ordering::Acquire)
    }

    fn stopping(&self) -> bool {
        self.stop.load(Ordering::Acquire) || self.cancelled()
    }

    fn capture_failed(&self) -> bool {
        self.overflow.load(Ordering::Acquire)
            || self.bad_frames.load(Ordering::Acquire)
            || self.stream_failed.load(Ordering::Acquire)
    }

    fn set_stream_error(&self, message: String) {
        let mut error = self.stream_error.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        if error.is_none() {
            *error = Some(message);
        }
        self.stream_failed.store(true, Ordering::Release);
    }

    fn failure(&self) -> Option<String> {
        if self.stream_failed.load(Ordering::Acquire) {
            return self.stream_error.lock().unwrap_or_else(|error| error.into_inner()).clone()
                .or_else(|| Some("Ошибка аудиопотока".into()));
        }
        if self.overflow.load(Ordering::Acquire) {
            return Some("Распознавание не успевает за записью: буфер аудио переполнен".into());
        }
        if self.bad_frames.load(Ordering::Acquire) {
            return Some("Устройство вернуло неполный аудиокадр".into());
        }
        None
    }
}

#[derive(Clone, Copy, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CaptureSource {
    Microphone,
    System,
}

#[derive(Deserialize)]
pub struct LiveRequest {
    session_id: String,
    model: String,
    source: CaptureSource,
    #[serde(default)]
    output_dir: Option<String>,
}

#[derive(Clone, Serialize)]
struct LiveEvent {
    session_id: String,
    kind: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    text: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    message: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    recording_path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    recording_complete: Option<bool>,
}

fn emit(app: &AppHandle, id: &str, kind: &'static str, text: Option<String>, message: Option<String>) {
    let _ = app.emit("live-event", LiveEvent {
        session_id: id.into(), kind, text, message, recording_path: None, recording_complete: None,
    });
}

#[tauri::command]
pub fn live_start(
    app: AppHandle,
    state: State<'_, LiveState>,
    asr_state: State<'_, AsrState>,
    request: LiveRequest,
) -> Result<(), String> {
    if request.session_id.is_empty()
        || request.session_id.len() > 128
        || !request.session_id.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
    {
        return Err("Некорректный идентификатор Live-сессии".into());
    }
    if request.model.is_empty() || request.model.len() > 128 {
        return Err("Некорректная модель распознавания".into());
    }
    if matches!(request.source, CaptureSource::System)
        && !cfg!(any(target_os = "windows", target_os = "linux"))
    {
        return Err("Захват системного звука поддерживается только в Windows и Linux".into());
    }
    if let Some(dir) = request.output_dir.as_deref() {
        if dir.is_empty() || !Path::new(dir).is_dir() {
            return Err("Выберите существующий каталог для записи Live".into());
        }
    }

    // Reserve the same job slot as file transcription before spawning. This
    // also makes simultaneous starts and model-cache replacement race-free.
    let mut active = state.active.lock().map_err(|_| "Не удалось открыть Live-сессию".to_string())?;
    if active.is_some() {
        return Err("Другая Live-сессия ещё выполняется".into());
    }
    let job = asr::begin_live_job(&asr_state, &request.session_id)?;
    let control = Arc::new(CaptureControl::new(job.cancelled()));
    *active = Some(ActiveSession { id: request.session_id.clone(), control: control.clone() });
    emit(&app, &request.session_id, "preparing", None, None);

    let active_sessions = state.active.clone();
    let worker_app = app.clone();
    let session_id = request.session_id.clone();
    let id = session_id.clone();
    let spawn = thread::Builder::new().name("gigaam-live".into()).spawn(move || {
        let mut transcript = String::new();
        let mut recording = None;
        let outcome = catch_unwind(AssertUnwindSafe(|| {
            run_session(&worker_app, &active_sessions, &request, &job, &control, &mut transcript, &mut recording)
        }));
        // The lease remains held until capture has closed and all native ASR
        // work has returned, even on failure or an unwind.
        drop(job);
        let mut active = active_sessions.lock().unwrap_or_else(|error| error.into_inner());
        let (kind, message) = match outcome {
            Ok(Ok(())) if control.cancelled() => ("cancelled", None),
            Ok(Err(error)) if control.cancelled() => ("cancelled", Some(error)),
            Ok(Ok(())) => ("stopped", None),
            Ok(Err(error)) => ("failed", Some(error)),
            Err(_) => ("failed", Some("Неожиданная ошибка Live-распознавания".into())),
        };
        if active.as_ref().is_some_and(|session| session.id == id) {
            *active = None;
            let (recording_path, recording_complete) = match recording {
                Some(saved) => (Some(saved.path), Some(saved.complete)),
                None => (None, None),
            };
            let _ = worker_app.emit("live-event", LiveEvent {
                session_id: id, kind, text: Some(transcript), message,
                recording_path, recording_complete,
            });
        }
    });
    if let Err(error) = spawn {
        *active = None;
        let message = format!("Не удалось запустить Live-распознавание: {error}");
        emit(&app, &session_id, "failed", None, Some(message.clone()));
        return Err(message);
    }
    Ok(())
}

fn request_stop(app: &AppHandle, state: &LiveState, id: &str, cancel: bool) -> Result<bool, String> {
    let active = state.active.lock().map_err(|_| "Не удалось открыть Live-сессию".to_string())?;
    let Some(session) = active.as_ref().filter(|session| session.id == id) else {
        return Ok(false);
    };
    if cancel {
        session.control.cancelled.store(true, Ordering::Release);
    }
    if !session.control.stop.swap(true, Ordering::AcqRel) {
        emit(app, id, "stopping", None, None);
    }
    Ok(true)
}

/// Graceful stop: close the capture, drain queued audio and decode the final tail.
#[tauri::command]
pub fn live_stop(app: AppHandle, state: State<'_, LiveState>, session_id: String) -> Result<bool, String> {
    request_stop(&app, &state, &session_id, false)
}

/// Cancellation: close capture and discard audio that has not been finalized.
#[tauri::command]
pub fn live_cancel(app: AppHandle, state: State<'_, LiveState>, session_id: String) -> Result<bool, String> {
    request_stop(&app, &state, &session_id, true)
}

struct CaptureReady {
    consumer: Consumer<f32>,
    sample_rate: u32,
}

// The worker owns this guard. Even unwinding through a decoder failure stops
// and joins the capture thread before the ASR job lease can be dropped.
struct CaptureGuard {
    control: Arc<CaptureControl>,
    thread: Option<JoinHandle<()>>,
}

impl Drop for CaptureGuard {
    fn drop(&mut self) {
        self.control.stop.store(true, Ordering::Release);
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

struct CaptureFinished {
    control: Arc<CaptureControl>,
    worker: thread::Thread,
}

impl Drop for CaptureFinished {
    fn drop(&mut self) {
        self.control.finished.store(true, Ordering::Release);
        self.worker.unpark();
    }
}
struct SavedRecording {
    path: String,
    complete: bool,
}

struct Recording {
    writer: hound::WavWriter<BufWriter<File>>,
    path: String,
    frames: u64,
    broken: bool,
}

impl Recording {
    fn create(directory: &str, sample_rate: u32) -> Result<Self, String> {
        // tempfile creates a unique file with create_new semantics. Persist it
        // before writing so even a failed recording is not silently discarded.
        let file = tempfile::Builder::new().prefix("gigaam-live-").suffix(".wav")
            .tempfile_in(directory)
            .map_err(|error| format!("Не удалось создать файл записи: {error}"))?;
        let (file, path) = file.keep().map_err(|error| format!("Не удалось сохранить файл записи: {error}"))?;
        let path = path.to_string_lossy().into_owned();
        let spec = hound::WavSpec {
            channels: 1, sample_rate, bits_per_sample: 16, sample_format: hound::SampleFormat::Int,
        };
        let writer = hound::WavWriter::new(BufWriter::new(file), spec)
            .map_err(|error| format!("Не удалось создать WAV {path}: {error}"))?;
        Ok(Self { writer, path, frames: 0, broken: false })
    }

    fn write(&mut self, samples: &[f32]) -> Result<(), String> {
        // RIFF uses 32-bit byte lengths; stop before its header would overflow.
        const MAX_FRAMES: u64 = (u32::MAX as u64 - 128) / 2;
        if self.frames + samples.len() as u64 > MAX_FRAMES {
            self.broken = true;
            return Err(format!("Достигнут предельный размер WAV: {}", self.path));
        }
        let result = {
            let mut writer = self.writer.get_i16_writer(samples.len() as u32);
            for &sample in samples {
                writer.write_sample((sample * 32_767.0).round() as i16);
            }
            writer.flush()
        };
        if let Err(error) = result {
            self.broken = true;
            return Err(format!("Ошибка записи WAV {}: {error}", self.path));
        }
        self.frames += samples.len() as u64;
        Ok(())
    }

    fn finish(self, saved: &mut Option<SavedRecording>, capture_ok: bool) -> Result<(), String> {
        let Self { writer, path, broken, .. } = self;
        match writer.finalize() {
            Ok(()) => {
                *saved = Some(SavedRecording { path, complete: capture_ok && !broken });
                Ok(())
            }
            Err(error) => {
                *saved = Some(SavedRecording { path: path.clone(), complete: false });
                Err(format!("Не удалось завершить WAV {path}: {error}"))
            }
        }
    }
}


fn run_session(
    app: &AppHandle,
    active: &Arc<Mutex<Option<ActiveSession>>>,
    request: &LiveRequest,
    job: &LiveAsrJob,
    control: &Arc<CaptureControl>,
    transcript: &mut String,
    recording: &mut Option<SavedRecording>,
) -> Result<(), String> {
    // Keep preparation on this worker: dropping an invoking webview cannot
    // release the lease while a download or a native call is still running.
    let prepared: Result<bool, String> = tauri::async_runtime::block_on(async {
        let prepare = job.prepare(app, &request.model);
        tokio::pin!(prepare);
        loop {
            if control.stopping() {
                return Ok(false);
            }
            tokio::select! {
                result = &mut prepare => {
                    result?;
                    return Ok(true);
                }
                _ = tokio::time::sleep(Duration::from_millis(100)) => {}
            }
        }
    });
    if !prepared? {
        return Ok(());
    }
    // Loading can be a long native call; stopping during it prevents capture
    // from starting, but the lease stays held until the call has returned.
    let engine = job.engine(app, &request.model)?;
    if control.stopping() {
        return Ok(());
    }
    let mut triplet = engine.pool.checkout_blocking()
        .map_err(|error| format!("Не удалось занять модель: {error}"))?;
    let mut stream_state = engine.create_state(false);
    stream_state.abort = Some(control.cancelled.clone());

    let (ready_tx, ready_rx) = mpsc::sync_channel(1);
    let capture_control = control.clone();
    let capture_worker = thread::current();
    let source = request.source;
    let capture_thread = thread::Builder::new().name("gigaam-live-capture".into()).spawn(move || {
        let _finished = CaptureFinished { control: capture_control.clone(), worker: capture_worker.clone() };
        let result = catch_unwind(AssertUnwindSafe(|| {
            capture(source, &capture_control, &capture_worker, ready_tx)
        }));
        match result {
            Ok(Ok(())) => {}
            Ok(Err(error)) => capture_control.set_stream_error(error),
            Err(_) => capture_control.set_stream_error("Неожиданная ошибка захвата аудио".into()),
        }
    }).map_err(|error| format!("Не удалось запустить захват аудио: {error}"))?;
    let _capture = CaptureGuard { control: control.clone(), thread: Some(capture_thread) };

    let ready = loop {
        match ready_rx.recv_timeout(CAPTURE_POLL) {
            Ok(ready) => break ready,
            Err(mpsc::RecvTimeoutError::Timeout) => continue,
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                // The sender can be dropped just before the capture thread
                // records its startup error; wait for its exit before reading it.
                while !control.finished.load(Ordering::Acquire) {
                    thread::park_timeout(CAPTURE_POLL);
                }
                if let Some(error) = control.failure() {
                    return Err(error);
                }
                if control.stopping() {
                    return Ok(());
                }
                return Err("Не удалось открыть устройство аудиозахвата".into());
            }
        }
    };

    // Serialize this event with stop/cancel so recording never follows stopping.
    {
        let active = active.lock().unwrap_or_else(|error| error.into_inner());
        if active.as_ref().is_some_and(|session| session.id == request.session_id)
            && !control.stopping() && !control.capture_failed()
        {
            emit(app, &request.session_id, "recording", None, None);
        }
    }

    let mut recorder = request.output_dir.as_deref()
        .map(|directory| Recording::create(directory, ready.sample_rate))
        .transpose()?;
    if let Some(writer) = recorder.as_ref() {
        // A panic can still leave a recoverable file; report it as incomplete.
        *recording = Some(SavedRecording { path: writer.path.clone(), complete: false });
    }
    let mut consumer = ready.consumer;
    let chunk_frames = (ready.sample_rate as usize / 10).max(1);
    let mut last_partial = String::new();
    let mut received_audio = false;
    let mut failure: Option<String> = None;
    let mut asr_failed = false;
    loop {
        if control.cancelled() && recorder.is_none() {
            return Ok(());
        }
        let finished = control.finished.load(Ordering::Acquire);
        let available = consumer.slots();
        if available >= chunk_frames || (finished && available > 0) {
            let count = available.min(chunk_frames);
            let mut samples = Vec::with_capacity(count);
            for _ in 0..count {
                match consumer.pop() {
                    Ok(sample) => samples.push(sample),
                    Err(_) => break,
                }
            }
            if !samples.is_empty() {
                received_audio = true;
                if let Some(writer) = recorder.as_mut() {
                    if !writer.broken {
                        if let Err(error) = writer.write(&samples) {
                            failure.get_or_insert(error);
                            control.stop.store(true, Ordering::Release);
                        }
                    }
                }
                if !control.cancelled() && !asr_failed {
                    if let Err(error) = feed_audio(app, &request.session_id, &engine, &mut stream_state,
                        &mut triplet, ready.sample_rate, samples, transcript, &mut last_partial)
                    {
                        asr_failed = true;
                        if !control.cancelled() {
                            failure.get_or_insert(error);
                            control.stop.store(true, Ordering::Release);
                        }
                    }
                }
            }
        } else if finished {
            break;
        } else {
            thread::park_timeout(CAPTURE_POLL);
        }
    }

    let capture_error = control.failure();
    if let Some(writer) = recorder {
        if let Err(error) = writer.finish(recording, capture_error.is_none()) {
            failure.get_or_insert(error);
        }
    }
    if !control.cancelled() && !asr_failed {
        if received_audio && ready.sample_rate != TARGET_RATE {
            // Flush the 256-tap resampler's retained tail with silence.
            if let Err(error) = feed_audio(app, &request.session_id, &engine, &mut stream_state,
                &mut triplet, ready.sample_rate, vec![0.0; 256], transcript, &mut last_partial)
            {
                if !control.cancelled() {
                    failure.get_or_insert(error);
                }
                asr_failed = true;
            }
        }
        if !asr_failed && !control.cancelled() {
            match engine.try_finish_stream(&mut stream_state, &mut triplet) {
                Ok(Some(segment)) => publish_segment(app, &request.session_id, segment, transcript, &mut last_partial),
                Ok(None) => {}
                Err(error) if !control.cancelled() => {
                    failure.get_or_insert(format!("Не удалось завершить распознавание: {error}"));
                }
                Err(_) => {}
            }
        }
    }
    if let Some(error) = failure.or(capture_error) {
        return Err(error);
    }
    Ok(())
}

fn feed_audio(
    app: &AppHandle,
    id: &str,
    engine: &Engine,
    state: &mut StreamingState,
    triplet: &mut SessionTriplet,
    sample_rate: u32,
    samples: Vec<f32>,
    transcript: &mut String,
    last_partial: &mut String,
) -> Result<(), String> {
    let samples = if sample_rate == TARGET_RATE {
        samples
    } else {
        let mut output = std::mem::take(&mut state.resample_output_buf);
        audio::resample_with_cache(samples, SampleRate(sample_rate), SampleRate(TARGET_RATE),
            &mut state.resampler, &mut output)
            .map_err(|error| format!("Не удалось преобразовать частоту аудио: {error}"))?;
        output
    };
    let result = engine.process_chunk(&samples, state, triplet);
    if sample_rate != TARGET_RATE {
        state.resample_output_buf = samples;
    }
    for segment in result.map_err(|error| format!("Ошибка распознавания Live: {error}"))? {
        publish_segment(app, id, segment, transcript, last_partial);
    }
    Ok(())
}

fn publish_segment(
    app: &AppHandle,
    id: &str,
    segment: TranscriptSegment,
    transcript: &mut String,
    last_partial: &mut String,
) {
    let text = segment.text.trim();
    if segment.is_final {
        last_partial.clear();
        if !text.is_empty() {
            if !transcript.is_empty() {
                transcript.push(' ');
            }
            transcript.push_str(text);
            emit(app, id, "transcript", Some(text.to_owned()), None);
        }
    } else if text != last_partial {
        last_partial.clear();
        last_partial.push_str(text);
        emit(app, id, "partial", Some(text.to_owned()), None);
    }
}

fn capture(
    source: CaptureSource,
    control: &Arc<CaptureControl>,
    worker: &thread::Thread,
    ready: mpsc::SyncSender<CaptureReady>,
) -> Result<(), String> {
    if control.stopping() {
        return Ok(());
    }
    #[cfg(target_os = "linux")]
    if matches!(source, CaptureSource::System) {
        return linux_monitor::capture(control, worker, ready);
    }
    let host = cpal::default_host();
    let device = match source {
        CaptureSource::Microphone => host.default_input_device()
            .ok_or_else(|| "Микрофон по умолчанию не найден".to_string())?,
        CaptureSource::System => host.default_output_device()
            .ok_or_else(|| "Устройство вывода звука по умолчанию не найдено".to_string())?,
    };
    // On Windows CPAL/WASAPI opens an output endpoint in loopback mode when
    // build_input_stream is used; its format is the *output* mix format.
    let supported = match source {
        CaptureSource::Microphone => device.default_input_config(),
        CaptureSource::System => device.default_output_config(),
    }.map_err(|error| format!("Не удалось определить формат аудиоустройства: {error}"))?;
    let format = supported.sample_format();
    let config: cpal::StreamConfig = supported.into();
    let rate = config.sample_rate.0;
    if !(8_000..=384_000).contains(&rate) || config.channels == 0 || config.channels > 64 {
        return Err("Устройство использует неподдерживаемую частоту или число каналов".into());
    }
    let (producer, consumer) = RingBuffer::<f32>::new(rate as usize * BUFFER_SECONDS);
    if control.stopping() {
        return Ok(());
    }
    let stream = match format {
        SampleFormat::I8 => build_stream::<i8>(&device, &config, producer, control, worker),
        SampleFormat::I16 => build_stream::<i16>(&device, &config, producer, control, worker),
        SampleFormat::I32 => build_stream::<i32>(&device, &config, producer, control, worker),
        SampleFormat::I64 => build_stream::<i64>(&device, &config, producer, control, worker),
        SampleFormat::U8 => build_stream::<u8>(&device, &config, producer, control, worker),
        SampleFormat::U16 => build_stream::<u16>(&device, &config, producer, control, worker),
        SampleFormat::U32 => build_stream::<u32>(&device, &config, producer, control, worker),
        SampleFormat::U64 => build_stream::<u64>(&device, &config, producer, control, worker),
        SampleFormat::F32 => build_stream::<f32>(&device, &config, producer, control, worker),
        SampleFormat::F64 => build_stream::<f64>(&device, &config, producer, control, worker),
        other => return Err(format!("Неподдерживаемый формат аудиоустройства: {other}")),
    }?;
    if control.stopping() {
        return Ok(());
    }
    stream.play().map_err(|error| format!("Не удалось начать захват аудио: {error}"))?;
    if ready.send(CaptureReady { consumer, sample_rate: rate }).is_err() {
        return Ok(());
    }
    while !control.stopping() && !control.capture_failed() {
        thread::sleep(CAPTURE_POLL);
    }
    // Drop on this thread: CPAL streams are not portable across threads.
    drop(stream);
    Ok(())
}

fn build_stream<T>(
    device: &cpal::Device,
    config: &cpal::StreamConfig,
    mut producer: Producer<f32>,
    control: &Arc<CaptureControl>,
    worker: &thread::Thread,
) -> Result<cpal::Stream, String>
where
    T: SizedSample,
    f32: FromSample<T>,
{
    let channels = usize::from(config.channels);
    let data_control = control.clone();
    let data_worker = worker.clone();
    let error_control = control.clone();
    let error_worker = worker.clone();
    device.build_input_stream(config, move |data: &[T], _| {
        if data_control.stopping() || data_control.capture_failed() {
            return;
        }
        let mut frames = data.chunks_exact(channels);
        for frame in &mut frames {
            let mut sum = 0.0f32;
            for &sample in frame {
                let value = f32::from_sample(sample);
                sum += if value.is_finite() { value.clamp(-1.0, 1.0) } else { 0.0 };
            }
            if producer.push(sum / channels as f32).is_err() {
                data_control.overflow.store(true, Ordering::Release);
                break;
            }
        }
        if !frames.remainder().is_empty() {
            data_control.bad_frames.store(true, Ordering::Release);
        }
        data_worker.unpark();
    }, move |error| {
        error_control.set_stream_error(format!("Ошибка захвата аудио: {error}"));
        error_worker.unpark();
    }, None).map_err(|error| format!("Не удалось открыть аудиопоток: {error}"))
}

// CPAL's Linux backend is ALSA, not a PulseAudio output monitor. Use the
// PulseAudio protocol directly so a system session cannot fall back to the
// default input device when an output disappears.
#[cfg(target_os = "linux")]
mod linux_monitor {
    use super::{CaptureControl, CaptureReady, CAPTURE_POLL, BUFFER_SECONDS};
    use rtrb::RingBuffer;
    use std::cell::Cell;
    use std::ffi::{c_char, c_int, c_void, CStr, CString};
    use std::ptr;
    use std::sync::{mpsc, Arc};
    use std::thread;
    use std::time::{Duration, Instant};

    const CONTEXT_READY: c_int = 4;
    const CONTEXT_FAILED: c_int = 5;
    const STREAM_CREATING: c_int = 1;
    const STREAM_READY: c_int = 2;
    const OPERATION_RUNNING: c_int = 0;
    const OPERATION_DONE: c_int = 1;
    const STREAM_START_CORKED: u32 = 0x0001;
    const STREAM_DONT_MOVE: u32 = 0x0200;
    const SAMPLE_FLOAT32LE: c_int = 5;
    const START_TIMEOUT: Duration = Duration::from_secs(5);

    // These are the stable initial fields of the corresponding libpulse
    // structs. The library may append fields, which we never access.
    #[repr(C)]
    struct SampleSpec {
        format: c_int,
        rate: u32,
        channels: u8,
    }

    #[repr(C)]
    struct ChannelMap {
        channels: u8,
        map: [c_int; 32],
    }

    #[repr(C)]
    struct Volume {
        channels: u8,
        values: [u32; 32],
    }

    #[repr(C)]
    struct ServerInfo {
        user_name: *const c_char,
        host_name: *const c_char,
        server_version: *const c_char,
        server_name: *const c_char,
        sample_spec: SampleSpec,
        default_sink_name: *const c_char,
    }

    // Sink and source introspection have the same prefix; peer is the monitor
    // source for a sink and the monitored sink for a source.
    #[repr(C)]
    struct DeviceInfo {
        name: *const c_char,
        index: u32,
        description: *const c_char,
        sample_spec: SampleSpec,
        channel_map: ChannelMap,
        owner_module: u32,
        volume: Volume,
        mute: c_int,
        peer_index: u32,
        peer_name: *const c_char,
    }

    #[repr(C)]
    struct BufferAttr {
        maxlength: u32,
        tlength: u32,
        prebuf: u32,
        minreq: u32,
        fragsize: u32,
    }

    type ServerCallback = unsafe extern "C" fn(*mut c_void, *const ServerInfo, *mut c_void);
    type DeviceCallback = unsafe extern "C" fn(*mut c_void, *const DeviceInfo, c_int, *mut c_void);
    type StreamCallback = unsafe extern "C" fn(*mut c_void, *mut c_void);
    type SuccessCallback = unsafe extern "C" fn(*mut c_void, c_int, *mut c_void);

    #[link(name = "pulse")]
    extern "C" {
        fn pa_mainloop_new() -> *mut c_void;
        fn pa_mainloop_free(mainloop: *mut c_void);
        fn pa_mainloop_get_api(mainloop: *mut c_void) -> *mut c_void;
        fn pa_mainloop_iterate(mainloop: *mut c_void, block: c_int, retval: *mut c_int) -> c_int;
        fn pa_context_new(api: *mut c_void, name: *const c_char) -> *mut c_void;
        fn pa_context_connect(context: *mut c_void, server: *const c_char, flags: u32, api: *const c_void) -> c_int;
        fn pa_context_get_state(context: *const c_void) -> c_int;
        fn pa_context_disconnect(context: *mut c_void);
        fn pa_context_unref(context: *mut c_void);
        fn pa_context_get_server_info(context: *mut c_void, callback: Option<ServerCallback>, userdata: *mut c_void) -> *mut c_void;
        fn pa_context_get_sink_info_by_name(context: *mut c_void, name: *const c_char, callback: Option<DeviceCallback>, userdata: *mut c_void) -> *mut c_void;
        fn pa_context_get_source_info_by_index(context: *mut c_void, index: u32, callback: Option<DeviceCallback>, userdata: *mut c_void) -> *mut c_void;
        fn pa_operation_get_state(operation: *const c_void) -> c_int;
        fn pa_operation_cancel(operation: *mut c_void);
        fn pa_operation_unref(operation: *mut c_void);
        fn pa_stream_new(context: *mut c_void, name: *const c_char, spec: *const SampleSpec, map: *const c_void) -> *mut c_void;
        fn pa_stream_connect_record(stream: *mut c_void, device: *const c_char, attr: *const BufferAttr, flags: u32) -> c_int;
        fn pa_stream_get_state(stream: *const c_void) -> c_int;
        fn pa_stream_get_sample_spec(stream: *const c_void) -> *const SampleSpec;
        fn pa_stream_is_corked(stream: *const c_void) -> c_int;
        fn pa_stream_get_device_index(stream: *const c_void) -> u32;
        fn pa_stream_get_device_name(stream: *const c_void) -> *const c_char;
        fn pa_stream_set_moved_callback(stream: *mut c_void, callback: Option<StreamCallback>, userdata: *mut c_void);
        fn pa_stream_cork(stream: *mut c_void, cork: c_int, callback: Option<SuccessCallback>, userdata: *mut c_void) -> *mut c_void;
        fn pa_stream_readable_size(stream: *const c_void) -> usize;
        fn pa_stream_peek(stream: *mut c_void, data: *mut *const c_void, nbytes: *mut usize) -> c_int;
        fn pa_stream_drop(stream: *mut c_void) -> c_int;
        fn pa_stream_disconnect(stream: *mut c_void) -> c_int;
        fn pa_stream_unref(stream: *mut c_void);
    }

    struct Query<T> {
        value: Option<T>,
        invalid: bool,
    }

    #[derive(Debug, PartialEq, Eq)]
    struct Device {
        name: CString,
        index: u32,
        rate: u32,
        peer_index: u32,
        peer_name: CString,
    }

    unsafe fn copy_name(name: *const c_char) -> Option<CString> {
        if name.is_null() {
            return None;
        }
        let name = CStr::from_ptr(name);
        if name.to_bytes().is_empty() {
            None
        } else {
            Some(name.to_owned())
        }
    }

    unsafe extern "C" fn server_callback(_: *mut c_void, info: *const ServerInfo, userdata: *mut c_void) {
        let result = &mut *(userdata as *mut Query<CString>);
        if info.is_null() || result.value.is_some() {
            result.invalid = true;
            return;
        }
        result.value = copy_name((*info).default_sink_name);
    }

    unsafe extern "C" fn device_callback(_: *mut c_void, info: *const DeviceInfo, eol: c_int, userdata: *mut c_void) {
        let result = &mut *(userdata as *mut Query<Device>);
        if eol < 0 {
            result.invalid = true;
        } else if eol == 0 {
            if info.is_null() || result.value.is_some() {
                result.invalid = true;
                return;
            }
            let info = &*info;
            if let (Some(name), Some(peer_name)) = (copy_name(info.name), copy_name(info.peer_name)) {
                result.value = Some(Device {
                    name, index: info.index, rate: info.sample_spec.rate,
                    peer_index: info.peer_index, peer_name,
                });
            } else {
                result.invalid = true;
            }
        }
    }

    unsafe extern "C" fn moved_callback(_: *mut c_void, userdata: *mut c_void) {
        (*(userdata as *const Cell<bool>)).set(true);
    }

    unsafe extern "C" fn success_callback(_: *mut c_void, success: c_int, userdata: *mut c_void) {
        let result = &mut *(userdata as *mut Query<bool>);
        if result.value.is_some() {
            result.invalid = true;
        } else {
            result.value = Some(success != 0);
        }
    }

    struct Operation(*mut c_void);

    impl Drop for Operation {
        fn drop(&mut self) {
            unsafe {
                // A cancelled operation cannot call its stack-backed userdata.
                pa_operation_cancel(self.0);
                pa_operation_unref(self.0);
            }
        }
    }

    struct Client {
        mainloop: *mut c_void,
        context: *mut c_void,
        stream: *mut c_void,
    }

    impl Drop for Client {
        fn drop(&mut self) {
            unsafe {
                if !self.stream.is_null() {
                    pa_stream_disconnect(self.stream);
                    pa_stream_unref(self.stream);
                }
                if !self.context.is_null() {
                    pa_context_disconnect(self.context);
                    pa_context_unref(self.context);
                }
                if !self.mainloop.is_null() {
                    pa_mainloop_free(self.mainloop);
                }
            }
        }
    }

    impl Client {
        fn connect() -> Result<Self, String> {
            let mut client = Self { mainloop: ptr::null_mut(), context: ptr::null_mut(), stream: ptr::null_mut() };
            unsafe {
                client.mainloop = pa_mainloop_new();
                if client.mainloop.is_null() {
                    return Err("Не удалось создать цикл PulseAudio/PipeWire".into());
                }
                client.context = pa_context_new(pa_mainloop_get_api(client.mainloop), b"GigaAMGUI\0".as_ptr().cast());
                if client.context.is_null()
                    || pa_context_connect(client.context, ptr::null(), 0, ptr::null()) < 0
                {
                    return Err("Не удалось подключиться к PulseAudio/PipeWire".into());
                }
            }
            Ok(client)
        }

        fn iterate(&self) -> Result<(), String> {
            unsafe {
                if pa_mainloop_iterate(self.mainloop, 0, ptr::null_mut()) < 0
                    || pa_context_get_state(self.context) >= CONTEXT_FAILED
                {
                    return Err("Соединение с PulseAudio/PipeWire потеряно или недоступно".into());
                }
            }
            Ok(())
        }

        fn wait(&self, control: &CaptureControl, condition: impl Fn() -> Result<bool, String>) -> Result<bool, String> {
            let deadline = Instant::now() + START_TIMEOUT;
            loop {
                if control.stopping() || control.capture_failed() {
                    return Ok(false);
                }
                if condition()? {
                    return Ok(true);
                }
                if Instant::now() >= deadline {
                    return Err("PulseAudio/PipeWire не ответил при открытии системного звука".into());
                }
                self.iterate()?;
                thread::sleep(CAPTURE_POLL);
            }
        }

        fn query<T>(
            &self,
            control: &CaptureControl,
            start: impl FnOnce(*mut c_void) -> *mut c_void,
        ) -> Result<Option<T>, String> {
            let mut result = Query { value: None, invalid: false };
            let operation = start((&mut result as *mut Query<T>).cast());
            if operation.is_null() {
                return Err("Не удалось запросить устройства PulseAudio/PipeWire".into());
            }
            let operation = Operation(operation);
            let completed = self.wait(control, || {
                match unsafe { pa_operation_get_state(operation.0) } {
                    OPERATION_RUNNING => Ok(false),
                    OPERATION_DONE => Ok(true),
                    _ => Err("Запрос PulseAudio/PipeWire отменён".into()),
                }
            });
            // Drop before result: on stop/error the callback may still be pending.
            drop(operation);
            if !completed? {
                return Ok(None);
            }
            match (result.invalid, result.value) {
                (false, Some(value)) => Ok(Some(value)),
                _ => Err("Не удалось однозначно определить монитор системного звука PulseAudio/PipeWire".into()),
            }
        }

        fn default_sink(&self, control: &CaptureControl) -> Result<Option<CString>, String> {
            self.query(control, |data| unsafe {
                pa_context_get_server_info(self.context, Some(server_callback), data)
            })
        }

        fn sink(&self, control: &CaptureControl, name: &CStr) -> Result<Option<Device>, String> {
            self.query(control, |data| unsafe {
                pa_context_get_sink_info_by_name(self.context, name.as_ptr(), Some(device_callback), data)
            })
        }

        fn source(&self, control: &CaptureControl, index: u32) -> Result<Option<Device>, String> {
            self.query(control, |data| unsafe {
                pa_context_get_source_info_by_index(self.context, index, Some(device_callback), data)
            })
        }

        fn stream_matches(&self, source: &Device, moved: &Cell<bool>) -> bool {
            if moved.get() {
                return false;
            }
            unsafe {
                let name = pa_stream_get_device_name(self.stream);
                !name.is_null()
                    && pa_stream_get_device_index(self.stream) == source.index
                    && CStr::from_ptr(name) == source.name.as_c_str()
            }
        }
    }

    fn monitor_matches(default_sink: &CStr, sink: &Device, source: &Device) -> bool {
        sink.name.as_c_str() == default_sink
            && sink.index != u32::MAX
            && source.index != u32::MAX
            && sink.peer_index == source.index
            && sink.peer_name == source.name
            && source.peer_index == sink.index
            && source.peer_name == sink.name
    }

    pub(super) fn capture(
        control: &Arc<CaptureControl>,
        worker: &thread::Thread,
        ready: mpsc::SyncSender<CaptureReady>,
    ) -> Result<(), String> {
        // The callback's userdata must outlive the stream and its Client.
        let moved = Cell::new(false);
        let mut client = Client::connect()?;
        if !client.wait(control, || Ok(unsafe { pa_context_get_state(client.context) } == CONTEXT_READY))? {
            return Ok(());
        }
        let Some(default_sink) = client.default_sink(control)? else { return Ok(()); };
        let Some(sink) = client.sink(control, &default_sink)? else { return Ok(()); };
        if sink.peer_index == u32::MAX || !(8_000..=384_000).contains(&sink.rate) {
            return Err("Монитор устройства вывода недоступен или имеет неподдерживаемую частоту".into());
        }
        let Some(source) = client.source(control, sink.peer_index)? else { return Ok(()); };
        if !monitor_matches(&default_sink, &sink, &source) {
            return Err("Не найден монитор текущего устройства вывода PulseAudio/PipeWire".into());
        }

        let spec = SampleSpec { format: SAMPLE_FLOAT32LE, rate: sink.rate, channels: 1 };
        let attr = BufferAttr {
            maxlength: u32::MAX, tlength: u32::MAX, prebuf: u32::MAX, minreq: u32::MAX,
            fragsize: (sink.rate / 50) * 4,
        };
        unsafe {
            client.stream = pa_stream_new(client.context, b"GigaAMGUI System Audio\0".as_ptr().cast(), &spec, ptr::null());
            if client.stream.is_null() {
                return Err("Не удалось создать поток системного звука PulseAudio/PipeWire".into());
            }
            pa_stream_set_moved_callback(client.stream, Some(moved_callback), (&moved as *const Cell<bool>).cast_mut().cast());
            if pa_stream_connect_record(
                client.stream, source.name.as_ptr(), &attr, STREAM_START_CORKED | STREAM_DONT_MOVE,
            ) < 0 {
                return Err("Не удалось открыть монитор системного звука PulseAudio/PipeWire".into());
            }
        }
        if !client.wait(control, || match unsafe { pa_stream_get_state(client.stream) } {
            STREAM_CREATING => Ok(false),
            STREAM_READY => Ok(true),
            _ => Err("Монитор системного звука PulseAudio/PipeWire недоступен".into()),
        })? {
            return Ok(());
        }

        let actual_spec = unsafe { pa_stream_get_sample_spec(client.stream).as_ref() };
        if unsafe { pa_stream_is_corked(client.stream) } != 1
            || actual_spec.map_or(true, |actual| {
                actual.format != spec.format || actual.rate != spec.rate || actual.channels != spec.channels
            })
        {
            return Err("Поток монитора имеет неподтверждённый формат или состояние".into());
        }

        // The stream is still corked. Recheck both ends and the default sink:
        // an unplug or default-device change during discovery must fail closed.
        let Some(current_sink) = client.sink(control, &default_sink)? else { return Ok(()); };
        let Some(current_source) = client.source(control, sink.peer_index)? else { return Ok(()); };
        let Some(current_default) = client.default_sink(control)? else { return Ok(()); };
        if current_sink != sink || current_source != source || current_default != default_sink
            || !monitor_matches(&current_default, &current_sink, &current_source)
            || !client.stream_matches(&source, &moved)
        {
            return Err("Устройство вывода или его монитор изменились при открытии системного звука".into());
        }
        let Some(resumed) = client.query::<bool>(control, |data| unsafe {
            pa_stream_cork(client.stream, 0, Some(success_callback), data)
        })? else { return Ok(()); };
        if !resumed || !client.stream_matches(&source, &moved) {
            return Err("Не удалось начать захват выбранного монитора системного звука".into());
        }
        let (mut producer, consumer) = RingBuffer::<f32>::new(sink.rate as usize * BUFFER_SECONDS);
        if control.stopping() || ready.send(CaptureReady { consumer, sample_rate: sink.rate }).is_err() {
            return Ok(());
        }

        while !control.stopping() && !control.capture_failed() {
            client.iterate()?;
            if unsafe { pa_stream_get_state(client.stream) } != STREAM_READY
                || !client.stream_matches(&source, &moved)
            {
                return Err("Монитор системного звука отключён или изменился; захват остановлен".into());
            }
            loop {
                if control.stopping() || control.capture_failed() {
                    break;
                }
                let available = unsafe { pa_stream_readable_size(client.stream) };
                if available == usize::MAX {
                    return Err("Ошибка чтения монитора системного звука".into());
                }
                if available == 0 {
                    break;
                }
                let mut data: *const c_void = ptr::null();
                let mut len = 0usize;
                if unsafe { pa_stream_peek(client.stream, &mut data, &mut len) } < 0 || len == 0 {
                    return Err("Ошибка чтения монитора системного звука".into());
                }
                if len % 4 != 0 || len > isize::MAX as usize {
                    control.bad_frames.store(true, std::sync::atomic::Ordering::Release);
                } else if data.is_null() {
                    // A PulseAudio hole represents silence, not an absent frame.
                    for _ in 0..len / 4 {
                        if producer.push(0.0).is_err() {
                            control.overflow.store(true, std::sync::atomic::Ordering::Release);
                            break;
                        }
                    }
                } else {
                    let bytes = unsafe { std::slice::from_raw_parts(data.cast::<u8>(), len) };
                    for sample in bytes.chunks_exact(4) {
                        let value = f32::from_le_bytes([sample[0], sample[1], sample[2], sample[3]]);
                        let value = if value.is_finite() { value.clamp(-1.0, 1.0) } else { 0.0 };
                        if producer.push(value).is_err() {
                            control.overflow.store(true, std::sync::atomic::Ordering::Release);
                            break;
                        }
                    }
                }
                if unsafe { pa_stream_drop(client.stream) } < 0 {
                    return Err("Ошибка освобождения аудиобуфера PulseAudio/PipeWire".into());
                }
                worker.unpark();
            }
            thread::sleep(CAPTURE_POLL);
        }
        Ok(())
    }

    #[cfg(test)]
    mod tests {
        use super::{monitor_matches, Device};
        use std::ffi::CString;

        fn device(name: &str, index: u32, peer_name: &str, peer_index: u32) -> Device {
            Device {
                name: CString::new(name).unwrap(), index, rate: 48_000,
                peer_name: CString::new(peer_name).unwrap(), peer_index,
            }
        }

        #[test]
        fn only_the_current_sinks_matching_monitor_is_accepted() {
            let default = CString::new("output").unwrap();
            let sink = device("output", 1, "output.monitor", 2);
            let monitor = device("output.monitor", 2, "output", 1);
            assert!(monitor_matches(&default, &sink, &monitor));
            assert!(!monitor_matches(&CString::new("other").unwrap(), &sink, &monitor));
            assert!(!monitor_matches(&default, &sink, &device("microphone", 2, "output", 1)));
            assert!(!monitor_matches(&default, &sink, &device("output.monitor", 2, "other", 3)));
            assert!(!monitor_matches(&default, &sink, &device("output.monitor", u32::MAX, "output", 1)));
        }
    }
}
