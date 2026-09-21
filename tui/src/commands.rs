//! Slash commands, their menus and the path helpers behind the input line.

use std::{
    fs,
    path::{Path, PathBuf},
};

use crate::{
    app::{llm_input_files, request_llm, App},
    i18n::{t, Lang},
    settings::save_app_settings,
    worker::{provider_from_menu_option, provider_menu_options, provider_prefix},
};

pub(crate) const FORMAT_KEYS: [&str; 7] = [
    "txt",
    "txt_timecodes",
    "txt_diarize",
    "txt_diarize_timecodes",
    "md",
    "srt",
    "vtt",
];

pub(crate) fn short_name(path: &str) -> String {
    path.rsplit(['/', '\\']).next().unwrap_or(path).to_string()
}
pub(crate) fn normalize_path(raw: &str) -> Result<String, String> {
    let mut text = raw.trim().trim_matches(['\'', '"']).trim().to_string();
    if let Some(path) = text.strip_prefix("file://") {
        text = path.replace("%20", " ");
    }
    if let Some(path) = text.strip_prefix("~/") {
        let home = std::env::var("HOME").map_err(|_| "HOME is not set".to_string())?;
        text = format!("{home}/{path}");
    }
    let path = fs::canonicalize(&text).map_err(|_| format!("File does not exist: {text}"))?;
    if !path.is_file() {
        return Err(format!("Not a file: {}", path.display()));
    }
    Ok(path.to_string_lossy().into_owned())
}

pub(crate) fn split_shell_paths(raw: &str) -> Vec<String> {
    let mut paths = Vec::new();
    let mut current = String::new();
    let mut quote = None;
    let mut escaped = false;
    for character in raw.chars() {
        if escaped {
            current.push(character);
            escaped = false;
        } else if character == '\\' {
            escaped = true;
        } else if matches!(character, '\'' | '"') {
            if quote == Some(character) {
                quote = None;
            } else if quote.is_none() {
                quote = Some(character);
            } else {
                current.push(character);
            }
        } else if character.is_whitespace() && quote.is_none() {
            if !current.is_empty() {
                paths.push(std::mem::take(&mut current));
            }
        } else {
            current.push(character);
        }
    }
    if escaped {
        current.push('\\');
    }
    if !current.is_empty() {
        paths.push(current);
    }
    paths
}

pub(crate) fn queue_paths(app: &mut App, raw: &str) {
    let lines: Vec<String> = raw
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .map(str::to_owned)
        .collect();
    let candidates = if lines.len() > 1 {
        lines
    } else {
        let value = lines.first().map(String::as_str).unwrap_or(raw).trim();
        match normalize_path(value) {
            Ok(path) => vec![path],
            Err(error) => {
                let split = split_shell_paths(value);
                if split.len() == 1 && split.first().is_some_and(|path| path != value) {
                    split
                } else if split.len() > 1 {
                    split
                } else {
                    app.status = error;
                    return;
                }
            }
        }
    };

    let mut queued = 0;
    let mut errors = Vec::new();
    for candidate in candidates {
        match normalize_path(&candidate) {
            Ok(path) => {
                app.files.push(path);
                queued += 1;
            }
            Err(error) => errors.push(error),
        }
    }
    if queued == 0 {
        app.status = errors
            .into_iter()
            .next()
            .unwrap_or_else(|| "No input files supplied".into());
        return;
    }
    app.selected_file = app.files.len().checked_sub(1);
    app.input.clear();
    app.status = if errors.is_empty() {
        format!("Queued {queued} file{}", if queued == 1 { "" } else { "s" })
    } else {
        format!(
            "Queued {queued} file{} · {} skipped (see log)",
            if queued == 1 { "" } else { "s" },
            errors.len()
        )
    };
    app.log(app.status.clone());
    for error in errors {
        app.log(error);
    }
}

pub(crate) fn complete_path(raw: &str) -> Option<String> {
    let text = raw.trim().trim_matches(['\'', '"']);
    if text.is_empty() || text.starts_with('/') && is_command(text) {
        return None;
    }
    let expanded = if let Some(path) = text.strip_prefix("~/") {
        format!("{}/{}", std::env::var("HOME").ok()?, path)
    } else {
        text.to_string()
    };
    let path = Path::new(&expanded);
    let (parent, prefix) = if expanded.ends_with('/') {
        (PathBuf::from(&expanded), "")
    } else {
        let parent = path
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
            .unwrap_or_else(|| Path::new("."));
        (parent.to_path_buf(), path.file_name()?.to_str()?)
    };
    let mut matches: Vec<PathBuf> = fs::read_dir(parent)
        .ok()?
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| {
            path.file_name()
                .and_then(|name| name.to_str())
                .is_some_and(|name| name.starts_with(prefix))
        })
        .collect();
    matches.sort();
    let candidate = matches.first()?;
    let mut completed = candidate.to_string_lossy().into_owned();
    if candidate.is_dir() {
        completed.push('/');
    }
    Some(completed)
}

pub(crate) const BACK_MENU_OPTION: &str = "← Back";

pub(crate) const MODEL_OPTIONS: [(&str, &str); 3] = [
    ("v3_e2e_rnnt", "GigaAM v3 e2e RNNT (current)"),
    ("multilingual_ctc", "Multilingual CTC (220M)"),
    ("multilingual_large_ctc", "Multilingual Large CTC (600M)"),
];

