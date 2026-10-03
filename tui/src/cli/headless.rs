//! Headless mode: `gigaam transcribe …` / `gigaam llm …` for scripts and agents.
//! The same worker and the same persisted settings as the TUI, but with a
//! deterministic stdout, no terminal and exit codes instead of a status line.

use std::{
    collections::HashSet,
    io::{self, Write},
    sync::mpsc::{self, Receiver},
    time::{Duration, Instant},
};

use serde_json::{json, Value};

use crate::{
    commands::short_name,
    options::backend_is_supported,
    options::is_model,
    settings::{load_settings, TuiSettings},
    signals,
    worker::{llm_settings_from, worker_command},
    worker_session::{
        Stderr, Transport, WorkerEvent, WorkerEventKind, WorkerSession, EVENT_CAPACITY,
    },
};

use super::args::{
    parse_headless_args, resolve_headless_paths, HeadlessCommand, LlmArgs, TranscribeArgs,
    HEADLESS_USAGE,
};

/// The same `start` command the TUI sends, from persisted settings plus flag overrides.
pub(super) fn headless_start_payload(settings: &TuiSettings, args: &TranscribeArgs) -> Value {
    let backend = args
        .backend
        .clone()
        .or_else(|| backend_is_supported(&settings.backend).then(|| settings.backend.clone()))
        .unwrap_or_else(|| "auto".into());
    let model = args
        .model
        .clone()
        .or_else(|| is_model(&settings.model).then(|| settings.model.clone()))
        .unwrap_or_else(|| "v3_e2e_rnnt".into());
    let diarization_backend = args
        .diarization_backend
        .clone()
        .unwrap_or_else(|| settings.diarization_backend.clone());
    // Sortformer always detects the speaker count itself (mirrors `/speakers` in the TUI).
    let num_speakers = if diarization_backend == "sortformer" {
        None
    } else {
        args.num_speakers.unwrap_or(settings.num_speakers)
    };
    let formats = args.formats.clone().unwrap_or_else(|| {
        if settings.formats.is_empty() {
            vec!["txt".into()]
        } else {
            settings.formats.clone()
        }
    });
    json!({
        "type": "start",
        "files": args.files,
        "output_dir": args.output_dir,
        "formats": formats,
        "diarization": args.diarize.unwrap_or(settings.diarization),
        "diarization_backend": diarization_backend,
        "num_speakers": num_speakers,
        "backend": backend,
        "model": model,
        "onnx_provider": settings.onnx_provider,
        "audio_preprocessing_mode": args.audio_mode.clone().unwrap_or_else(|| settings.audio_preprocessing_mode.clone()),
        "subtitle_sentence_split": settings.subtitle_sentence_split,
        "subtitle_max_lines": settings.subtitle_max_lines.clamp(1, 4),
        "subtitle_max_width": settings.subtitle_max_width.clamp(20, 100),
    })
}

pub(super) fn headless_llm_payload(settings: &TuiSettings, args: &LlmArgs) -> Value {
    json!({
        "type": "llm_start",
        "files": args.files,
        "modes": args.modes,
        "prompt": args.prompt,
        "output_dir": args.output_dir,
        "settings": llm_settings_from(settings, &[]),
    })
}

/// One human-readable line for the events that end a file or the run; `None` for the rest.
pub(super) fn format_headless_line(event: &Value) -> Option<String> {
    let message = |value: &Value| value.as_str().unwrap_or("").to_string();
    match event["type"].as_str()? {
        "file_completed" => {
            let name = short_name(event["file"].as_str().unwrap_or("?"));
            let result = &event["result"];
            if result["success"].as_bool().unwrap_or(false) {
                let saved: Vec<&str> = result["saved_files"]
                    .as_array()
                    .map(|items| items.iter().filter_map(Value::as_str).collect())
                    .unwrap_or_default();
                Some(format!("✓ {name} → {}", saved.join(", ")))
            } else {
                Some(format!("× {name}: {}", message(&result["error"])))
            }
        }
        "error" => Some(format!("error: {}", message(&event["message"]))),
        "completed" | "llm_completed" if event["cancelled"].as_bool().unwrap_or(false) => {
            Some("cancelled".into())
        }
        "completed" | "llm_completed" if !event["success"].as_bool().unwrap_or(false) => event
            ["message"]
            .as_str()
            .filter(|text| !text.is_empty())
            .map(|text| format!("error: {text}")),
        _ => None,
    }
}

