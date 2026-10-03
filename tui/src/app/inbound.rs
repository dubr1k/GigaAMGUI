//! Worker messages that are not part of a batch (`batch.rs`) or of input
//! resolution (`session.rs`): LLM runs, tool checks, logs and errors.

use serde_json::Value;

use crate::{
    i18n::{t, tf, tn},
    lifecycle::{Activity, JobKind},
    worker::LlmTool,
};

use super::App;

impl App {
    /// The worker's Python traceback, one log line per line. Its end names the
    /// failing call and the real exception; the head is bounded so one error
    /// cannot push the whole session out of the 200-line log.
    fn log_traceback(&mut self, trace: &str) {
        const TRACEBACK_LINES: usize = 12;
        let lines: Vec<&str> = trace
            .lines()
            .filter(|line| !line.trim().is_empty())
            .collect();
        let skipped = lines.len().saturating_sub(TRACEBACK_LINES);
        if skipped > 0 {
            self.log(format!("  … ({skipped})"));
        }
        for line in &lines[skipped..] {
            self.log(format!("  {}", line.trim_end()));
        }
    }

    pub(crate) fn handle_message(&mut self, value: Value) {
        let kind = value["type"].as_str().unwrap_or("error");
        if self.handle_batch_message(kind, &value) {
            return;
        }
        match kind {
            "inputs_resolved" => self.inputs_resolved(value),
            "log" => self.log(value["message"].as_str().unwrap_or("").to_string()),
            "llm_started" => {
                if !self.activity.acknowledge(JobKind::Llm)
                    && self.activity != Activity::Running(JobKind::Llm)
                {
                    return;
                }
                self.llm_stream.clear();
                // A new run must never show the previous run's results: `llm_completed`
                // without `results` (worker failure, cancel) leaves `llm_results` alone.
                self.llm_results.clear();
                self.llm_saved_files.clear();
                self.llm_stream_mode = value["mode"].as_str().unwrap_or("summary").to_owned();
                self.status = tf(
                    self.lang,
                    "status.llm_mode_running",
                    &[
                        ("mode", value["mode"].as_str().unwrap_or("summary")),
                        ("index", &value["index"].as_u64().unwrap_or(1).to_string()),
                        ("total", &value["total"].as_u64().unwrap_or(1).to_string()),
                    ],
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
                if !self.llm_running() {
                    return;
                }
                let termination_failed = value["termination_failed"].as_bool().unwrap_or(false);
                if termination_failed {
                    // Отмена не подтверждена: новый запуск может пересечься со старым CLI.
                    self.activity = Activity::Stopping(JobKind::Llm);
                } else {
                    self.activity.finish(JobKind::Llm);
                }
                self.llm_stream.clear();
                self.llm_saved_files = value["saved_files"]
                    .as_array()
                    .map(|items| {
                        items
                            .iter()
                            .filter_map(|v| v.as_str().map(str::to_owned))
                            .collect()
                    })
                    .unwrap_or_default();
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
                if termination_failed {
                    self.status = tf(
                        self.lang,
                        "status.stop_failed",
                        &[(
                            "error",
                            value["message"]
                                .as_str()
                                .unwrap_or(t(self.lang, "status.unknown_error")),
                        )],
                    );
                } else if value["cancelled"].as_bool().unwrap_or(false) {
                    self.status = t(self.lang, "status.llm_cancelled").into();
                } else if value["success"].as_bool().unwrap_or(false) {
                    let saved = value["saved_files"].as_array().map_or(0, Vec::len);
                    self.status = tf(
                        self.lang,
                        "status.llm_saved",
                        &[("results", &tn(self.lang, saved, "plural.results"))],
                    );
                } else {
                    self.status = tf(
                        self.lang,
                        "status.llm_error",
                        &[(
                            "error",
                            value["message"]
                                .as_str()
                                .unwrap_or(t(self.lang, "status.unknown_error")),
                        )],
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
                        "found" => tf(
                            self.lang,
                            "status.tool_found",
                            &[
                                ("provider", &tool.provider),
                                (
                                    "version",
                                    tool.version
                                        .as_deref()
                                        .unwrap_or(t(self.lang, "value.found")),
                                ),
                                ("path", tool.path.as_deref().unwrap_or("?")),
                            ],
                        ),
                        status => tf(
                            self.lang,
                            "status.tool_missing",
                            &[
                                ("provider", &tool.provider),
                                (
                                    "status",
                                    match status {
                                        "broken" => t(self.lang, "llm.broken"),
                                        "missing" => t(self.lang, "llm.not_installed"),
                                        other => other,
                                    },
                                ),
                                ("hint", &tool.install_hint),
                            ],
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
                // Newer workers name the command an error answers. Without that,
                // a late reply to `cancel` arriving during the next start was taken
                // as the start's rejection: the TUI went idle and then ignored the
                // `started` that followed. Old workers send no `request`, and for
                // them any error during a start still rejects it.
                let request = value["request"].as_str();
                let message = value["message"].as_str().map(str::to_owned);
                if matches!(request, None | Some("start")) {
                    if let Some(batch) = &mut self.batch {
                        batch.error = message.clone();
                    }
                }
                if let Some(kind) = self.activity.starting_kind() {
                    if request.is_none_or(|request| request == kind.start_command()) {
                        self.finish_batch(false, message.clone(), None);
                        self.activity = Activity::Idle;
                    }
                }
                self.status = message.unwrap_or_else(|| t(self.lang, "status.worker_error").into());
                self.log(tf(self.lang, "log.error", &[("error", &self.status)]));
                if let Some(trace) = value["traceback"].as_str() {
                    self.log_traceback(trace);
                }
                let resolver_failed = match request {
                    Some(request) => request == "resolve_inputs",
                    // An old worker without `resolve_inputs` says so only in English.
                    None => self.status.contains("resolve_inputs"),
                };
                if !self.pending_inputs.is_empty() && resolver_failed {
                    self.resolver_unavailable();
                }
            }
            _ => {}
        }
    }
}