pub(crate) const COMMANDS: [(&str, &str); 31] = [
    ("/output", "set the results directory"),
    ("/backend", "select the ASR runtime"),
    ("/onnx-provider", "select the ONNX execution provider"),
    ("/model", "select the GigaAM recognition model"),
    ("/formats", "output formats, e.g. txt,srt"),
    ("/subtitle-split", "turn sentence splitting on or off"),
    ("/subtitle-lines", "set 1-4 lines per subtitle cue"),
    ("/subtitle-width", "set 20-100 characters per subtitle line"),
    ("/diarize", "turn speaker diarization on or off"),
    (
        "/audio-mode",
        "audio preprocessing: auto, off, light, denoise",
    ),
    (
        "/diarization-backend",
        "select ONNX, pyannote, or NVIDIA Sortformer",
    ),
    ("/speakers", "auto or a fixed speaker count"),
    ("/remove", "remove a file from the queue by number"),
    ("/clear", "clear the queue and result list"),
    (
        "/llm-file",
        "add a transcript file (.txt/.md/.srt/.vtt) for the LLM",
    ),
    ("/settings", "show current processing settings"),
    ("/pets", "toggle the animated unicorn companion"),
    ("/llm-mode", "summary, tasks, terms, or custom"),
    ("/llm-prompt", "set a custom LLM prompt"),
    ("/llm-run", "run LLM processing"),
    ("/llm-api-url", "set LLM API URL"),
    ("/llm-api-key", "set LLM API key"),
    ("/llm-model", "set LLM model"),
    ("/llm-temperature", "set LLM temperature"),
    (
        "/llm-provider-name",
        "Pi/oh-my-pi internal provider, e.g. anthropic",
    ),
    (
        "/llm-args",
        "extra CLI arguments for the current LLM provider",
    ),
    ("/llm-path", "path to the current provider's CLI binary"),
    (
        "/llm-tools",
        "on|off · let the CLI agent use tools and sessions",
    ),
    ("/lang", "interface language: ru or en"),
    ("/mouse", "mouse support: on or off"),
    ("/exit", "exit the terminal UI"),
];

pub(crate) fn selectable_backends() -> &'static [&'static str] {
    #[cfg(target_os = "macos")]
    {
        &["auto", "pytorch", "mlx", "onnx"]
    }
    #[cfg(not(target_os = "macos"))]
    {
        &["auto", "pytorch", "onnx"]
    }
}

pub(crate) fn backend_is_supported(backend: &str) -> bool {
    #[cfg(target_os = "macos")]
    {
        matches!(backend, "auto" | "pytorch" | "mlx" | "onnx")
    }
    #[cfg(not(target_os = "macos"))]
    {
        matches!(backend, "auto" | "pytorch" | "onnx")
    }
}

pub(crate) fn backend_usage() -> &'static str {
    #[cfg(target_os = "macos")]
    {
        "Usage: /backend auto|pytorch|mlx|onnx"
    }
    #[cfg(not(target_os = "macos"))]
    {
        "Usage: /backend auto|pytorch|onnx"
    }
}

pub(crate) fn command_suggestions(input: &str) -> Vec<(&'static str, &'static str)> {
    let command = input
        .trim_start()
        .split_whitespace()
        .next()
        .unwrap_or_default();
    if !command.starts_with('/') {
        return Vec::new();
    }
    COMMANDS
        .into_iter()
        .filter(|(name, _)| name.starts_with(command))
        .collect()
}

pub(crate) fn is_command(input: &str) -> bool {
    !command_suggestions(input).is_empty()
}

pub(crate) fn accept_command_suggestion(app: &mut App, command: &str) {
    if open_command_menu(app, command) {
        app.selected_command = 0;
        return;
    }
    app.input = command.into();
    if matches!(command, "/clear" | "/pets") {
        run_command(app);
    } else {
        app.input.push(' ');
    }
    app.selected_command = 0;
}