pub(crate) fn run_headless(argv: &[String]) -> io::Result<i32> {
    let mut command = match parse_headless_args(argv) {
        Ok(command) => command,
        Err(message) => {
            eprintln!("gigaam: {message}\n\n{HEADLESS_USAGE}");
            return Ok(2);
        }
    };
    if let Err(message) = resolve_headless_paths(&mut command) {
        eprintln!("gigaam: {message}");
        return Ok(2);
    }
    let settings = load_settings();
    let (payload, json_output, quiet, started_type, terminal_type) = match &command {
        HeadlessCommand::Transcribe(args) => (
            headless_start_payload(&settings, args),
            args.json,
            args.quiet,
            "started",
            "completed",
        ),
        HeadlessCommand::Llm(args) => (
            headless_llm_payload(&settings, args),
            args.json,
            false,
            "llm_started",
            "llm_completed",
        ),
    };
    // `--json` promises a clean stderr and `--quiet` a silent run; the worker's own
    // diagnostics (Python warnings, download progress) would break both.
    let stderr = if json_output || quiet {
        Stderr::Discard
    } else {
        Stderr::Inherit
    };
    // The same transport as the UI: the worker gets its own process group/Job, so
    // stopping it also stops CLI agents it launched (`child.kill()` left them
    // running), lines are bounded, and a dead worker is an event, not a silence.
    let (sender, events) = mpsc::sync_channel(EVENT_CAPACITY);
    let session = WorkerSession::spawn_with(
        HEADLESS_GENERATION,
        worker_command(),
        Transport {
            stderr,
            max_protocol_line: HEADLESS_MAX_LINE,
        },
        sender,
    );
    let result = match session.try_send(payload) {
        Ok(()) => headless_event_loop(&events, json_output, quiet, started_type, terminal_type),
        Err(error) => {
            eprintln!("gigaam: worker unavailable: {error} (try `gigaam --update`)");
            Ok(3)
        }
    };
    session.stop();
    await_stopped(&events);
    match result {
        Ok(code) => Ok(code),
        // `gigaam transcribe … | head -1`: the reader went away; nothing to report.
        Err((code, error)) if error.kind() == io::ErrorKind::BrokenPipe => Ok(code),
        Err((_, error)) => Err(error),
    }
}

/// Headless has one worker for its whole life.
const HEADLESS_GENERATION: u64 = 1;
/// Headless keeps the full (non-compact) ASR events of its JSON contract, and
/// `completed` repeats every result with its word timings: a batch of long
/// recordings is far beyond the UI's 8 MiB. Still bounded, never unlimited.
const HEADLESS_MAX_LINE: usize = 256 * 1024 * 1024;
/// No event at all for this long (model downloads report progress on stderr
/// only) means the worker hangs.
const HEADLESS_SILENCE: Duration = Duration::from_secs(3600);
/// How often the loop looks at termination signals between events.
const HEADLESS_POLL: Duration = Duration::from_millis(200);

