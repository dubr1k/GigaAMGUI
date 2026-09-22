//! Headless mode: `gigaam transcribe …` / `gigaam llm …` and the `--data-dir` argument.

use std::{
    collections::HashSet,
    fs,
    io::{self, Write},
    path::PathBuf,
    process::Stdio,
    sync::mpsc::{self, Receiver},
    time::Duration,
};

use serde_json::{json, Value};

use crate::{
    commands::{
        backend_is_supported, normalize_path, selectable_backends, short_name, FORMAT_KEYS,
        MODEL_OPTIONS,
    },
    i18n::Lang,
    settings::{load_settings, TuiSettings},
    worker::{llm_settings_from, send, spawn_worker_with},
};

fn data_dir_from_args<I, S>(args: I) -> Result<Option<String>, String>
where
    I: IntoIterator<Item = S>,
    S: AsRef<str>,
{
    let values: Vec<String> = args
        .into_iter()
        .map(|value| value.as_ref().to_string())
        .collect();
    for (index, value) in values.iter().enumerate().skip(1) {
        if let Some(path) = value.strip_prefix("--data-dir=") {
            if path.is_empty() {
                return Err("--data-dir requires a path".into());
            }
            return Ok(Some(path.into()));
        }
        if value == "--data-dir" {
            return values
                .get(index + 1)
                .filter(|path| !path.is_empty() && !path.starts_with('-'))
                .cloned()
                .map(Some)
                .ok_or_else(|| "--data-dir requires a path".into());
        }
    }
    Ok(None)
}