pub(crate) fn command_menu_options(app: &App) -> Vec<String> {
    match app.command_menu.as_deref() {
        Some("/backend") => selectable_backends()
            .iter()
            .map(|backend| (*backend).to_owned())
            .chain(std::iter::once(BACK_MENU_OPTION.to_owned()))
            .collect(),
        Some("/onnx-provider") => [
            "auto",
            "cpu",
            "cuda",
            "tensorrt",
            "coreml",
            "directml",
            BACK_MENU_OPTION,
        ]
        .into_iter()
        .map(str::to_owned)
        .collect(),
        Some("/model") => MODEL_OPTIONS
            .iter()
            .map(|(id, label)| format!("{id} · {label}"))
            .chain(std::iter::once(BACK_MENU_OPTION.to_owned()))
            .collect(),
        Some("/diarize") => ["on", "off", BACK_MENU_OPTION]
            .into_iter()
            .map(str::to_owned)
            .collect(),
        Some("/audio-mode") => ["auto", "off", "light", "denoise", BACK_MENU_OPTION]
            .into_iter()
            .map(str::to_owned)
            .collect(),
        Some("/diarization-backend") => ["pyannote", "onnx", "sortformer", BACK_MENU_OPTION]
            .into_iter()
            .map(str::to_owned)
            .collect(),
        Some("/speakers") => [
            "auto",
            "1",
            "2",
            "3",
            "4",
            "5",
            "6",
            "7",
            "8",
            BACK_MENU_OPTION,
        ]
        .into_iter()
        .map(str::to_owned)
        .collect(),
        Some("/settings") => vec![
            format!("LLM provider · {}", app.llm_provider),
            format!(
                "API URL · {}",
                if app.llm_api_url.is_empty() {
                    "not set"
                } else {
                    "configured"
                }
            ),
            format!(
                "API key · {}",
                if app.llm_api_key.is_empty() {
                    "not set"
                } else {
                    "configured"
                }
            ),
            format!(
                "LLM model · {}",
                if app.llm_model.is_empty() {
                    "not set"
                } else {
                    &app.llm_model
                }
            ),
            format!("Temperature · {}", app.llm_temperature),
            format!(
                "Binary path · {}",
                app.llm_tool_paths
                    .get(provider_prefix(&app.llm_provider))
                    .map_or("auto", String::as_str)
            ),
            format!(
                "Internal provider · {}",
                app.llm_internal_providers
                    .get(provider_prefix(&app.llm_provider))
                    .map_or("default", String::as_str)
            ),
            format!(
                "Extra args · {}",
                app.llm_extra_args
                    .get(provider_prefix(&app.llm_provider))
                    .map_or("none", String::as_str)
            ),
            format!(
                "Agent tools · {}",
                if app.llm_allow_tools { "on" } else { "off" }
            ),
            BACK_MENU_OPTION.into(),
        ],
        Some("/settings-provider") => provider_menu_options(app),
        Some("/settings-model") => llm_model_options(&app.llm_provider),
        Some("/llm-mode") => ["summary", "tasks", "terms", "custom"]
            .into_iter()
            .map(|mode| {
                format!(
                    "[{}] {mode}",
                    if app.llm_modes.iter().any(|item| item == mode) {
                        "x"
                    } else {
                        " "
                    }
                )
            })
            .chain(std::iter::once(BACK_MENU_OPTION.to_owned()))
            .collect(),
        Some("/formats") => [
            "txt",
            "txt_timecodes",
            "txt_diarize",
            "txt_diarize_timecodes",
            "md",
            "srt",
            "vtt",
        ]
        .into_iter()
        .map(|format| {
            format!(
                "[{}] {format}",
                if app.formats.iter().any(|selected| selected == format) {
                    "x"
                } else {
                    " "
                }
            )
        })
        .chain(std::iter::once(BACK_MENU_OPTION.to_owned()))
        .collect(),
        _ => Vec::new(),
    }
}

fn llm_model_options(provider: &str) -> Vec<String> {
    let models: &[&str] = match provider {
        "Claude Code" => &["default", "sonnet", "opus", "haiku"],
        // Codex with a ChatGPT account rejects explicit `-m` values such as
        // gpt-5-codex. Let the installed Codex client choose its supported model.
        "Codex" => &["default"],
        "OpenCode" => &["default"],
        "Pi" => &["default"],
        "oh-my-pi" => &["default"],
        "Other" => &["default"],
        _ => &["gpt-4.1-mini", "gpt-4.1", "gpt-5-mini", "gpt-5"],
    };
    models
        .iter()
        .map(|model| (*model).to_owned())
        .chain(["Enter manually".to_owned(), BACK_MENU_OPTION.to_owned()])
        .collect()
}

pub(crate) fn open_command_menu(app: &mut App, command: &str) -> bool {
    if matches!(
        command,
        "/backend"
            | "/onnx-provider"
            | "/model"
            | "/diarize"
            | "/diarization-backend"
            | "/audio-mode"
            | "/formats"
            | "/speakers"
            | "/llm-mode"
            | "/settings"
            | "/settings-provider"
            | "/settings-model"
    ) {
        app.command_menu = Some(command.to_owned());
        app.command_menu_index = if command == "/backend" {
            command_menu_options(app)
                .iter()
                .position(|option| option == &app.backend)
                .unwrap_or(0)
        } else if command == "/onnx-provider" {
            command_menu_options(app)
                .iter()
                .position(|option| option == &app.onnx_provider)
                .unwrap_or(0)
        } else if command == "/audio-mode" {
            command_menu_options(app)
                .iter()
                .position(|option| option == &app.audio_preprocessing_mode)
                .unwrap_or(0)
        } else {
            0
        };
        app.status = "Choose 1–9, 0 for Back, or arrows and Enter".into();
        true
    } else {
        false
    }
}

