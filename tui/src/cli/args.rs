//! Command-line arguments: UTF-8 conversion, `--data-dir`, and the headless
//! `transcribe`/`llm` subcommands with their validation.

use std::{io, path::PathBuf};

use crate::{
    commands::{normalize_path, prepare_output_dir},
    i18n::Lang,
    options::{
        alternatives, backend_is_supported, is_llm_mode, is_model, llm_mode_ids, model_ids,
        selectable_backends, AUDIO_MODES, DIARIZATION_BACKENDS, FORMAT_KEYS,
    },
};

/// The arguments after the program name as UTF-8. `std::env::args()` panics on
/// the first one that is not (a file name from a non-UTF-8 directory on Linux);
/// that is a usage error with a readable message instead.
pub(crate) fn utf8_args(
    args: impl IntoIterator<Item = std::ffi::OsString>,
) -> Result<Vec<String>, String> {
    args.into_iter()
        .map(|arg| {
            arg.into_string()
                .map_err(|arg| format!("argument is not valid UTF-8: {}", arg.to_string_lossy()))
        })
        .collect()
}

/// `args` excludes the program name.
pub(super) fn data_dir_from_args<I, S>(args: I) -> Result<Option<String>, String>
where
    I: IntoIterator<Item = S>,
    S: AsRef<str>,
{
    let values: Vec<String> = args
        .into_iter()
        .map(|value| value.as_ref().to_string())
        .collect();
    for (index, value) in values.iter().enumerate() {
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

pub(crate) fn apply_data_dir_argument(args: &[String]) -> io::Result<()> {
    let Some(root) = data_dir_from_args(args)
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
pub(super) enum HeadlessCommand {
    Transcribe(TranscribeArgs),
    Llm(LlmArgs),
}

#[derive(Debug, Default)]
pub(super) struct TranscribeArgs {
    pub(super) files: Vec<String>,
    pub(super) output_dir: Option<String>,
    pub(super) formats: Option<Vec<String>>,
    pub(super) diarize: Option<bool>,
    /// `None`: not given; `Some(None)`: `auto`; `Some(Some(n))`: fixed count.
    pub(super) num_speakers: Option<Option<u32>>,
    pub(super) diarization_backend: Option<String>,
    pub(super) backend: Option<String>,
    pub(super) model: Option<String>,
    pub(super) audio_mode: Option<String>,
    pub(super) json: bool,
    pub(super) quiet: bool,
}

#[derive(Debug, Default)]
pub(super) struct LlmArgs {
    pub(super) files: Vec<String>,
    pub(super) modes: Vec<String>,
    pub(super) prompt: String,
    pub(super) output_dir: Option<String>,
    pub(super) json: bool,
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

pub(super) fn parse_headless_args(argv: &[String]) -> Result<HeadlessCommand, String> {
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
                "--mode" if is_llm_mode(&value) => {
                    if !args.modes.contains(&value) {
                        args.modes.push(value);
                    }
                }
                "--mode" => {
                    return Err(format!(
                        "--mode must be {}, got `{value}`",
                        alternatives(&llm_mode_ids())
                    ))
                }
                "--prompt" => args.prompt = value,
                _ => args.output_dir = Some(value),
            }
        }
        if args.modes.is_empty() {
            return Err(format!(
                "llm needs at least one --mode ({})",
                alternatives(&llm_mode_ids())
            ));
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
            "--diarization-backend" if DIARIZATION_BACKENDS.contains(&value.as_str()) => {
                args.diarization_backend = Some(value)
            }
            "--diarization-backend" => {
                return Err(format!(
                    "--diarization-backend must be {}, got `{value}`",
                    alternatives(&DIARIZATION_BACKENDS)
                ))
            }
            "--backend" if backend_is_supported(&value.to_ascii_lowercase()) => {
                args.backend = Some(value.to_ascii_lowercase())
            }
            "--backend" => {
                return Err(format!(
                    "--backend must be one of {}, got `{value}`",
                    alternatives(selectable_backends())
                ))
            }
            "--model" if is_model(&value) => args.model = Some(value),
            "--model" => {
                return Err(format!(
                    "--model must be one of {}, got `{value}`",
                    alternatives(&model_ids())
                ))
            }
            "--audio-mode" if AUDIO_MODES.contains(&value.as_str()) => {
                args.audio_mode = Some(value)
            }
            _ => {
                return Err(format!(
                    "--audio-mode must be {}, got `{value}`",
                    alternatives(&AUDIO_MODES)
                ))
            }
        }
    }
    Ok(HeadlessCommand::Transcribe(args))
}

/// The worker runs with cwd = the repo checkout, so every path must be made
/// absolute against the caller's cwd before it is sent. Output directories are
/// created here (as `/output` does in the TUI) and canonicalised.
pub(super) fn resolve_headless_paths(command: &mut HeadlessCommand) -> Result<(), String> {
    let (files, output_dir) = match command {
        HeadlessCommand::Transcribe(args) => (&mut args.files, &mut args.output_dir),
        HeadlessCommand::Llm(args) => (&mut args.files, &mut args.output_dir),
    };
    for file in files.iter_mut() {
        *file = normalize_path(file).map_err(|error| error.message(Lang::En))?;
    }
    if let Some(directory) = output_dir.as_mut() {
        *directory = prepare_output_dir(directory, Lang::En)
            .map_err(|error| format!("cannot create output directory {directory}: {error}"))?;
    }
    Ok(())
}
