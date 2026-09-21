//! Application state and the handling of worker events.

use std::{
    collections::{HashMap, HashSet},
    path::Path,
};

use ratatui_image::{
    picker::{Picker, ProtocolType},
    protocol::StatefulProtocol,
};
use serde_json::Value;

use crate::{commands::short_name, i18n::Lang, worker::LlmTool};

pub(crate) struct App {
    pub(crate) lang: Lang,
    pub(crate) input: String,
    pub(crate) files: Vec<String>,
    pub(crate) logs: Vec<String>,
    pub(crate) status: String,
    pub(crate) current_file: Option<String>,
    pub(crate) file_index: usize,
    pub(crate) total_files: usize,
    pub(crate) stage: String,
    pub(crate) progress: f64,
    pub(crate) processed_seconds: Option<f64>,
    pub(crate) total_seconds: Option<f64>,
    pub(crate) running: bool,
    pub(crate) cancelled: bool,
    pub(crate) diarization: bool,
    pub(crate) diarization_backend: String,
    pub(crate) num_speakers: Option<u32>,
    pub(crate) formats: Vec<String>,
    pub(crate) output_dir: Option<String>,
    pub(crate) backend: String,
    pub(crate) onnx_provider: String,
    pub(crate) model: String,
    pub(crate) subtitle_sentence_split: bool,
    pub(crate) subtitle_max_lines: u8,
    pub(crate) subtitle_max_width: u16,
    pub(crate) show_logs: bool,
    pub(crate) result_files: Vec<String>,
    pub(crate) selected_file: Option<usize>,
    pub(crate) selected_command: usize,
    pub(crate) command_menu: Option<String>,
    pub(crate) command_menu_index: usize,
    pub(crate) exit_requested: bool,
    pub(crate) pet_enabled: bool,
    pub(crate) pet_frame: usize,
    pub(crate) pet_running: bool,
    pub(crate) pet_picker: Option<Picker>,
    pub(crate) pet_protocol: Option<ProtocolType>,
    pub(crate) pet_image: Option<StatefulProtocol>,
    pub(crate) llm_modes: Vec<String>,
    pub(crate) llm_prompt: String,
    pub(crate) llm_requested: bool,
    pub(crate) llm_provider: String,
    pub(crate) llm_api_url: String,
    pub(crate) llm_api_key: String,
    pub(crate) llm_model: String,
    pub(crate) llm_temperature: f64,
    pub(crate) llm_providers: Vec<String>,
    pub(crate) llm_tools: Vec<LlmTool>,
    pub(crate) llm_internal_providers: HashMap<String, String>,
    pub(crate) llm_extra_args: HashMap<String, String>,
    pub(crate) llm_tool_paths: HashMap<String, String>,
    pub(crate) llm_allow_tools: bool,
    pub(crate) llm_tool_check_requested: Option<(String, String)>,
    pub(crate) llm_running: bool,
    pub(crate) llm_stream: String,
    pub(crate) llm_results: Vec<(String, String)>,
    pub(crate) show_llm_result: bool,
    pub(crate) llm_cancel_requested: bool,
    pub(crate) llm_extra_files: Vec<String>,
    pub(crate) audio_preprocessing_mode: String,
}

impl Default for App {
    fn default() -> Self {
        Self {
            lang: Lang::Ru,
            input: String::new(),
            files: Vec::new(),
            logs: vec!["Ready. Paste a media path and press Enter.".into()],
            status: "Ready".into(),
            current_file: None,
            file_index: 0,
            total_files: 0,
            stage: "preparing".into(),
            progress: 0.0,
            processed_seconds: None,
            total_seconds: None,
            running: false,
            cancelled: false,
            diarization: false,
            diarization_backend: "pyannote".into(),
            num_speakers: None,
            formats: vec!["txt".into()],
            output_dir: None,
            backend: "auto".into(),
            onnx_provider: "auto".into(),
            model: "v3_e2e_rnnt".into(),
            subtitle_sentence_split: true,
            subtitle_max_lines: 2,
            subtitle_max_width: 64,
            show_logs: true,
            result_files: Vec::new(),
            selected_file: None,
            selected_command: 0,
            command_menu: None,
            command_menu_index: 0,
            exit_requested: false,
            pet_enabled: false,
            pet_frame: 0,
            pet_running: false,
            pet_picker: None,
            pet_protocol: None,
            pet_image: None,
            llm_modes: vec!["summary".into()],
            llm_prompt: String::new(),
            llm_requested: false,
            llm_provider: "API".into(),
            llm_api_url: String::new(),
            llm_api_key: String::new(),
            llm_model: String::new(),
            llm_temperature: 0.2,
            llm_providers: Vec::new(),
            llm_tools: Vec::new(),
            llm_internal_providers: HashMap::new(),
            llm_extra_args: HashMap::new(),
            llm_tool_paths: HashMap::new(),
            llm_allow_tools: false,
            llm_tool_check_requested: None,
            llm_running: false,
            llm_stream: String::new(),
            llm_results: Vec::new(),
            show_llm_result: false,
            llm_cancel_requested: false,
            llm_extra_files: Vec::new(),
            audio_preprocessing_mode: "auto".into(),
        }
    }
}