/// Waits (bounded) for the transport to confirm the worker tree is gone.
fn await_stopped(events: &Receiver<WorkerEvent>) {
    let deadline = Instant::now() + Duration::from_secs(6);
    while Instant::now() < deadline {
        match events.recv_timeout(HEADLESS_POLL) {
            Ok(WorkerEvent {
                kind: WorkerEventKind::Stopped(result),
                ..
            }) => {
                if let Err(error) = result {
                    eprintln!("gigaam: worker shutdown: {error}");
                }
                return;
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => return,
            _ => {}
        }
    }
    eprintln!("gigaam: worker shutdown: termination was not confirmed");
}

/// Consumes worker events until the terminal one; on a write failure returns the
/// exit code computed so far together with the error.
fn headless_event_loop(
    events: &Receiver<WorkerEvent>,
    json_output: bool,
    quiet: bool,
    started_type: &str,
    terminal_type: &str,
) -> Result<i32, (i32, io::Error)> {
    let mut exit_code = 1;
    let mut batch_started = false;
    let mut progress_line_open = false;
    let mut streamed_modes: HashSet<String> = HashSet::new();
    let stdout = io::stdout();
    let mut last_event = Instant::now();
    loop {
        if signals::requested() {
            // The caller stops the worker tree and re-raises the signal.
            break;
        }
        let kind = match events.recv_timeout(HEADLESS_POLL) {
            Ok(event) => event.kind,
            Err(mpsc::RecvTimeoutError::Timeout) => {
                if last_event.elapsed() >= HEADLESS_SILENCE {
                    eprintln!("gigaam: worker stopped responding");
                    break;
                }
                continue;
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                eprintln!("gigaam: worker exited unexpectedly (try `gigaam --update`)");
                exit_code = 3;
                break;
            }
        };
        last_event = Instant::now();
        let event = match kind {
            WorkerEventKind::Message(value) => value,
            // Only non-protocol stdout lines arrive here (stderr is inherited or
            // discarded); they used to be dropped silently.
            WorkerEventKind::Diagnostic(text) => {
                if !json_output && !quiet {
                    eprintln!("worker {text}");
                }
                continue;
            }
            WorkerEventKind::Failed(error) => {
                if progress_line_open {
                    eprint!("\r\x1b[K");
                }
                eprintln!("gigaam: worker unavailable: {error} (try `gigaam --update`)");
                exit_code = 3;
                break;
            }
            WorkerEventKind::Stopped(_) => {
                eprintln!("gigaam: worker exited unexpectedly (try `gigaam --update`)");
                exit_code = 3;
                break;
            }
        };
        let kind = event["type"].as_str().unwrap_or("").to_string();
        if kind == started_type {
            batch_started = true;
        }
        let terminal = kind == terminal_type;
        if terminal {
            exit_code = if event["success"].as_bool().unwrap_or(false) {
                0
            } else {
                1
            };
        }
        let written = if json_output {
            let mut out = stdout.lock();
            writeln!(out, "{}", serde_json::to_string(&event).unwrap_or_default())
        } else {
            if progress_line_open && kind != "progress" {
                eprint!("\r\x1b[K");
                progress_line_open = false;
            }
            let mut out = stdout.lock();
            let written = match kind.as_str() {
                "progress" if !quiet => {
                    let percent = (event["file_progress"].as_f64().unwrap_or(0.0) * 100.0) as u16;
                    eprint!(
                        "\r{:<12} {:>3}%",
                        event["stage"].as_str().unwrap_or(""),
                        percent.min(100)
                    );
                    progress_line_open = true;
                    Ok(())
                }
                "log" if !quiet => {
                    eprintln!("{}", event["message"].as_str().unwrap_or(""));
                    Ok(())
                }
                "llm_chunk" => {
                    // Streaming providers (API) deliver the text here; CLI providers
                    // only deliver it in `llm_completed.results`, so the heading is
                    // printed lazily, right before the first text of each mode.
                    let mode = event["mode"].as_str().unwrap_or("").to_string();
                    let mut heading = Ok(());
                    if !streamed_modes.contains(&mode) {
                        if !streamed_modes.is_empty() {
                            heading = writeln!(out);
                        }
                        heading = heading.and_then(|_| writeln!(out, "## {mode}"));
                        streamed_modes.insert(mode);
                    }
                    heading
                        .and_then(|_| write!(out, "{}", event["text"].as_str().unwrap_or("")))
                        .and_then(|_| out.flush())
                }
                "llm_completed" => {
                    let results = event["results"].as_array().cloned().unwrap_or_default();
                    let mut written = if streamed_modes.is_empty() {
                        Ok(())
                    } else {
                        writeln!(out) // streamed text ends without a newline
                    };
                    let mut printed = streamed_modes.len();
                    for result in &results {
                        let mode = result["mode"].as_str().unwrap_or("");
                        if streamed_modes.contains(mode) {
                            continue;
                        }
                        if printed > 0 {
                            written = written.and_then(|_| writeln!(out));
                        }
                        written = written.and_then(|_| {
                            writeln!(
                                out,
                                "## {mode}\n{}",
                                result["text"].as_str().unwrap_or("").trim_end()
                            )
                        });
                        printed += 1;
                    }
                    written.and_then(|_| out.flush())
                }
                _ => Ok(()),
            };
            match format_headless_line(&event) {
                Some(line) if kind == "file_completed" => {
                    written.and_then(|_| writeln!(out, "{line}"))
                }
                Some(line) => {
                    eprintln!("{line}");
                    written
                }
                None => written,
            }
        };
        if let Err(error) = written {
            return Err((exit_code, error));
        }
        if terminal {
            break;
        }
        if kind == "error" && !batch_started {
            // The worker rejected the request before starting (missing file, bad
            // formats, …): no `completed` will follow, so the run ends here.
            break;
        }
    }
    Ok(exit_code)
}
