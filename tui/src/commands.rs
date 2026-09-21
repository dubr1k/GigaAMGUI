//! Slash commands, their menus and the path helpers behind the input line.

use std::{
    fs,
    path::{Path, PathBuf},
};

use crate::{
    app::{llm_input_files, on_off, request_llm, App, Page},
    i18n::{t, tf, tn, Lang},
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
/// Why a pasted path was rejected; [`PathError::message`] renders it in the UI
/// language (headless mode uses English).
#[derive(Debug, PartialEq, Eq)]
pub(crate) enum PathError {
    HomeNotSet,
    Missing(String),
    NotAFile(String),
}

impl PathError {
    pub(crate) fn message(&self, lang: Lang) -> String {
        match self {
            PathError::HomeNotSet => t(lang, "err.home_not_set").to_owned(),
            PathError::Missing(path) => tf(lang, "err.file_missing", &[("path", path)]),
            PathError::NotAFile(path) => tf(lang, "err.not_a_file", &[("path", path)]),
        }
    }
}

pub(crate) fn normalize_path(raw: &str) -> Result<String, PathError> {
    let mut text = raw.trim().trim_matches(['\'', '"']).trim().to_string();
    if let Some(path) = text.strip_prefix("file://") {
        text = path.replace("%20", " ");
    }
    if let Some(path) = text.strip_prefix("~/") {
        let home = std::env::var("HOME").map_err(|_| PathError::HomeNotSet)?;
        text = format!("{home}/{path}");
    }
    let path = fs::canonicalize(&text).map_err(|_| PathError::Missing(text.clone()))?;
    if !path.is_file() {
        return Err(PathError::NotAFile(path.display().to_string()));
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
                    app.status = error.message(app.lang);
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
            Err(error) => errors.push(error.message(app.lang)),
        }
    }
    if queued == 0 {
        app.status = errors
            .into_iter()
            .next()
            .unwrap_or_else(|| t(app.lang, "status.no_input_files").into());
        return;
    }
    app.selected_file = app.files.len().checked_sub(1);
    app.input.clear();
    let files = tn(app.lang, queued, "plural.files");
    app.status = if errors.is_empty() {
        tf(app.lang, "status.queued", &[("files", &files)])
    } else {
        tf(
            app.lang,
            "status.queued_skipped",
            &[("files", &files), ("skipped", &errors.len().to_string())],
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

/// Menu entries that are not values: the UI shows them through `menu.back` /
/// `menu.enter_manually`, the code compares against these identifiers.
pub(crate) const BACK_MENU_OPTION: &str = "← Back";
pub(crate) const ENTER_MANUALLY_OPTION: &str = "Enter manually";

pub(crate) const MODEL_OPTIONS: [(&str, &str); 3] = [
    ("v3_e2e_rnnt", "GigaAM v3 e2e RNNT (current)"),
    ("multilingual_ctc", "Multilingual CTC (220M)"),
    ("multilingual_large_ctc", "Multilingual Large CTC (600M)"),
];

pub(crate) const COMMANDS: [(&str, &str); 32] = [
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
    ("/settings", "open the Settings tab"),
    ("/help", "show the keys and commands"),
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

pub(crate) fn backend_usage(lang: Lang) -> String {
    tf(
        lang,
        "usage.backend",
        &[("backends", &selectable_backends().join("|"))],
    )
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
    if matches!(command, "/clear" | "/pets" | "/settings" | "/help") {
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
        Some("/settings-provider") => provider_menu_options(app),
        Some("/settings-model") => llm_model_options(&app.llm_provider),
        Some("/lang") => vec![
            t(Lang::Ru, "lang.name").to_owned(),
            t(Lang::En, "lang.name").to_owned(),
            BACK_MENU_OPTION.to_owned(),
        ],
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
        .chain([
            ENTER_MANUALLY_OPTION.to_owned(),
            BACK_MENU_OPTION.to_owned(),
        ])
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
            | "/lang"
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
        } else if command == "/lang" {
            usize::from(app.lang == Lang::En)
        } else {
            0
        };
        app.status = t(app.lang, "status.choose_option").into();
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
        app.status = t(app.lang, "status.menu_closed").into();
        app.log(app.status.clone());
        return;
    }
    let command = app.command_menu.clone().unwrap_or_default();
    match command.as_str() {
        "/backend" => {
            app.backend = option.clone();
            app.status = tf(app.lang, "status.backend", &[("value", &app.backend)]);
            app.command_menu = None;
            app.input.clear();
            save_app_settings(app);
        }
        "/onnx-provider" => {
            app.onnx_provider = option.clone();
            app.status = tf(
                app.lang,
                "status.onnx_provider",
                &[("value", &app.onnx_provider)],
            );
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
            app.status = tf(app.lang, "status.model", &[("value", &app.model)]);
            app.command_menu = None;
            app.input.clear();
            save_app_settings(app);
        }
        "/settings-provider" => {
            app.llm_provider = provider_from_menu_option(option).to_owned();
            app.command_menu = None;
            app.input.clear();
            app.status = tf(
                app.lang,
                "status.llm_provider",
                &[("value", &app.llm_provider)],
            );
            save_app_settings(app);
        }
        "/lang" => {
            app.lang = if app.command_menu_index == 1 {
                Lang::En
            } else {
                Lang::Ru
            };
            app.command_menu = None;
            app.input.clear();
            app.status = t(app.lang, "settings.language_changed").into();
            save_app_settings(app);
        }
        "/settings-model" if option == ENTER_MANUALLY_OPTION => {
            app.command_menu = None;
            app.input = "/llm-model ".into();
            app.status = t(app.lang, "status.enter_model_name").into();
        }
        "/settings-model" => {
            app.llm_model = if option == "default" {
                String::new()
            } else {
                option.clone()
            };
            app.command_menu = None;
            app.input.clear();
            app.status = if app.llm_model.is_empty() {
                t(app.lang, "status.llm_default_model").into()
            } else {
                tf(app.lang, "status.llm_model", &[("value", &app.llm_model)])
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
            app.status = tf(
                app.lang,
                "status.llm_modes",
                &[("modes", &app.llm_modes.join(", "))],
            );
        }
        "/diarize" => {
            app.diarization = option == "on";
            app.status = tf(
                app.lang,
                "status.diarization",
                &[("value", on_off(app.lang, app.diarization))],
            );
            app.command_menu = None;
            app.input.clear();
            save_app_settings(app);
        }
        "/audio-mode" => {
            app.audio_preprocessing_mode = option.clone();
            app.status = tf(app.lang, "status.audio_mode", &[("value", option)]);
            app.command_menu = None;
            app.input.clear();
            save_app_settings(app);
        }
        "/diarization-backend" => {
            app.diarization_backend = option.clone();
            if app.diarization_backend == "sortformer" {
                app.num_speakers = None;
            }
            app.status = tf(
                app.lang,
                "status.diarization_backend",
                &[("value", &app.diarization_backend)],
            );
            app.command_menu = None;
            app.input.clear();
            save_app_settings(app);
        }
        "/speakers" if app.diarization_backend == "sortformer" => {
            app.num_speakers = None;
            app.status = t(app.lang, "status.sortformer_auto").into();
            app.command_menu = None;
            app.input.clear();
            save_app_settings(app);
        }
        "/speakers" => {
            app.num_speakers = option.parse().ok();
            app.status = tf(app.lang, "status.speakers", &[("value", option)]);
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
            app.status = tf(
                app.lang,
                "status.formats",
                &[("formats", &app.formats.join(", "))],
            );
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
            app.status = t(app.lang, "status.exiting").into();
        }
        "/settings" => {
            app.page = Page::Settings;
            app.status = t(app.lang, "settings.opened").into();
        }
        "/help" => app.help_open = !app.help_open,
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
            app.status = tf(
                app.lang,
                "status.llm_modes",
                &[("modes", &app.llm_modes.join(", "))],
            );
        }
        "/llm-mode" => app.status = t(app.lang, "usage.llm-mode").into(),
        "/llm-prompt" if !argument.is_empty() => {
            app.llm_prompt = argument.into();
            app.status = t(app.lang, "status.llm_prompt_saved").into();
        }
        "/llm-prompt" => app.status = t(app.lang, "usage.llm-prompt").into(),
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
                app.status = tf(
                    app.lang,
                    "status.llm_inputs",
                    &[("n", &llm_input_files(app).len().to_string())],
                );
            }
            Ok(_) => app.status = t(app.lang, "status.llm_file_type").into(),
            Err(error) => app.status = error.message(app.lang),
        },
        "/llm-api-url" if !argument.is_empty() => {
            app.llm_api_url = argument.into();
            app.status = t(app.lang, "status.llm_api_url_saved").into();
            save_app_settings(app);
        }
        "/llm-api-key" if !argument.is_empty() => {
            app.llm_api_key = argument.into();
            app.status = t(app.lang, "status.llm_api_key_saved").into();
            save_app_settings(app);
        }
        "/llm-model" if !argument.is_empty() => {
            app.llm_model = argument.into();
            app.status = t(app.lang, "status.llm_model_saved").into();
            save_app_settings(app);
        }
        "/llm-model" => {
            let _ = open_command_menu(app, "/settings-model");
        }
        "/llm-temperature" => match argument.parse::<f64>() {
            Ok(value) if (0.0..=2.0).contains(&value) => {
                app.llm_temperature = value;
                app.status = t(app.lang, "status.llm_temperature_saved").into();
                save_app_settings(app);
            }
            _ => app.status = t(app.lang, "status.temperature_range").into(),
        },
        "/llm-provider-name" if !matches!(provider_prefix(&app.llm_provider), "pi" | "omp") => {
            app.status = t(app.lang, "status.provider_name_pi_only").into();
        }
        "/llm-provider-name" => {
            let prefix = provider_prefix(&app.llm_provider).to_owned();
            if argument.is_empty() {
                app.llm_internal_providers.remove(&prefix);
                app.status = t(app.lang, "status.provider_name_cleared").into();
            } else {
                app.llm_internal_providers.insert(prefix, argument.into());
                app.status = tf(app.lang, "status.provider_name", &[("value", argument)]);
            }
            save_app_settings(app);
        }
        "/llm-args" if app.llm_provider == "API" => {
            app.status = t(app.lang, "status.args_cli_only").into();
        }
        "/llm-args" => {
            let prefix = provider_prefix(&app.llm_provider).to_owned();
            if argument.is_empty() {
                app.llm_extra_args.remove(&prefix);
                app.status = t(app.lang, "status.args_cleared").into();
            } else {
                app.llm_extra_args.insert(prefix, argument.into());
                app.status = tf(app.lang, "status.args", &[("value", argument)]);
            }
            save_app_settings(app);
        }
        "/llm-path" if app.llm_provider == "API" => {
            app.status = t(app.lang, "status.path_cli_only").into();
        }
        "/llm-path" => {
            let prefix = provider_prefix(&app.llm_provider).to_owned();
            if argument.is_empty() {
                app.llm_tool_paths.remove(&prefix);
                app.status = t(app.lang, "status.path_reset").into();
            } else {
                app.llm_tool_paths.insert(prefix, argument.into());
                app.status = tf(app.lang, "status.path_checking", &[("value", argument)]);
            }
            if app.llm_provider != "Other" {
                app.llm_tool_check_requested = Some((app.llm_provider.clone(), argument.into()));
            }
            save_app_settings(app);
        }
        "/llm-tools" if matches!(argument, "on" | "off") => {
            app.llm_allow_tools = argument == "on";
            app.status = tf(
                app.lang,
                "status.llm_tools",
                &[("value", on_off(app.lang, app.llm_allow_tools))],
            );
            save_app_settings(app);
        }
        "/llm-tools" => app.status = t(app.lang, "usage.llm-tools").into(),
        "/pets" => toggle_pets(app),
        "/output" => {
            if argument.is_empty() {
                app.status = t(app.lang, "usage.output").into();
            } else if let Err(error) = fs::create_dir_all(argument) {
                app.status = tf(
                    app.lang,
                    "status.output_dir_error",
                    &[("error", &error.to_string())],
                );
            } else if let Ok(path) = fs::canonicalize(argument) {
                app.output_dir = Some(path.to_string_lossy().into_owned());
                app.status = t(app.lang, "status.output_dir_updated").into();
            }
        }
        "/backend" if backend_is_supported(&argument.to_ascii_lowercase()) => {
            app.backend = argument.to_ascii_lowercase();
            app.status = tf(app.lang, "status.backend", &[("value", &app.backend)]);
            save_app_settings(app);
        }
        "/backend" => app.status = backend_usage(app.lang),
        "/onnx-provider"
            if matches!(
                argument.to_ascii_lowercase().as_str(),
                "auto" | "cpu" | "cuda" | "tensorrt" | "coreml" | "directml"
            ) =>
        {
            app.onnx_provider = argument.to_ascii_lowercase();
            app.status = tf(
                app.lang,
                "status.onnx_provider",
                &[("value", &app.onnx_provider)],
            );
            save_app_settings(app);
        }
        "/onnx-provider" => app.status = t(app.lang, "usage.onnx-provider").into(),
        "/model" if MODEL_OPTIONS.iter().any(|(id, _)| *id == argument) => {
            app.model = argument.into();
            app.status = tf(app.lang, "status.model", &[("value", &app.model)]);
            save_app_settings(app);
        }
        "/model" => app.status = t(app.lang, "usage.model").into(),
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
                app.status = t(app.lang, "usage.formats").into();
            } else {
                app.formats = formats;
                app.status = tf(
                    app.lang,
                    "status.formats",
                    &[("formats", &app.formats.join(", "))],
                );
                save_app_settings(app);
            }
        }
        "/subtitle-split" if matches!(argument, "on" | "off") => {
            app.subtitle_sentence_split = argument == "on";
            app.status = tf(
                app.lang,
                "status.subtitle_split",
                &[("value", on_off(app.lang, app.subtitle_sentence_split))],
            );
            save_app_settings(app);
        }
        "/subtitle-split" => app.status = t(app.lang, "usage.subtitle-split").into(),
        "/subtitle-lines" => match argument.parse::<u8>() {
            Ok(value) if (1..=4).contains(&value) => {
                app.subtitle_max_lines = value;
                app.status = tf(
                    app.lang,
                    "status.subtitle_lines",
                    &[("value", &value.to_string())],
                );
                save_app_settings(app);
            }
            _ => app.status = t(app.lang, "status.subtitle_lines_range").into(),
        },
        "/subtitle-width" => match argument.parse::<u16>() {
            Ok(value) if (20..=100).contains(&value) => {
                app.subtitle_max_width = value;
                app.status = tf(
                    app.lang,
                    "status.subtitle_width",
                    &[("value", &value.to_string())],
                );
                save_app_settings(app);
            }
            _ => app.status = t(app.lang, "status.subtitle_width_range").into(),
        },
        "/diarize" if matches!(argument, "on" | "off") => {
            app.diarization = argument == "on";
            app.status = tf(
                app.lang,
                "status.diarization",
                &[("value", on_off(app.lang, app.diarization))],
            );
            save_app_settings(app);
        }
        "/diarize" => app.status = t(app.lang, "usage.diarize").into(),
        "/audio-mode" if matches!(argument, "auto" | "off" | "light" | "denoise") => {
            app.audio_preprocessing_mode = argument.into();
            app.status = tf(app.lang, "status.audio_mode", &[("value", argument)]);
            save_app_settings(app);
        }
        "/audio-mode" => app.status = t(app.lang, "usage.audio-mode").into(),
        "/diarization-backend" if matches!(argument, "pyannote" | "onnx" | "sortformer") => {
            app.diarization_backend = argument.into();
            if app.diarization_backend == "sortformer" {
                app.num_speakers = None;
            }
            app.status = tf(
                app.lang,
                "status.diarization_backend",
                &[("value", &app.diarization_backend)],
            );
            save_app_settings(app);
        }
        "/diarization-backend" => app.status = t(app.lang, "usage.diarization-backend").into(),
        "/speakers" if app.diarization_backend == "sortformer" => {
            app.num_speakers = None;
            app.status = t(app.lang, "status.sortformer_auto").into();
            save_app_settings(app);
        }
        "/speakers" if argument == "auto" => {
            app.num_speakers = None;
            app.status = tf(
                app.lang,
                "status.speakers",
                &[("value", t(app.lang, "value.auto"))],
            );
            save_app_settings(app);
        }
        "/speakers" => match argument.parse::<u32>() {
            Ok(value) if value > 0 => {
                app.num_speakers = Some(value);
                app.status = tf(
                    app.lang,
                    "status.speakers",
                    &[("value", &value.to_string())],
                );
                save_app_settings(app);
            }
            _ => app.status = t(app.lang, "usage.speakers").into(),
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
                app.status = tf(app.lang, "queue.removed", &[("name", &short_name(&file))]);
            }
            _ => app.status = t(app.lang, "usage.remove").into(),
        },
        _ => app.status = tf(app.lang, "status.unknown_command", &[("name", &name)]),
    }
    app.log(app.status.clone());
    app.input.clear();
}

