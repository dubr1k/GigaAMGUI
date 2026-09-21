//! The Python worker process: spawning, the JSON line protocol and the payloads sent to it.

use std::{
    io::{self, BufRead, BufReader, Write},
    path::{Path, PathBuf},
    process::{Child, ChildStdin, Command, Stdio},
    sync::mpsc::{self, Receiver},
};

use serde::Deserialize;
use serde_json::{json, Value};

use crate::{
    app::{llm_input_files, App},
    commands::BACK_MENU_OPTION,
    settings::TuiSettings,
};

#[derive(Clone, Debug, Deserialize)]
#[serde(default)]
pub(crate) struct LlmTool {
    pub(crate) id: String,
    pub(crate) provider: String,
    pub(crate) status: String, // found | missing | broken | not_applicable
    pub(crate) path: Option<String>,
    pub(crate) version: Option<String>,
    pub(crate) install_hint: String,
}

impl Default for LlmTool {
    fn default() -> Self {
        Self {
            id: String::new(),
            provider: String::new(),
            status: "missing".into(),
            path: None,
            version: None,
            install_hint: String::new(),
        }
    }
}

/// Запасной список на случай, если воркер ещё не ответил на `llm_tools`.
pub(crate) const FALLBACK_PROVIDERS: [&str; 7] = [
    "API",
    "Claude Code",
    "Codex",
    "OpenCode",
    "Pi",
    "oh-my-pi",
    "Other",
];

/// Префикс ключей settings для провайдера — совпадает с `cli_tools.PROVIDERS`.
pub(crate) fn provider_prefix(provider: &str) -> &'static str {
    match provider {
        "Claude Code" => "claude",
        "Codex" => "codex",
        "OpenCode" => "opencode",
        "Pi" => "pi",
        "oh-my-pi" => "omp",
        "Other" => "other",
        _ => "api",
    }
}

pub(crate) fn llm_tool_for<'a>(app: &'a App, provider: &str) -> Option<&'a LlmTool> {
    app.llm_tools.iter().find(|tool| tool.provider == provider)
}

pub(crate) fn provider_menu_options(app: &App) -> Vec<String> {
    let providers: Vec<String> = if app.llm_providers.is_empty() {
        FALLBACK_PROVIDERS
            .iter()
            .map(|name| (*name).to_owned())
            .collect()
    } else {
        app.llm_providers.clone()
    };
    providers
        .into_iter()
        .map(|provider| match llm_tool_for(app, &provider) {
            Some(tool) if tool.status == "found" => format!(
                "{provider} · {}",
                tool.version.as_deref().unwrap_or("found")
            ),
            Some(tool) if tool.status == "broken" => format!("{provider} · broken"),
            Some(tool) if tool.status == "missing" => format!("{provider} · not installed"),
            _ => provider,
        })
        .chain(std::iter::once(BACK_MENU_OPTION.to_owned()))
        .collect()
}

pub(crate) fn provider_from_menu_option(option: &str) -> &str {
    option.split(" · ").next().unwrap_or(option).trim()
}

pub(crate) fn llm_settings_payload(app: &App) -> Value {
    llm_settings_from(&TuiSettings::from(app), &app.llm_tools)
}

/// The `llm_start` command for the current queue of transcripts and modes.
pub(crate) fn llm_start_payload(app: &App) -> Value {
    json!({
        "type": "llm_start",
        "files": llm_input_files(app),
        "modes": app.llm_modes,
        "prompt": app.llm_prompt,
        "settings": llm_settings_payload(app),
        "output_dir": app.output_dir,
    })
}

/// The `settings` object of `llm_start`, built from persisted settings plus the
/// tool registry (empty in headless mode: the worker then locates binaries itself).
pub(crate) fn llm_settings_from(settings: &TuiSettings, tools: &[LlmTool]) -> Value {
    let mut payload = json!({
        "provider": settings.llm_provider,
        "api_url": settings.llm_api_url,
        "api_key": settings.llm_api_key,
        "model": if settings.llm_provider == "Codex" { String::new() } else { settings.llm_model.clone() },
        "temperature": settings.llm_temperature,
    });
    for (provider, binary) in [
        ("Claude Code", "claude"),
        ("Codex", "codex"),
        ("OpenCode", "opencode"),
        ("Pi", "pi"),
        ("oh-my-pi", "omp"),
    ] {
        let path = tools
            .iter()
            .find(|tool| tool.provider == provider)
            .and_then(|tool| tool.path.clone())
            .unwrap_or_else(|| binary.to_owned());
        payload[format!("{}_path", provider_prefix(provider))] = Value::String(path);
    }
    payload["other_path"] = Value::String(String::new());
    for (prefix, path) in &settings.llm_tool_paths {
        payload[format!("{prefix}_path")] = Value::String(path.clone());
    }
    for (prefix, provider) in &settings.llm_internal_providers {
        payload[format!("{prefix}_provider")] = Value::String(provider.clone());
    }
    for (prefix, args) in &settings.llm_extra_args {
        payload[format!("{prefix}_args")] = Value::String(args.clone());
    }
    payload["llm_allow_tools"] = Value::Bool(settings.llm_allow_tools);
    payload
}