pub(crate) fn apply_command_menu(app: &mut App) {
    let options = command_menu_options(app);
    let Some(option) = options.get(app.command_menu_index.min(options.len().saturating_sub(1)))
    else {
        return;
    };
    if option == BACK_MENU_OPTION {
        app.command_menu = None;
        app.input.clear();
        app.status = "Settings menu closed".into();
        app.log(app.status.clone());
        return;
    }
    let command = app.command_menu.clone().unwrap_or_default();
    match command.as_str() {
        "/backend" => {
            app.backend = option.clone();
            app.status = format!("Backend: {}", app.backend);
            app.command_menu = None;
            app.input.clear();
            save_app_settings(app);
        }
        "/onnx-provider" => {
            app.onnx_provider = option.clone();
            app.status = format!("ONNX provider: {}", app.onnx_provider);
            app.command_menu = None;
            app.input.clear();
            save_app_settings(app);
        }
        "/model" => {
            app.model = option
                .split_whitespace()
                .next()
                .unwrap_or("v3_e2e_rnnt")
                .into();
            app.status = format!("Model: {}", app.model);
            app.command_menu = None;
            app.input.clear();
            save_app_settings(app);
        }
        "/settings" => {
            app.command_menu = None;
            app.input = match app.command_menu_index {
                0 => {
                    app.command_menu = Some("/settings-provider".into());
                    app.status = "Choose LLM provider".into();
                    return;
                }
                1 => "/llm-api-url ".into(),
                2 => "/llm-api-key ".into(),
                3 => {
                    app.command_menu = Some("/settings-model".into());
                    app.status = "Choose LLM model".into();
                    return;
                }
                4 => "/llm-temperature ".into(),
                5 => "/llm-path ".into(),
                6 => "/llm-provider-name ".into(),
                7 => "/llm-args ".into(),
                8 => {
                    app.llm_allow_tools = !app.llm_allow_tools;
                    save_app_settings(app);
                    app.command_menu = Some("/settings".into());
                    app.command_menu_index = 8;
                    app.status = format!(
                        "Agent tools {}",
                        if app.llm_allow_tools { "on" } else { "off" }
                    );
                    return;
                }
                _ => String::new(),
            };
            app.status = "Enter value and press Enter".into();
        }
        "/settings-provider" => {
            app.llm_provider = provider_from_menu_option(option).to_owned();
            app.command_menu = Some("/settings".into());
            app.command_menu_index = 0;
            app.status = format!("LLM provider: {}", app.llm_provider);
            save_app_settings(app);
        }
        "/settings-model" if option == "Enter manually" => {
            app.command_menu = None;
            app.input = "/llm-model ".into();
            app.status = "Enter model name and press Enter".into();
        }
        "/settings-model" => {
            app.llm_model = if option == "default" {
                String::new()
            } else {
                option.clone()
            };
            app.command_menu = Some("/settings".into());
            app.command_menu_index = 0;
            app.status = if app.llm_model.is_empty() {
                "LLM default model selected".into()
            } else {
                format!("LLM model: {}", app.llm_model)
            };
            save_app_settings(app);
        }
        "/llm-mode" => {
            let mode = option
                .trim_start_matches(|c: char| c == '[' || c == 'x' || c == ' ' || c == ']')
                .trim();
            if let Some(index) = app.llm_modes.iter().position(|item| item == mode) {
                app.llm_modes.remove(index);
            } else {
                app.llm_modes.push(mode.into());
            }
            app.status = format!("LLM modes: {}", app.llm_modes.join(", "));
        }
        "/diarize" => {
            app.diarization = option == "on";
            app.status = format!("Diarization {option}");
            app.command_menu = None;
            app.input.clear();
            save_app_settings(app);
        }
        "/audio-mode" => {
            app.audio_preprocessing_mode = option.clone();
            app.status = format!("Audio preprocessing: {option}");
            app.command_menu = None;
            app.input.clear();
            save_app_settings(app);
        }
        "/diarization-backend" => {
            app.diarization_backend = option.clone();
            if app.diarization_backend == "sortformer" {
                app.num_speakers = None;
            }
            app.status = format!("Diarization backend: {}", app.diarization_backend);
            app.command_menu = None;
            app.input.clear();
            save_app_settings(app);
        }
        "/speakers" if app.diarization_backend == "sortformer" => {
            app.num_speakers = None;
            app.status = "Sortformer detects the speaker count automatically".into();
            app.command_menu = None;
            app.input.clear();
            save_app_settings(app);
        }
        "/speakers" => {
            app.num_speakers = option.parse().ok();
            app.status = format!("Speaker count: {option}");
            app.command_menu = None;
            app.input.clear();
            save_app_settings(app);
        }
        "/formats" => {
            let format = option.trim_start_matches("[x] ").trim_start_matches("[ ] ");
            if let Some(index) = app.formats.iter().position(|selected| selected == format) {
                app.formats.remove(index);
            } else {
                app.formats.push(format.to_owned());
            }
            if app.formats.is_empty() {
                app.formats.push("txt".into());
            }
            app.status = format!("Formats: {}", app.formats.join(", "));
            save_app_settings(app);
        }
        _ => {}
    }
    app.log(app.status.clone());
}

