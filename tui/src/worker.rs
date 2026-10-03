//! The Python worker process: spawning, the JSON line protocol and its types.
//! Commands built from the UI state live in `requests.rs`; this module knows
//! nothing about `App`.

use std::{
    io::{self, Write},
    path::{Path, PathBuf},
    process::{ChildStdin, Command},
};

use serde::Deserialize;
use serde_json::{json, Value};

use crate::{providers::cli_providers, settings::TuiSettings};

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
    for provider in cli_providers() {
        let path = tools
            .iter()
            .find(|tool| tool.provider == provider.name)
            .and_then(|tool| tool.path.clone())
            .unwrap_or_else(|| provider.binary.unwrap_or_default().to_owned());
        payload[format!("{}_path", provider.prefix)] = Value::String(path);
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

/// Shared executable/environment selection; transports own their pipe policy.
pub(crate) fn worker_command() -> Command {
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
    command.current_dir(project_root);
    // The protocol is UTF-8. Without these a piped Python stdout on Windows uses
    // the ANSI code page: Cyrillic paths arrive as bytes `from_slice` rejects, or
    // the worker dies on UnicodeEncodeError (cp1252). The worker also reconfigures
    // its streams itself, for frozen builds that ignore the environment.
    command
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8");
    command
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
    fn llm_settings_follow_the_provider_registry_order() {
        let payload = llm_settings_from(&TuiSettings::default(), &[]);
        let keys: Vec<&String> = payload.as_object().unwrap().keys().collect();
        assert_eq!(
            keys[5..11],
            [
                "claude_path",
                "codex_path",
                "opencode_path",
                "pi_path",
                "omp_path",
                "other_path"
            ]
        );
        assert_eq!(payload["omp_path"], "omp");
        assert_eq!(payload["other_path"], "");
    }

    #[test]
    fn worker_command_asks_python_for_a_utf8_protocol_stream() {
        // Windows pipes get the ANSI code page otherwise; the protocol is UTF-8.
        let command = worker_command();
        let environment: std::collections::HashMap<_, _> = command.get_envs().collect();
        for (name, value) in [("PYTHONUTF8", "1"), ("PYTHONIOENCODING", "utf-8")] {
            assert_eq!(
                environment
                    .get(std::ffi::OsStr::new(name))
                    .copied()
                    .flatten(),
                Some(std::ffi::OsStr::new(value)),
                "{name}"
            );
        }
    }
}