pub(crate) fn start_payload(app: &App) -> Value {
    json!({
        "type": "start",
        "files": app.files,
        "output_dir": app.output_dir,
        "formats": app.formats,
        "diarization": app.diarization,
        "diarization_backend": app.diarization_backend,
        "num_speakers": app.num_speakers,
        "backend": app.backend,
        "model": app.model,
        "onnx_provider": app.onnx_provider,
        "audio_preprocessing_mode": app.audio_preprocessing_mode,
        "subtitle_sentence_split": app.subtitle_sentence_split,
        "subtitle_max_lines": app.subtitle_max_lines,
        "subtitle_max_width": app.subtitle_max_width,
    })
}

fn python_in(directory: &Path) -> Option<PathBuf> {
    #[cfg(target_os = "windows")]
    let executable = directory.join("Scripts").join("python.exe");
    #[cfg(not(target_os = "windows"))]
    let executable = directory.join("bin").join("python");
    executable.is_file().then_some(executable)
}

fn find_python(project_root: &Path) -> String {
    if let Ok(python) = std::env::var("GIGAAM_PYTHON") {
        return python;
    }
    if let Ok(virtual_env) = std::env::var("VIRTUAL_ENV") {
        if let Some(python) = python_in(Path::new(&virtual_env)) {
            return python.to_string_lossy().into_owned();
        }
    }
    for name in [".venv", "venv"] {
        if let Some(python) = python_in(&project_root.join(name)) {
            return python.to_string_lossy().into_owned();
        }
    }
    "python".into()
}

fn bundled_worker() -> Option<PathBuf> {
    if let Ok(worker) = std::env::var("GIGAAM_TUI_WORKER_EXE") {
        let path = PathBuf::from(worker);
        if path.is_file() {
            return Some(path);
        }
    }
    let executable = if cfg!(target_os = "windows") {
        "GigaAMTuiWorker.exe"
    } else {
        "gigaam-tui-worker"
    };
    let directory = std::env::current_exe().ok()?.parent()?.to_path_buf();
    let worker = directory.join(executable);
    worker.is_file().then_some(worker)
}

pub(crate) fn spawn_worker() -> io::Result<(Child, ChildStdin, Receiver<Value>)> {
    spawn_worker_with(Stdio::inherit())
}

/// `stderr` is what the worker's diagnostics (Python warnings, model downloads)
/// go to: the terminal for the TUI and human headless output, nowhere for `--json`.
pub(crate) fn spawn_worker_with(stderr: Stdio) -> io::Result<(Child, ChildStdin, Receiver<Value>)> {
    let module = std::env::var("GIGAAM_TUI_WORKER").unwrap_or_else(|_| "src.tui_worker".into());
    let project_root = std::env::var("GIGAAM_PROJECT_ROOT")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|_| {
            std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .expect("tui has a project root")
                .to_path_buf()
        });
    let mut command = if let Some(worker) = bundled_worker() {
        Command::new(worker)
    } else {
        let python = find_python(&project_root);
        let mut command = Command::new(python);
        command.args(["-m", &module]);
        command
    };
    let mut child = command
        .current_dir(project_root)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(stderr)
        .spawn()?;
    let stdin = child.stdin.take().expect("worker stdin");
    let stdout = child.stdout.take().expect("worker stdout");
    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if let Ok(value) = serde_json::from_str::<Value>(&line) {
                let _ = tx.send(value);
            }
        }
    });
    Ok((child, stdin, rx))
}

pub(crate) fn send(stdin: &mut ChildStdin, message: Value) -> io::Result<()> {
    writeln!(
        stdin,
        "{}",
        serde_json::to_string(&message).expect("JSON command")
    )?;
    stdin.flush()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn llm_settings_payload_uses_discovered_tool_paths() {
        let mut app = App::default();
        app.llm_provider = "Claude Code".into();
        app.llm_tools.push(LlmTool {
            id: "claude".into(),
            provider: "Claude Code".into(),
            status: "found".into(),
            path: Some("/opt/homebrew/bin/claude".into()),
            version: Some("2.1.275".into()),
            install_hint: String::new(),
        });

        let payload = llm_settings_payload(&app);

        assert_eq!(payload["provider"], "Claude Code");
        assert_eq!(payload["claude_path"], "/opt/homebrew/bin/claude");
        assert_eq!(payload["codex_path"], "codex");
        assert_eq!(payload["temperature"], 0.2);
    }
}