/// `/pets` and the Settings row: shows or hides the companion and persists it.
pub(crate) fn toggle_pets(app: &mut App) {
    if app.pet_enabled {
        app.clear_pet_layer();
        app.pet_enabled = false;
        app.pet_image = None;
        app.status = t(app.lang, "status.pets_off").into();
        save_app_settings(app);
    } else if let Err(error) = app.refresh_pet_image() {
        app.status = error;
    } else {
        app.pet_enabled = true;
        app.pet_running = app.running;
        app.status = t(app.lang, "status.pets_on").into();
        save_app_settings(app);
    }
}

pub(crate) fn clear_queue(app: &mut App) {
    app.files.clear();
    app.file_states.clear();
    app.selected_file = None;
    app.result_files.clear();
    app.llm_extra_files.clear();
    app.llm_results.clear();
    app.show_llm_result = false;
    app.status = t(app.lang, "status.queue_cleared").into();
}

pub(crate) fn remove_selected_file(app: &mut App) {
    let Some(index) = app.selected_file.filter(|index| *index < app.files.len()) else {
        app.status = t(app.lang, "status.no_file_selected").into();
        return;
    };
    let file = app.files.remove(index);
    app.file_states.remove(&file);
    app.selected_file = app
        .files
        .get(index)
        .map(|_| index)
        .or_else(|| index.checked_sub(1));
    app.status = tf(app.lang, "queue.removed", &[("name", &short_name(&file))]);
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
        app.lang = Lang::En;
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
        app.lang = Lang::En;
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
        app.lang = Lang::En;
        app.input = "/llm-model gpt-4.1-mini".into();

        run_command(&mut app);

        assert_eq!(app.llm_model, "gpt-4.1-mini");
        assert_eq!(app.status, "LLM model saved");
    }

    #[test]
    fn settings_menu_offers_all_llm_providers() {
        let mut app = App::default();
        assert!(
            !open_command_menu(&mut app, "/settings"),
            "/settings is a tab now"
        );
        assert!(open_command_menu(&mut app, "/settings-provider"));

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
        app.lang = Lang::En;
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
        app.lang = Lang::En;

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
        app.lang = Lang::En;
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
        app.lang = Lang::En;
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