pub(crate) fn run_command(app: &mut App) {
    let command = app.input.trim().to_owned();
    let mut parts = command.splitn(2, char::is_whitespace);
    // Pasted commands often contain a typographic dash (–/—/−) instead of
    // ASCII `-`; accept them so settings commands remain usable.
    let name = parts
        .next()
        .unwrap_or_default()
        .replace(['–', '—', '−'], "-");
    let argument = parts.next().unwrap_or_default().trim();
    match name.as_str() {
        "/exit" => {
            app.exit_requested = true;
            app.status = "Exiting…".into();
        }
        "/settings" => {
            let _ = open_command_menu(app, "/settings");
        }
        "/lang" => match Lang::parse(argument) {
            Some(lang) => {
                app.lang = lang;
                app.status = t(lang, "settings.language_changed").into();
                save_app_settings(app);
            }
            None => app.status = t(app.lang, "usage.lang").into(),
        },
        "/mouse" => match argument {
            "on" | "off" => {
                app.mouse_enabled = argument == "on";
                app.status = t(
                    app.lang,
                    if app.mouse_enabled {
                        "settings.mouse_on"
                    } else {
                        "settings.mouse_off"
                    },
                )
                .into();
                save_app_settings(app);
            }
            _ => app.status = t(app.lang, "usage.mouse").into(),
        },
        "/llm-mode" if matches!(argument, "summary" | "tasks" | "terms" | "custom") => {
            app.llm_modes = vec![argument.into()];
            app.status = format!("LLM modes: {}", app.llm_modes.join(", "));
        }
        "/llm-mode" => app.status = "Usage: /llm-mode summary|tasks|terms|custom".into(),
        "/llm-prompt" if !argument.is_empty() => {
            app.llm_prompt = argument.into();
            app.status = "Custom LLM prompt saved".into();
        }
        "/llm-prompt" => app.status = "Usage: /llm-prompt <instruction>".into(),
        "/llm-run" => request_llm(app),
        "/llm-file" => match normalize_path(argument) {
            Ok(path)
                if matches!(
                    Path::new(&path)
                        .extension()
                        .and_then(|value| value.to_str()),
                    Some("txt" | "md" | "srt" | "vtt")
                ) =>
            {
                app.llm_extra_files.push(path);
                app.status = format!("LLM inputs: {}", llm_input_files(app).len());
            }
            Ok(_) => app.status = "LLM input must be .txt, .md, .srt or .vtt".into(),
            Err(error) => app.status = error,
        },
        "/llm-api-url" if !argument.is_empty() => {
            app.llm_api_url = argument.into();
            app.status = "LLM API URL saved".into();
            save_app_settings(app);
        }
        "/llm-api-key" if !argument.is_empty() => {
            app.llm_api_key = argument.into();
            app.status = "LLM API key saved".into();
            save_app_settings(app);
        }
        "/llm-model" if !argument.is_empty() => {
            app.llm_model = argument.into();
            app.status = "LLM model saved".into();
            save_app_settings(app);
        }
        "/llm-model" => {
            let _ = open_command_menu(app, "/settings-model");
        }
        "/llm-temperature" => match argument.parse::<f64>() {
            Ok(value) if (0.0..=2.0).contains(&value) => {
                app.llm_temperature = value;
                app.status = "LLM temperature saved".into();
                save_app_settings(app);
            }
            _ => app.status = "Temperature must be between 0 and 2".into(),
        },
        "/llm-provider-name" if !matches!(provider_prefix(&app.llm_provider), "pi" | "omp") => {
            app.status = "Internal provider applies to Pi and oh-my-pi only".into();
        }
        "/llm-provider-name" => {
            let prefix = provider_prefix(&app.llm_provider).to_owned();
            if argument.is_empty() {
                app.llm_internal_providers.remove(&prefix);
                app.status = "Internal provider cleared (CLI default)".into();
            } else {
                app.llm_internal_providers.insert(prefix, argument.into());
                app.status = format!("Internal provider: {argument}");
            }
            save_app_settings(app);
        }
        "/llm-args" if app.llm_provider == "API" => {
            app.status = "Extra arguments apply to CLI providers only".into();
        }
        "/llm-args" => {
            let prefix = provider_prefix(&app.llm_provider).to_owned();
            if argument.is_empty() {
                app.llm_extra_args.remove(&prefix);
                app.status = "Extra arguments cleared".into();
            } else {
                app.llm_extra_args.insert(prefix, argument.into());
                app.status = format!("Extra arguments: {argument}");
            }
            save_app_settings(app);
        }
        "/llm-path" if app.llm_provider == "API" => {
            app.status = "Binary path applies to CLI providers only".into();
        }
        "/llm-path" => {
            let prefix = provider_prefix(&app.llm_provider).to_owned();
            if argument.is_empty() {
                app.llm_tool_paths.remove(&prefix);
                app.status = "Binary path reset to auto-discovery".into();
            } else {
                app.llm_tool_paths.insert(prefix, argument.into());
                app.status = format!("Binary path: {argument} · checking…");
            }
            if app.llm_provider != "Other" {
                app.llm_tool_check_requested = Some((app.llm_provider.clone(), argument.into()));
            }
            save_app_settings(app);
        }
        "/llm-tools" if matches!(argument, "on" | "off") => {
            app.llm_allow_tools = argument == "on";
            app.status = format!("Agent tools and sessions {argument}");
            save_app_settings(app);
        }
        "/llm-tools" => app.status = "Usage: /llm-tools on|off".into(),
        "/pets" => {
            if app.pet_enabled {
                app.clear_pet_layer();
                app.pet_enabled = false;
                app.pet_image = None;
                app.status = "Pets off".into();
                save_app_settings(app);
            } else if let Err(error) = app.refresh_pet_image() {
                app.status = error;
            } else {
                app.pet_enabled = true;
                app.pet_running = app.running;
                app.status = "Pets on · /pets to hide".into();
                save_app_settings(app);
            }
        }
        "/output" => {
            if argument.is_empty() {
                app.status = "Usage: /output <directory>".into();
            } else if let Err(error) = fs::create_dir_all(argument) {
                app.status = format!("Cannot create output directory: {error}");
            } else if let Ok(path) = fs::canonicalize(argument) {
                app.output_dir = Some(path.to_string_lossy().into_owned());
                app.status = "Output directory updated".into();
            }
        }
        "/backend" if backend_is_supported(&argument.to_ascii_lowercase()) => {
            app.backend = argument.to_ascii_lowercase();
            app.status = format!("Backend: {}", app.backend);
            save_app_settings(app);
        }
        "/backend" => app.status = backend_usage().into(),
        "/onnx-provider"
            if matches!(
                argument.to_ascii_lowercase().as_str(),
                "auto" | "cpu" | "cuda" | "tensorrt" | "coreml" | "directml"
            ) =>
        {
            app.onnx_provider = argument.to_ascii_lowercase();
            app.status = format!("ONNX provider: {}", app.onnx_provider);
            save_app_settings(app);
        }
        "/onnx-provider" => {
            app.status = "Usage: /onnx-provider auto|cpu|cuda|tensorrt|coreml|directml".into()
        }
        "/model" if MODEL_OPTIONS.iter().any(|(id, _)| *id == argument) => {
            app.model = argument.into();
            app.status = format!("Model: {}", app.model);
            save_app_settings(app);
        }
        "/model" => {
            app.status = "Usage: /model v3_e2e_rnnt|multilingual_ctc|multilingual_large_ctc".into()
        }
        "/formats" => {
            let formats: Vec<String> = argument
                .split(',')
                .map(str::trim)
                .filter(|format| {
                    matches!(
                        *format,
                        "txt"
                            | "txt_timecodes"
                            | "txt_diarize"
                            | "txt_diarize_timecodes"
                            | "md"
                            | "srt"
                            | "vtt"
                    )
                })
                .map(str::to_owned)
                .collect();
            if formats.is_empty() {
                app.status = "Usage: /formats txt,srt,md,vtt".into();
            } else {
                app.formats = formats;
                app.status = format!("Formats: {}", app.formats.join(", "));
                save_app_settings(app);
            }
        }
        "/subtitle-split" if matches!(argument, "on" | "off") => {
            app.subtitle_sentence_split = argument == "on";
            app.status = format!("Subtitle sentence splitting: {argument}");
            save_app_settings(app);
        }
        "/subtitle-split" => app.status = "Usage: /subtitle-split on|off".into(),
        "/subtitle-lines" => match argument.parse::<u8>() {
            Ok(value) if (1..=4).contains(&value) => {
                app.subtitle_max_lines = value;
                app.status = format!("Subtitle lines per cue: {value}");
                save_app_settings(app);
            }
            _ => app.status = "Subtitle lines must be between 1 and 4".into(),
        },
        "/subtitle-width" => match argument.parse::<u16>() {
            Ok(value) if (20..=100).contains(&value) => {
                app.subtitle_max_width = value;
                app.status = format!("Subtitle characters per line: {value}");
                save_app_settings(app);
            }
            _ => app.status = "Subtitle width must be between 20 and 100".into(),
        },
        "/diarize" if matches!(argument, "on" | "off") => {
            app.diarization = argument == "on";
            app.status = format!("Diarization {}", argument);
            save_app_settings(app);
        }
        "/diarize" => app.status = "Usage: /diarize on|off".into(),
        "/audio-mode" if matches!(argument, "auto" | "off" | "light" | "denoise") => {
            app.audio_preprocessing_mode = argument.into();
            app.status = format!("Audio preprocessing: {argument}");
            save_app_settings(app);
        }
        "/audio-mode" => app.status = "Usage: /audio-mode auto|off|light|denoise".into(),
        "/diarization-backend" if matches!(argument, "pyannote" | "onnx" | "sortformer") => {
            app.diarization_backend = argument.into();
            if app.diarization_backend == "sortformer" {
                app.num_speakers = None;
            }
            app.status = format!("Diarization backend: {}", app.diarization_backend);
            save_app_settings(app);
        }
        "/diarization-backend" => {
            app.status = "Usage: /diarization-backend pyannote|onnx|sortformer".into()
        }
        "/speakers" if app.diarization_backend == "sortformer" => {
            app.num_speakers = None;
            app.status = "Sortformer detects the speaker count automatically".into();
            save_app_settings(app);
        }
        "/speakers" if argument == "auto" => {
            app.num_speakers = None;
            app.status = "Speaker count: auto".into();
            save_app_settings(app);
        }
        "/speakers" => match argument.parse::<u32>() {
            Ok(value) if value > 0 => {
                app.num_speakers = Some(value);
                app.status = format!("Speaker count: {value}");
                save_app_settings(app);
            }
            _ => app.status = "Usage: /speakers auto|<positive number>".into(),
        },
        "/clear" => clear_queue(app),
        "/remove" => match argument.parse::<usize>() {
            Ok(index) if index > 0 && index <= app.files.len() => {
                let file = app.files.remove(index - 1);
                app.file_states.remove(&file);
                app.selected_file = app
                    .files
                    .get(index - 1)
                    .map(|_| index - 1)
                    .or_else(|| index.checked_sub(2));
                app.status = format!("Removed {}", short_name(&file));
            }
            _ => app.status = "Usage: /remove <queue number>".into(),
        },
        _ => app.status = format!("Unknown command: {name}"),
    }
    app.log(app.status.clone());
    app.input.clear();
}