impl App {
    pub(crate) fn log(&mut self, line: impl Into<String>) {
        self.logs.push(line.into());
        if self.logs.len() > 200 {
            self.logs.remove(0);
        }
    }

    pub(crate) fn handle_message(&mut self, value: Value) {
        let kind = value["type"].as_str().unwrap_or("error");
        match kind {
            "started" => {
                self.running = true;
                self.cancelled = false;
                self.total_files = value["total_files"].as_u64().unwrap_or(0) as usize;
                self.status = format!(
                    "Recognition running · {}",
                    value["backend"].as_str().unwrap_or("auto")
                );
            }
            "log" => self.log(value["message"].as_str().unwrap_or("").to_string()),
            "file_started" => {
                self.current_file = value["file"].as_str().map(str::to_owned);
                self.file_index = value["file_index"].as_u64().unwrap_or(0) as usize;
                self.progress = 0.0;
                self.status = "Recognition running…".into();
            }
            "progress" => {
                self.stage = value["stage"].as_str().unwrap_or("preparing").to_string();
                self.progress = value["file_progress"]
                    .as_f64()
                    .unwrap_or(0.0)
                    .clamp(0.0, 1.0);
                self.processed_seconds = value["processed_seconds"].as_f64();
                self.total_seconds = value["total_seconds"].as_f64();
                if let Some(message) = value["message"].as_str() {
                    self.status = message.to_string();
                }
            }
            "file_completed" => {
                if value["result"]["success"].as_bool().unwrap_or(false) {
                    self.log(format!(
                        "✓ {}",
                        short_name(value["file"].as_str().unwrap_or(""))
                    ));
                    if let Some(saved) = value["result"]["saved_files"].as_array() {
                        self.result_files
                            .extend(saved.iter().filter_map(|p| p.as_str().map(str::to_owned)));
                    }
                } else {
                    self.log(format!(
                        "× {}",
                        short_name(value["file"].as_str().unwrap_or(""))
                    ));
                }
            }
            "cancelling" => {
                self.cancelled = true;
                self.status = value["message"].as_str().unwrap_or("Cancelling…").into();
            }
            "completed" => {
                self.running = false;
                self.cancelled = value["cancelled"].as_bool().unwrap_or(false);
                self.status = if self.cancelled {
                    "Cancelled".into()
                } else if value["success"].as_bool().unwrap_or(false) {
                    "Completed".into()
                } else {
                    "Completed with errors".into()
                };
                self.log(self.status.clone());
            }
            "llm_started" => {
                self.running = true;
                self.llm_running = true;
                self.llm_stream.clear();
                // A new run must never show the previous run's results: `llm_completed`
                // without `results` (worker failure, cancel) leaves `llm_results` alone.
                self.llm_results.clear();
                self.show_llm_result = false;
                self.status = format!(
                    "LLM {} ({}/{})…",
                    value["mode"].as_str().unwrap_or("summary"),
                    value["index"].as_u64().unwrap_or(1),
                    value["total"].as_u64().unwrap_or(1)
                );
            }
            "llm_chunk" => {
                if let Some(text) = value["text"].as_str() {
                    self.llm_stream.push_str(text);
                    if self.llm_stream.len() > 8_000 {
                        let cut = self.llm_stream.len() - 8_000;
                        let boundary = self
                            .llm_stream
                            .char_indices()
                            .map(|(index, _)| index)
                            .find(|index| *index >= cut)
                            .unwrap_or(cut);
                        self.llm_stream.drain(..boundary);
                    }
                }
            }
            "llm_completed" => {
                self.running = false;
                self.llm_running = false;
                self.llm_cancel_requested = false;
                self.llm_stream.clear();
                if let Some(results) = value["results"].as_array() {
                    self.llm_results = results
                        .iter()
                        .filter_map(|item| {
                            Some((
                                item["mode"].as_str()?.to_owned(),
                                item["text"].as_str()?.to_owned(),
                            ))
                        })
                        .collect();
                }
                if value["cancelled"].as_bool().unwrap_or(false) {
                    self.status = "LLM cancelled".into();
                } else if value["success"].as_bool().unwrap_or(false) {
                    let saved = value["saved_files"].as_array().map_or(0, Vec::len);
                    self.status = format!("LLM saved {saved} result(s) · r to view");
                    self.show_llm_result = !self.llm_results.is_empty();
                } else {
                    self.status = format!(
                        "LLM error: {}",
                        value["message"].as_str().unwrap_or("unknown error")
                    );
                }
                self.log(self.status.clone());
            }
            "llm_tools" => {
                self.llm_providers = value["providers"]
                    .as_array()
                    .map(|items| {
                        items
                            .iter()
                            .filter_map(|v| v.as_str().map(str::to_owned))
                            .collect()
                    })
                    .unwrap_or_default();
                self.llm_tools = value["tools"]
                    .as_array()
                    .map(|items| {
                        items
                            .iter()
                            .filter_map(|v| serde_json::from_value::<LlmTool>(v.clone()).ok())
                            .collect()
                    })
                    .unwrap_or_default();
            }
            "llm_tool_check" => {
                if let Ok(tool) = serde_json::from_value::<LlmTool>(value["tool"].clone()) {
                    self.status = match tool.status.as_str() {
                        "found" => format!(
                            "{} · {} at {}",
                            tool.provider,
                            tool.version.as_deref().unwrap_or("found"),
                            tool.path.as_deref().unwrap_or("?")
                        ),
                        _ => format!(
                            "{} · {} · {}",
                            tool.provider, tool.status, tool.install_hint
                        ),
                    };
                    self.log(self.status.clone());
                    if let Some(slot) = self
                        .llm_tools
                        .iter_mut()
                        .find(|t| t.provider == tool.provider)
                    {
                        *slot = tool;
                    } else {
                        self.llm_tools.push(tool);
                    }
                }
            }
            "error" => {
                self.status = value["message"].as_str().unwrap_or("Worker error").into();
                self.log(format!("Error: {}", self.status));
            }
            _ => {}
        }
    }
}