pub(crate) fn apply_data_dir_argument() -> io::Result<()> {
    let Some(root) = data_dir_from_args(std::env::args())
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidInput, error))?
    else {
        return Ok(());
    };
    let root = PathBuf::from(root);
    std::env::set_var("GIGAAM_DATA_DIR", &root);

    if std::env::var_os("GIGAAM_RUNTIME_DIR").is_none() {
        std::env::set_var("GIGAAM_RUNTIME_DIR", root.join("runtimes"));
    }
    if std::env::var_os("GIGAAM_PYTORCH_MODEL_DIR").is_none() {
        std::env::set_var(
            "GIGAAM_PYTORCH_MODEL_DIR",
            root.join("models").join("gigaam"),
        );
    }
    if std::env::var_os("HF_HOME").is_none() {
        std::env::set_var("HF_HOME", root.join("models").join("huggingface"));
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Headless mode: `gigaam transcribe …` / `gigaam llm …` for scripts and agents.
// The same worker and the same persisted settings as the TUI, but with a
// deterministic stdout, no terminal and exit codes instead of a status line.
// ---------------------------------------------------------------------------

pub(crate) const HEADLESS_USAGE: &str = "Usage:
  gigaam                       launch the terminal UI
  --lang ru|en                 interactive UI only: interface language for this run
  --theme NAME                 interactive UI only: colour scheme for this run (`/theme` lists the names)
  gigaam transcribe FILE... [options]
  gigaam llm FILE... --mode MODE [--mode MODE ...] [options]

transcribe options (defaults come from the saved settings):
  --output DIR                 write results into DIR (default: next to each input)
  --formats LIST               comma-separated: txt,txt_timecodes,srt,vtt,md,txt_diarize,txt_diarize_timecodes
  --diarize                    enable speaker diarization
  --speakers N|auto            fixed speaker count or automatic detection
  --diarization-backend NAME   pyannote|onnx|sortformer
  --backend NAME               auto|pytorch|mlx|onnx (mlx: macOS only)
  --model ID                   v3_e2e_rnnt|multilingual_ctc|multilingual_large_ctc
  --audio-mode MODE            auto|off|light|denoise
  --json                       one JSON worker event per line on stdout; the worker's stderr is
                               silenced (use human mode without --quiet to see model/download diagnostics)
  --quiet                      no progress or log lines, worker stderr silenced

llm options:
  --mode MODE                  summary|tasks|terms|custom (repeatable)
  --prompt TEXT                the prompt for --mode custom
  --output DIR                 where session_llm_<mode>.txt is saved (default: next to the last file)
  --json                       one JSON worker event per line on stdout; worker stderr silenced

exit codes: 0 all files succeeded, 1 at least one file failed,
            2 bad arguments or an input file that does not exist
              (message on stderr; nothing on stdout even with --json),
            3 worker unavailable (run `gigaam --update`)";

#[derive(Debug)]
enum HeadlessCommand {
    Transcribe(TranscribeArgs),
    Llm(LlmArgs),
}

#[derive(Debug, Default)]
struct TranscribeArgs {
    files: Vec<String>,
    output_dir: Option<String>,
    formats: Option<Vec<String>>,
    diarize: Option<bool>,
    /// `None`: not given; `Some(None)`: `auto`; `Some(Some(n))`: fixed count.
    num_speakers: Option<Option<u32>>,
    diarization_backend: Option<String>,
    backend: Option<String>,
    model: Option<String>,
    audio_mode: Option<String>,
    json: bool,
    quiet: bool,
}

#[derive(Debug, Default)]
struct LlmArgs {
    files: Vec<String>,
    modes: Vec<String>,
    prompt: String,
    output_dir: Option<String>,
    json: bool,
}

/// Removes `--data-dir X` / `--data-dir=X`, which `apply_data_dir_argument` has
/// already consumed, so that the headless parser only sees its own arguments.
pub(crate) fn strip_data_dir(args: Vec<String>) -> Vec<String> {
    let mut result = Vec::with_capacity(args.len());
    let mut skip_next = false;
    for arg in args {
        if skip_next {
            skip_next = false;
        } else if arg == "--data-dir" {
            skip_next = true;
        } else if !arg.starts_with("--data-dir=") {
            result.push(arg);
        }
    }
    result
}

fn parse_headless_args(argv: &[String]) -> Result<HeadlessCommand, String> {
    let (subcommand, rest) = argv
        .split_first()
        .ok_or_else(|| "expected `transcribe` or `llm`".to_string())?;
    // Flags accept both `--flag value` and `--flag=value`.
    let mut items = rest.iter().map(|arg| match arg.split_once('=') {
        Some((flag, value)) if flag.starts_with("--") => {
            (flag.to_string(), Some(value.to_string()))
        }
        _ => (arg.clone(), None),
    });
    let mut files = Vec::new();
    let mut options: Vec<(String, String)> = Vec::new();
    let mut switches: Vec<String> = Vec::new();
    let value_flags: &[&str] = match subcommand.as_str() {
        "transcribe" => &[
            "--output",
            "--formats",
            "--speakers",
            "--diarization-backend",
            "--backend",
            "--model",
            "--audio-mode",
        ],
        "llm" => &["--mode", "--prompt", "--output"],
        other => {
            return Err(format!(
                "unknown command `{other}`; expected `transcribe` or `llm`"
            ))
        }
    };
    let switch_flags: &[&str] = if subcommand == "transcribe" {
        &["--diarize", "--json", "--quiet"]
    } else {
        &["--json"]
    };
    while let Some((flag, inline_value)) = items.next() {
        if !flag.starts_with('-') || flag == "-" {
            files.push(flag);
        } else if switch_flags.contains(&flag.as_str()) {
            if inline_value.is_some() {
                return Err(format!("{flag} does not take a value"));
            }
            switches.push(flag);
        } else if value_flags.contains(&flag.as_str()) {
            let value = match inline_value {
                Some(value) => value,
                None => items
                    .next()
                    .map(|(value, _)| value)
                    .filter(|value| !value.is_empty())
                    .ok_or_else(|| format!("{flag} requires a value"))?,
            };
            options.push((flag, value));
        } else {
            return Err(format!("unknown option {flag}"));
        }
    }
    if files.is_empty() {
        return Err(format!("{subcommand} needs at least one file"));
    }
    if subcommand == "llm" {
        let mut args = LlmArgs {
            files,
            json: switches.iter().any(|flag| flag == "--json"),
            ..LlmArgs::default()
        };
        for (flag, value) in options {
            match flag.as_str() {
                "--mode" if matches!(value.as_str(), "summary" | "tasks" | "terms" | "custom") => {
                    if !args.modes.contains(&value) {
                        args.modes.push(value);
                    }
                }
                "--mode" => {
                    return Err(format!(
                        "--mode must be summary|tasks|terms|custom, got `{value}`"
                    ))
                }
                "--prompt" => args.prompt = value,
                _ => args.output_dir = Some(value),
            }
        }
        if args.modes.is_empty() {
            return Err("llm needs at least one --mode (summary|tasks|terms|custom)".into());
        }
        let custom = args.modes.iter().any(|mode| mode == "custom");
        if custom && args.prompt.trim().is_empty() {
            return Err("--mode custom requires --prompt TEXT".into());
        }
        if !custom && !args.prompt.is_empty() {
            return Err("--prompt requires --mode custom".into());
        }
        return Ok(HeadlessCommand::Llm(args));
    }
    let mut args = TranscribeArgs {
        files,
        diarize: switches
            .iter()
            .any(|flag| flag == "--diarize")
            .then_some(true),
        json: switches.iter().any(|flag| flag == "--json"),
        quiet: switches.iter().any(|flag| flag == "--quiet"),
        ..TranscribeArgs::default()
    };
    for (flag, value) in options {
        match flag.as_str() {
            "--output" => args.output_dir = Some(value),
            "--formats" => {
                let formats: Vec<String> = value
                    .split(',')
                    .map(str::trim)
                    .filter(|format| !format.is_empty())
                    .map(str::to_owned)
                    .collect();
                if let Some(unknown) = formats
                    .iter()
                    .find(|format| !FORMAT_KEYS.contains(&format.as_str()))
                {
                    return Err(format!(
                        "--formats: unknown format `{unknown}` (expected {})",
                        FORMAT_KEYS.join(",")
                    ));
                }
                if formats.is_empty() {
                    return Err("--formats requires at least one format".into());
                }
                args.formats = Some(formats);
            }
            "--speakers" if value == "auto" => args.num_speakers = Some(None),
            "--speakers" => match value.parse::<u32>() {
                Ok(count) if count > 0 => args.num_speakers = Some(Some(count)),
                _ => {
                    return Err(format!(
                        "--speakers must be auto or a positive number, got `{value}`"
                    ))
                }
            },
            "--diarization-backend"
                if matches!(value.as_str(), "pyannote" | "onnx" | "sortformer") =>
            {
                args.diarization_backend = Some(value)
            }
            "--diarization-backend" => {
                return Err(format!(
                    "--diarization-backend must be pyannote|onnx|sortformer, got `{value}`"
                ))
            }
            "--backend" if backend_is_supported(&value.to_ascii_lowercase()) => {
                args.backend = Some(value.to_ascii_lowercase())
            }
            "--backend" => {
                return Err(format!(
                    "--backend must be one of {}, got `{value}`",
                    selectable_backends().join("|")
                ))
            }
            "--model" if MODEL_OPTIONS.iter().any(|(id, _)| *id == value) => {
                args.model = Some(value)
            }
            "--model" => {
                return Err(format!(
                    "--model must be one of {}, got `{value}`",
                    MODEL_OPTIONS
                        .iter()
                        .map(|(id, _)| *id)
                        .collect::<Vec<_>>()
                        .join("|")
                ))
            }
            "--audio-mode" if matches!(value.as_str(), "auto" | "off" | "light" | "denoise") => {
                args.audio_mode = Some(value)
            }
            _ => {
                return Err(format!(
                    "--audio-mode must be auto|off|light|denoise, got `{value}`"
                ))
            }
        }
    }
    Ok(HeadlessCommand::Transcribe(args))
}

/// The same `start` command the TUI sends, from persisted settings plus flag overrides.
fn headless_start_payload(settings: &TuiSettings, args: &TranscribeArgs) -> Value {
    let backend = args
        .backend
        .clone()
        .or_else(|| backend_is_supported(&settings.backend).then(|| settings.backend.clone()))
        .unwrap_or_else(|| "auto".into());
    let model = args
        .model
        .clone()
        .or_else(|| {
            MODEL_OPTIONS
                .iter()
                .any(|(id, _)| *id == settings.model)
                .then(|| settings.model.clone())
        })
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

fn headless_llm_payload(settings: &TuiSettings, args: &LlmArgs) -> Value {
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
fn format_headless_line(event: &Value) -> Option<String> {
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

/// The worker runs with cwd = the repo checkout, so every path must be made
/// absolute against the caller's cwd before it is sent. Output directories are
/// created here (as `/output` does in the TUI) and canonicalised.
fn resolve_headless_paths(command: &mut HeadlessCommand) -> Result<(), String> {
    let (files, output_dir) = match command {
        HeadlessCommand::Transcribe(args) => (&mut args.files, &mut args.output_dir),
        HeadlessCommand::Llm(args) => (&mut args.files, &mut args.output_dir),
    };
    for file in files.iter_mut() {
        *file = normalize_path(file).map_err(|error| error.message(Lang::En))?;
    }
    if let Some(directory) = output_dir.as_mut() {
        fs::create_dir_all(&*directory)
            .map_err(|error| format!("cannot create output directory {directory}: {error}"))?;
        let canonical = fs::canonicalize(&*directory)
            .map_err(|error| format!("cannot resolve output directory {directory}: {error}"))?;
        *directory = canonical.to_string_lossy().into_owned();
    }
    Ok(())
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
    let worker_stderr = if json_output || quiet {
        Stdio::null()
    } else {
        Stdio::inherit()
    };
    let (mut child, mut worker, events) = match spawn_worker_with(worker_stderr) {
        Ok(parts) => parts,
        Err(error) => {
            eprintln!("gigaam: worker unavailable: {error} (try `gigaam --update`)");
            return Ok(3);
        }
    };
    if let Err(error) = send(&mut worker, payload) {
        eprintln!("gigaam: worker unavailable: {error} (try `gigaam --update`)");
        let _ = child.kill();
        return Ok(3);
    }
    let result = headless_event_loop(&events, json_output, quiet, started_type, terminal_type);
    let _ = child.kill();
    let _ = child.wait();
    match result {
        Ok(code) => Ok(code),
        // `gigaam transcribe … | head -1`: the reader went away; nothing to report.
        Err((code, error)) if error.kind() == io::ErrorKind::BrokenPipe => Ok(code),
        Err((_, error)) => Err(error),
    }
}

/// Consumes worker events until the terminal one; on a write failure returns the
/// exit code computed so far together with the error.
fn headless_event_loop(
    events: &Receiver<Value>,
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
    loop {
        let event = match events.recv_timeout(Duration::from_secs(3600)) {
            Ok(event) => event,
            Err(mpsc::RecvTimeoutError::Timeout) => {
                eprintln!("gigaam: worker stopped responding");
                break;
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
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

#[cfg(test)]
mod tests {
    use std::collections::HashMap;

    use super::*;

    #[test]
    fn data_directory_argument_accepts_separate_and_equals_forms() {
        assert_eq!(
            data_dir_from_args(["gigaam", "--data-dir", "/mnt/models"]),
            Ok(Some("/mnt/models".into()))
        );
        assert_eq!(
            data_dir_from_args(["gigaam", "--data-dir=/srv/gigaam"]),
            Ok(Some("/srv/gigaam".into()))
        );
        assert!(data_dir_from_args(["gigaam", "--data-dir"]).is_err());
        assert!(data_dir_from_args(["gigaam", "--data-dir", "--help"]).is_err());
    }

    #[test]
    fn strip_data_dir_removes_both_argument_forms() {
        let args = |list: &[&str]| list.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert_eq!(
            strip_data_dir(args(&["--data-dir", "/x", "transcribe", "a.wav"])),
            args(&["transcribe", "a.wav"])
        );
        assert_eq!(
            strip_data_dir(args(&["transcribe", "--data-dir=/x", "a.wav"])),
            args(&["transcribe", "a.wav"])
        );
    }

    #[test]
    fn headless_transcribe_args_override_settings() {
        let args: Vec<String> = [
            "transcribe",
            "/tmp/a.wav",
            "/tmp/b.mp3",
            "--formats",
            "txt,srt",
            "--diarize",
            "--speakers",
            "2",
            "--backend",
            "onnx",
            "--audio-mode",
            "denoise",
            "--output",
            "/tmp/out",
            "--json",
        ]
        .iter()
        .map(|s| s.to_string())
        .collect();
        let HeadlessCommand::Transcribe(parsed) = parse_headless_args(&args).unwrap() else {
            panic!("transcribe")
        };
        let settings = TuiSettings {
            backend: "mlx".into(),
            model: "multilingual_ctc".into(),
            ..TuiSettings::default()
        };
        let payload = headless_start_payload(&settings, &parsed);
        assert_eq!(payload["type"], "start");
        assert_eq!(payload["files"], json!(["/tmp/a.wav", "/tmp/b.mp3"]));
        assert_eq!(payload["formats"], json!(["txt", "srt"]));
        assert_eq!(payload["diarization"], true);
        assert_eq!(payload["num_speakers"], 2);
        assert_eq!(payload["backend"], "onnx", "flag overrides settings");
        assert_eq!(
            payload["model"], "multilingual_ctc",
            "settings fill what flags omit"
        );
        assert_eq!(payload["audio_preprocessing_mode"], "denoise");
        assert_eq!(payload["output_dir"], "/tmp/out");
        assert!(parsed.json);
    }

    #[test]
    fn headless_args_reject_unknown_flags_and_missing_files() {
        let args = |list: &[&str]| list.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert!(parse_headless_args(&args(&["transcribe"]))
            .unwrap_err()
            .contains("at least one file"));
        assert!(
            parse_headless_args(&args(&["transcribe", "a.wav", "--bogus"]))
                .unwrap_err()
                .contains("--bogus")
        );
        assert!(
            parse_headless_args(&args(&["transcribe", "a.wav", "--audio-mode", "loud"]))
                .unwrap_err()
                .contains("audio-mode")
        );
        assert!(parse_headless_args(&args(&["llm", "a.txt"]))
            .unwrap_err()
            .contains("--mode"));
        assert!(
            parse_headless_args(&args(&["llm", "a.txt", "--mode", "custom"]))
                .unwrap_err()
                .contains("--prompt")
        );
        assert!(
            parse_headless_args(&args(&["--data-dir", "/x"])).is_err(),
            "not a headless command"
        );
    }

    #[test]
    fn headless_llm_payload_uses_saved_provider_settings() {
        let args = |list: &[&str]| list.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        let HeadlessCommand::Llm(parsed) = parse_headless_args(&args(&[
            "llm",
            "/tmp/a.txt",
            "--mode",
            "summary",
            "--mode",
            "custom",
            "--prompt",
            "Why?",
        ]))
        .unwrap() else {
            panic!("llm")
        };
        let settings = TuiSettings {
            llm_provider: "Other".into(),
            llm_tool_paths: HashMap::from([("other".to_string(), "/bin/cat".to_string())]),
            llm_extra_args: HashMap::from([("other".to_string(), "-".to_string())]),
            ..TuiSettings::default()
        };
        let payload = headless_llm_payload(&settings, &parsed);
        assert_eq!(payload["type"], "llm_start");
        assert_eq!(payload["files"], json!(["/tmp/a.txt"]));
        assert_eq!(payload["modes"], json!(["summary", "custom"]));
        assert_eq!(payload["prompt"], "Why?");
        assert_eq!(payload["settings"]["provider"], "Other");
        assert_eq!(payload["settings"]["other_path"], "/bin/cat");
        assert_eq!(payload["settings"]["other_args"], "-");
        assert_eq!(payload["settings"]["claude_path"], "claude");
    }

    #[test]
    fn headless_human_lines_name_saved_files_and_errors() {
        let done = json!({"type":"file_completed","file":"/tmp/a.wav","result":{"success":true,"saved_files":["/tmp/a.txt","/tmp/a.srt"]}});
        assert_eq!(
            format_headless_line(&done).unwrap(),
            "✓ a.wav → /tmp/a.txt, /tmp/a.srt"
        );
        let failed = json!({"type":"file_completed","file":"/tmp/b.mp3","result":{"success":false,"error":"boom","saved_files":[]}});
        assert_eq!(format_headless_line(&failed).unwrap(), "× b.mp3: boom");
        let error = json!({"type":"error","message":"Input file does not exist: /tmp/c.wav"});
        assert_eq!(
            format_headless_line(&error).unwrap(),
            "error: Input file does not exist: /tmp/c.wav"
        );
        assert!(format_headless_line(&json!({"type":"progress","stage":"asr"})).is_none());
    }

    #[test]
    fn headless_paths_become_absolute_before_the_worker_sees_them() {
        // The worker runs with cwd = the repo checkout, so relative paths must be
        // resolved by the binary against the caller's cwd.
        let directory =
            std::env::temp_dir().join(format!("gigaam-headless-paths-{}", std::process::id()));
        let _ = fs::remove_dir_all(&directory);
        fs::create_dir_all(&directory).unwrap();
        fs::write(directory.join("a.wav"), b"").unwrap();
        let args = |list: &[&str]| list.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        let relative_file = format!("{}/./a.wav", directory.display());
        let relative_out = format!("{}/./out", directory.display());
        let mut command = parse_headless_args(&args(&[
            "transcribe",
            &relative_file,
            "--output",
            &relative_out,
        ]))
        .unwrap();
        resolve_headless_paths(&mut command).unwrap();
        let HeadlessCommand::Transcribe(parsed) = &command else {
            panic!("transcribe")
        };
        let canonical = fs::canonicalize(&directory).unwrap();
        assert_eq!(
            parsed.files,
            vec![canonical.join("a.wav").to_string_lossy().into_owned()]
        );
        assert_eq!(
            parsed.output_dir.as_deref(),
            Some(canonical.join("out").to_string_lossy().as_ref()),
            "output directory is created and canonicalised"
        );
        let mut llm =
            parse_headless_args(&args(&["llm", &relative_file, "--mode", "summary"])).unwrap();
        resolve_headless_paths(&mut llm).unwrap();
        let HeadlessCommand::Llm(parsed) = &llm else {
            panic!("llm")
        };
        assert_eq!(
            parsed.files,
            vec![canonical.join("a.wav").to_string_lossy().into_owned()]
        );
        let mut missing = parse_headless_args(&args(&[
            "transcribe",
            &format!("{}/missing.wav", directory.display()),
        ]))
        .unwrap();
        assert!(resolve_headless_paths(&mut missing)
            .unwrap_err()
            .contains("missing.wav"));
        let _ = fs::remove_dir_all(&directory);
    }

    #[test]
    fn headless_prompt_requires_custom_mode() {
        let args = |list: &[&str]| list.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        let error = parse_headless_args(&args(&[
            "llm", "a.txt", "--mode", "summary", "--prompt", "x",
        ]))
        .unwrap_err();
        assert!(error.contains("--prompt requires --mode custom"), "{error}");
        assert!(parse_headless_args(&args(&[
            "llm", "a.txt", "--mode", "custom", "--prompt", "x"
        ]))
        .is_ok());
    }
}