pub(crate) fn clear_queue(app: &mut App) {
    app.files.clear();
    app.file_states.clear();
    app.selected_file = None;
    app.result_files.clear();
    app.llm_extra_files.clear();
    app.llm_results.clear();
    app.show_llm_result = false;
    app.status = "Queue cleared".into();
}

pub(crate) fn remove_selected_file(app: &mut App) {
    let Some(index) = app.selected_file.filter(|index| *index < app.files.len()) else {
        app.status = "No queued file selected".into();
        return;
    };
    let file = app.files.remove(index);
    app.file_states.remove(&file);
    app.selected_file = app
        .files
        .get(index)
        .map(|_| index)
        .or_else(|| index.checked_sub(1));
    app.status = format!("Removed {}", short_name(&file));
    app.log(app.status.clone());
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        app::llm_can_run,
        i18n::Lang,
        settings::{isolated_config_dir, load_settings, TuiSettings},
        worker::{llm_settings_payload, start_payload},
    };

    #[test]
    fn mouse_setting_persists_and_defaults_on() {
        let _config = isolated_config_dir();
        let mut app = App::default();
        assert!(app.mouse_enabled);
        app.input = "/mouse off".into();
        run_command(&mut app);
        assert!(!app.mouse_enabled);
        assert!(!load_settings().mouse);
    }

    #[test]
    fn lang_command_switches_and_persists() {
        let _config = isolated_config_dir();
        let mut app = App::default();
        app.input = "/lang en".into();
        run_command(&mut app);
        assert_eq!(app.lang, Lang::En);
        assert_eq!(app.status, "Language: English");
        assert_eq!(load_settings().language, "en");
        app.input = "/lang xx".into();
        run_command(&mut app);
        assert_eq!(app.status, "Usage: /lang ru|en");
        assert_eq!(app.lang, Lang::En);
    }

    #[test]
    fn shell_path_split_keeps_escaped_and_quoted_spaces() {
        assert_eq!(
            split_shell_paths(r#"/tmp/first\ file.mp3 "/tmp/second file.mp3""#),
            vec!["/tmp/first file.mp3", "/tmp/second file.mp3"]
        );
    }

    #[test]
    fn subtitle_commands_validate_and_update_limits() {
        let _config = isolated_config_dir();
        let mut app = App::default();
        app.input = "/subtitle-split off".into();
        run_command(&mut app);
        app.input = "/subtitle-lines 3".into();
        run_command(&mut app);
        app.input = "/subtitle-width 72".into();
        run_command(&mut app);

        assert!(!app.subtitle_sentence_split);
        assert_eq!(app.subtitle_max_lines, 3);
        assert_eq!(app.subtitle_max_width, 72);

        app.input = "/subtitle-width 5".into();
        run_command(&mut app);
        assert_eq!(app.subtitle_max_width, 72);
        assert_eq!(app.status, "Subtitle width must be between 20 and 100");
    }

    #[test]
    fn diarization_backend_command_offers_both_backends() {
        let mut app = App::default();

        assert!(open_command_menu(&mut app, "/diarization-backend"));
        assert_eq!(
            command_menu_options(&app),
            vec!["pyannote", "onnx", "sortformer", BACK_MENU_OPTION]
        );
    }

    #[test]
    fn backend_command_offers_onnx_and_provider_is_persisted() {
        let mut app = App::default();
        app.onnx_provider = "coreml".into();

        assert!(open_command_menu(&mut app, "/backend"));
        assert!(command_menu_options(&app).contains(&"onnx".to_string()));
        let serialized = serde_json::to_value(TuiSettings {
            onnx_provider: app.onnx_provider.clone(),
            ..TuiSettings::default()
        })
        .expect("settings serialize");
        assert_eq!(serialized["onnx_provider"], "coreml");
    }

    #[test]
    fn sortformer_rejects_fixed_speaker_count() {
        let _config = isolated_config_dir();
        let mut app = App::default();
        app.diarization_backend = "sortformer".into();
        app.input = "/speakers 2".into();

        run_command(&mut app);

        assert_eq!(app.num_speakers, None);
        assert_eq!(
            app.status,
            "Sortformer detects the speaker count automatically"
        );
    }

    #[test]
    fn backend_command_opens_its_choices_with_the_current_value_selected() {
        let mut app = App::default();
        app.backend = selectable_backends()[0].into();

        assert!(open_command_menu(&mut app, "/backend"));

        assert_eq!(app.command_menu.as_deref(), Some("/backend"));
        assert_eq!(app.command_menu_index, 0);
        assert!(command_menu_options(&app)
            .iter()
            .any(|option| option == &app.backend));
    }

    #[test]
    fn llm_model_command_is_accepted() {
        let _config = isolated_config_dir();
        let mut app = App::default();
        app.input = "/llm-model gpt-4.1-mini".into();

        run_command(&mut app);

        assert_eq!(app.llm_model, "gpt-4.1-mini");
        assert_eq!(app.status, "LLM model saved");
    }

    #[test]
    fn settings_menu_offers_all_llm_providers() {
        let mut app = App::default();
        assert!(open_command_menu(&mut app, "/settings"));
        app.command_menu = Some("/settings-provider".into());

        assert_eq!(
            command_menu_options(&app),
            vec![
                "API",
                "Claude Code",
                "Codex",
                "OpenCode",
                "Pi",
                "oh-my-pi",
                "Other",
                BACK_MENU_OPTION
            ]
        );
    }

    #[test]
    fn provider_extras_reach_the_worker_settings_payload() {
        let _config = isolated_config_dir();
        let mut app = App::default();
        app.llm_provider = "oh-my-pi".into();
        app.input = "/llm-provider-name anthropic".into();
        run_command(&mut app);
        app.input = "/llm-args --thinking low".into();
        run_command(&mut app);
        app.input = "/llm-tools on".into();
        run_command(&mut app);
        app.input = "/llm-path /opt/homebrew/bin/omp".into();
        run_command(&mut app);

        let payload = llm_settings_payload(&app);
        assert_eq!(payload["omp_provider"], "anthropic");
        assert_eq!(payload["omp_args"], "--thinking low");
        assert_eq!(payload["omp_path"], "/opt/homebrew/bin/omp");
        assert_eq!(payload["llm_allow_tools"], true);
        assert!(app.llm_tool_check_requested.is_some());
    }

    #[test]
    fn other_provider_uses_its_path_and_args() {
        let _config = isolated_config_dir();
        let mut app = App::default();
        app.llm_provider = "Other".into();
        app.input = "/llm-path /usr/local/bin/my-llm".into();
        run_command(&mut app);
        app.input = "/llm-args --stdin {stdin}".into();
        run_command(&mut app);

        let payload = llm_settings_payload(&app);
        assert_eq!(payload["other_path"], "/usr/local/bin/my-llm");
        assert_eq!(payload["other_args"], "--stdin {stdin}");
    }

    #[test]
    fn provider_name_command_is_only_for_pi_like_providers() {
        let _config = isolated_config_dir();
        let mut app = App::default();
        app.llm_provider = "Claude Code".into();
        app.input = "/llm-provider-name openai".into();
        run_command(&mut app);
        assert_eq!(
            app.status,
            "Internal provider applies to Pi and oh-my-pi only"
        );
    }

    #[test]
    fn clear_suggestion_executes_without_an_extra_enter() {
        let _config = isolated_config_dir();
        let mut app = App::default();
        app.files.push("/tmp/input.wav".into());
        app.result_files.push("/tmp/output.txt".into());

        accept_command_suggestion(&mut app, "/clear");

        assert!(app.files.is_empty());
        assert!(app.result_files.is_empty());
        assert!(app.input.is_empty());
    }

    #[test]
    fn pets_suggestion_executes_without_an_extra_enter() {
        let _config = isolated_config_dir();
        let mut app = App::default();

        accept_command_suggestion(&mut app, "/pets");

        assert_eq!(
            app.status,
            "Pets require Kitty, iTerm2, or Sixel image support."
        );
        assert!(app.input.is_empty());
    }

    #[test]
    fn back_option_closes_a_settings_menu_without_applying_a_change() {
        let _config = isolated_config_dir();
        let mut app = App::default();
        app.command_menu = Some("/diarize".into());
        app.command_menu_index = command_menu_options(&app)
            .iter()
            .position(|option| option == BACK_MENU_OPTION)
            .expect("Back option must be present");

        apply_command_menu(&mut app);

        assert!(app.command_menu.is_none());
        assert!(!app.diarization);
    }

    #[test]
    fn queue_paths_accepts_multiple_pasted_lines() {
        let directory = std::env::temp_dir().join(format!(
            "gigaam-tui-queue-paths-{}-{}",
            std::process::id(),
            std::thread::current().name().unwrap_or("test")
        ));
        fs::create_dir_all(&directory).unwrap();
        let first = directory.join("first.wav");
        let second = directory.join("second file.mp3");
        fs::write(&first, []).unwrap();
        fs::write(&second, []).unwrap();

        let mut app = App::default();
        queue_paths(
            &mut app,
            &format!("{}\n{}", first.display(), second.display()),
        );

        assert_eq!(app.files.len(), 2);
        assert!(app.files.iter().any(|path| path.ends_with("first.wav")));
        assert!(app
            .files
            .iter()
            .any(|path| path.ends_with("second file.mp3")));
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn llm_file_command_adds_text_inputs_and_rejects_media() {
        let _config = isolated_config_dir();
        let directory =
            std::env::temp_dir().join(format!("gigaam-tui-llm-file-{}", std::process::id()));
        fs::create_dir_all(&directory).unwrap();
        let transcript = directory.join("meeting.txt");
        let audio = directory.join("meeting.wav");
        fs::write(&transcript, "hello").unwrap();
        fs::write(&audio, []).unwrap();

        let mut app = App::default();
        app.input = format!("/llm-file {}", transcript.display());
        run_command(&mut app);
        assert_eq!(llm_input_files(&app).len(), 1);
        assert!(llm_can_run(&app));

        app.input = format!("/llm-file {}", audio.display());
        run_command(&mut app);
        assert_eq!(app.status, "LLM input must be .txt, .md, .srt or .vtt");
        assert_eq!(llm_input_files(&app).len(), 1);

        app.input = "/clear".into();
        run_command(&mut app);
        assert!(llm_input_files(&app).is_empty());
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn audio_mode_is_selectable_persisted_and_sent_to_the_worker() {
        let _config = isolated_config_dir();
        let mut app = App::default();
        assert!(open_command_menu(&mut app, "/audio-mode"));
        assert_eq!(
            command_menu_options(&app),
            vec!["auto", "off", "light", "denoise", BACK_MENU_OPTION]
        );
        app.input = "/audio-mode denoise".into();
        run_command(&mut app);
        assert_eq!(app.audio_preprocessing_mode, "denoise");
        app.input = "/audio-mode loud".into();
        run_command(&mut app);
        assert_eq!(app.status, "Usage: /audio-mode auto|off|light|denoise");

        let payload = start_payload(&app);
        assert_eq!(payload["audio_preprocessing_mode"], "denoise");
        let restored: TuiSettings = serde_json::from_str(
            &serde_json::to_string(&TuiSettings {
                audio_preprocessing_mode: "light".into(),
                ..TuiSettings::default()
            })
            .unwrap(),
        )
        .unwrap();
        assert_eq!(restored.audio_preprocessing_mode, "light");
    }
}