pub(crate) fn llm_input_files(app: &App) -> Vec<String> {
    let mut seen = HashSet::new();
    app.result_files
        .iter()
        .chain(app.llm_extra_files.iter())
        .filter(|path| {
            matches!(
                Path::new(path).extension().and_then(|value| value.to_str()),
                Some("txt" | "md" | "srt" | "vtt")
            )
        })
        .filter(|path| seen.insert((*path).clone()))
        .cloned()
        .collect()
}

pub(crate) fn llm_can_run(app: &App) -> bool {
    !llm_input_files(app).is_empty()
        && !app.llm_modes.is_empty()
        && (!app.llm_modes.iter().any(|mode| mode == "custom") || !app.llm_prompt.is_empty())
}

pub(crate) fn request_llm(app: &mut App) {
    if llm_input_files(app).is_empty() {
        app.status = "No transcripts: run a transcription or /llm-file <path>".into();
    } else if app.llm_modes.is_empty() {
        app.status = "Select at least one LLM mode first".into();
    } else if app.llm_modes.iter().any(|mode| mode == "custom") && app.llm_prompt.is_empty() {
        app.status = "Set /llm-prompt for custom mode first".into();
    } else {
        app.llm_requested = true;
        app.status = format!(
            "Starting LLM for {} session result(s)…",
            llm_input_files(app).len()
        );
    }
}

/// The first Esc during an LLM run asks the worker to stop politely; once that request is
/// pending, Esc must fall through to the double-Esc kill/restart path, otherwise a CLI
/// provider that ignores the cancel (up to the worker's 600 s timeout) or a dead worker
/// locks the UI: `running` only clears on `llm_completed`, and `q`/Ctrl+C wait for it.
pub(crate) fn esc_should_soft_cancel(app: &App) -> bool {
    app.llm_running && !app.llm_cancel_requested
}

/// After the worker is killed and respawned nothing it was doing survives, so every
/// "in flight" flag and buffer of both the transcription and the LLM run must go back to
/// idle; the fresh worker will never send the `completed`/`llm_completed` that would.
pub(crate) fn reset_after_worker_restart(app: &mut App) {
    app.running = false;
    app.cancelled = true;
    app.llm_running = false;
    app.llm_cancel_requested = false;
    app.llm_requested = false;
    app.llm_stream.clear();
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;
    use crate::{
        commands::{command_menu_options, BACK_MENU_OPTION},
        worker::provider_from_menu_option,
    };

    #[test]
    fn llm_tools_message_fills_providers_and_menu_shows_status() {
        let mut app = App::default();
        app.handle_message(json!({
            "type": "llm_tools",
            "providers": ["API", "Claude Code", "Other"],
            "tools": [
                {"id": "claude", "provider": "Claude Code", "status": "found",
                 "path": "/opt/homebrew/bin/claude", "version": "2.1.275",
                 "detail": null, "install_hint": "npm install -g @anthropic-ai/claude-code"},
                {"id": "codex", "provider": "Codex", "status": "missing",
                 "path": null, "version": null, "detail": null,
                 "install_hint": "npm install -g @openai/codex"}
            ]
        }));

        assert_eq!(app.llm_providers, vec!["API", "Claude Code", "Other"]);
        assert_eq!(app.llm_tools.len(), 2);
        app.command_menu = Some("/settings-provider".into());
        assert_eq!(
            command_menu_options(&app),
            vec!["API", "Claude Code · 2.1.275", "Other", BACK_MENU_OPTION]
        );
        assert_eq!(
            provider_from_menu_option("Claude Code · 2.1.275"),
            "Claude Code"
        );
        assert_eq!(provider_from_menu_option("Codex · not installed"), "Codex");
    }

    #[test]
    fn llm_chunks_stream_into_the_view_and_results_are_kept() {
        let mut app = App::default();
        app.handle_message(
            json!({"type": "llm_started", "mode": "summary", "index": 1, "total": 1}),
        );
        assert!(app.llm_running && app.running);
        app.handle_message(json!({"type": "llm_chunk", "mode": "summary", "text": "Итог: "}));
        app.handle_message(json!({"type": "llm_chunk", "mode": "summary", "text": "всё хорошо"}));
        assert_eq!(app.llm_stream, "Итог: всё хорошо");
        app.handle_message(json!({
            "type": "llm_completed", "success": true, "saved_files": ["/tmp/session_llm_summary.txt"],
            "results": [{"mode": "summary", "text": "Итог: всё хорошо"}]
        }));
        assert!(!app.llm_running && !app.running);
        assert_eq!(
            app.llm_results,
            vec![("summary".to_string(), "Итог: всё хорошо".to_string())]
        );
        assert!(app.show_llm_result);
        assert!(app.llm_stream.is_empty());
    }

    #[test]
    fn a_new_llm_run_clears_the_previous_results() {
        let mut app = App::default();
        app.handle_message(
            json!({"type": "llm_started", "mode": "summary", "index": 1, "total": 1}),
        );
        app.handle_message(json!({
            "type": "llm_completed", "success": true, "saved_files": [],
            "results": [{"mode": "summary", "text": "old"}]
        }));
        assert!(app.show_llm_result && !app.llm_results.is_empty());

        app.handle_message(json!({"type": "llm_started", "mode": "tasks", "index": 1, "total": 1}));
        assert!(app.llm_results.is_empty());
        assert!(!app.show_llm_result);
        // A completion without `results` (worker failure) must not resurrect the old run.
        app.handle_message(json!({"type": "llm_completed", "success": false, "error": "boom"}));
        assert!(app.llm_results.is_empty());
        assert!(!app.show_llm_result);
    }

    #[test]
    fn esc_soft_cancels_an_llm_run_only_once_then_falls_through_to_the_kill_path() {
        let mut app = App::default();
        assert!(
            !esc_should_soft_cancel(&app),
            "idle: Esc is not an LLM cancel"
        );

        app.handle_message(
            json!({"type": "llm_started", "mode": "summary", "index": 1, "total": 1}),
        );
        assert!(esc_should_soft_cancel(&app), "first Esc sends llm_cancel");

        app.llm_cancel_requested = true; // what the first Esc arm sets
        assert!(app.llm_running && app.running);
        assert!(
            !esc_should_soft_cancel(&app),
            "second Esc must reach the `running` double-Esc kill/restart arm"
        );
    }

    #[test]
    fn worker_restart_resets_every_in_flight_flag() {
        let mut app = App::default();
        app.handle_message(
            json!({"type": "llm_started", "mode": "summary", "index": 1, "total": 1}),
        );
        app.handle_message(json!({"type": "llm_chunk", "mode": "summary", "text": "partial"}));
        app.llm_cancel_requested = true;
        app.llm_requested = true;
        app.cancelled = false;

        reset_after_worker_restart(&mut app);

        assert!(!app.running);
        assert!(app.cancelled);
        assert!(!app.llm_running);
        assert!(!app.llm_cancel_requested);
        assert!(!app.llm_requested);
        assert!(app.llm_stream.is_empty());
    }

    #[test]
    fn cancelled_llm_run_is_reported_without_an_error() {
        let mut app = App::default();
        app.handle_message(json!({"type": "llm_started", "mode": "tasks", "index": 1, "total": 2}));
        app.handle_message(
            json!({"type": "llm_completed", "success": false, "cancelled": true,
                                  "saved_files": [], "results": []}),
        );
        assert_eq!(app.status, "LLM cancelled");
        assert!(!app.llm_running);
    }
}
