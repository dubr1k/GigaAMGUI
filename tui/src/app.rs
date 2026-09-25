//! Application state and the handling of worker events.

use std::{
    collections::{HashMap, HashSet, VecDeque},
    path::Path,
    time::{Duration, Instant},
};

use ratatui_image::{
    picker::{Picker, ProtocolType},
    protocol::StatefulProtocol,
};
use serde_json::{json, Value};

use crate::{
    batch::{BatchRun, BatchSummary},
    commands::{
        accept_command_suggestion, apply_command_menu, clear_queue, command_menu_options,
        command_suggestions, is_command, open_command_menu, remove_selected_file, short_name,
        toggle_pets,
    },
    i18n::{t, tf, tn, Lang},
    lifecycle::{Activity, Connection, ConnectionState, JobKind},
    queue::{QueueState, RunSelection},
    session::PendingInput,
    settings::save_app_settings,
    theme::{Palette, Theme},
    ui::{llm::MODES, settings::rows as setting_rows, Action, AreaId, ButtonId, HitMap},
    worker::{llm_start_payload, LlmTool},
};

/// The tabs of the interface, in tab-bar order.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Page {
    Processing,
    Llm,
    Settings,
    Log,
}

impl Page {
    pub(crate) const ALL: [Page; 4] = [Page::Processing, Page::Llm, Page::Settings, Page::Log];

    pub(crate) fn index(self) -> usize {
        Page::ALL.iter().position(|page| *page == self).unwrap_or(0)
    }

    pub(crate) fn next(self) -> Page {
        Page::ALL[(self.index() + 1) % Page::ALL.len()]
    }

    pub(crate) fn previous(self) -> Page {
        Page::ALL[(self.index() + Page::ALL.len() - 1) % Page::ALL.len()]
    }
}

/// Which part of the Processing page the arrow keys and Enter act on.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub(crate) enum Focus {
    #[default]
    Input,
    Queue,
    Params,
}

pub(crate) use crate::queue::FileState;

pub(crate) struct App {
    pub(crate) lang: Lang,
    pub(crate) page: Page,
    /// Interactive areas of the last frame, rebuilt by every `draw`.
    pub(crate) hits: HitMap,
    pub(crate) mouse_enabled: bool,
    /// Scroll offsets of the scrollable areas, in lines.
    pub(crate) scroll: HashMap<AreaId, u16>,
    /// The `?` overlay is drawn over the current page and swallows other keys.
    pub(crate) help_open: bool,
    pub(crate) show_path: bool,
    /// The highlighted row of the Settings page.
    pub(crate) settings_cursor: usize,
    /// The Log page shows the newest lines while this is set; scrolling up clears
    /// it and reaching the end (or `End`) sets it again.
    pub(crate) log_follow: bool,
    pub(crate) focus: Focus,
    /// The highlighted row of the parameter panel while `focus == Focus::Params`.
    pub(crate) params_cursor: usize,
    /// The worker could not be started or its event channel closed: the UI stays
    /// usable for settings, and the next-step hint says how to repair it.
    pub(crate) connection: Connection,
    pub(crate) activity: Activity,
    pub(crate) reconnect_requested: bool,
    pub(crate) worker_stop_requested: bool,
    pub(crate) worker_stopped: bool,
    pub(crate) stop_confirmation: bool,
    pub(crate) queue: QueueState,
    pub(crate) pending_inputs: VecDeque<PendingInput>,
    pub(crate) failed_inputs: Vec<String>,
    pub(crate) next_input_id: u64,
    pub(crate) batch: Option<BatchRun>,
    pub(crate) batch_summary: Option<BatchSummary>,
    pub(crate) outbox: Vec<Value>,
    pub(crate) rerun_confirmation: Option<u64>,
    /// The last `Esc` / `Ctrl+C` press and when: a second one within 700 ms confirms.
    pub(crate) last_exit_request: Option<(&'static str, Instant)>,
    pub(crate) input: crate::input::InputState,
    pub(crate) logs: Vec<String>,
    pub(crate) status: String,
    pub(crate) current_file: Option<String>,
    pub(crate) file_index: usize,
    pub(crate) total_files: usize,
    pub(crate) stage: String,
    pub(crate) progress: f64,
    pub(crate) processed_seconds: Option<f64>,
    pub(crate) total_seconds: Option<f64>,
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
    pub(crate) result_files: Vec<String>,
    pub(crate) results_open: bool,
    pub(crate) result_cursor: usize,
    pub(crate) result_notice: String,
    pub(crate) result_opening: Option<std::sync::mpsc::Receiver<Result<(), String>>>,
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
    pub(crate) llm_stream: String,
    pub(crate) llm_results: Vec<(String, String)>,
    pub(crate) llm_extra_files: Vec<String>,
    /// The highlighted row of the «Транскрипты» table on the LLM page.
    pub(crate) llm_input_cursor: usize,
    /// `Some(i)` while the LLM-page cursor is on mode row `i` (`Space` toggles it);
    /// `None` while it is on the transcripts table. `Up`/`Down` walk the table
    /// first and then the modes, so one cursor covers both blocks.
    pub(crate) llm_mode_cursor: Option<usize>,
    /// The mode the worker is streaming right now, from `llm_started`.
    pub(crate) llm_stream_mode: String,
    /// Where the last completed run saved its answers (`session_llm_<mode>.txt`).
    pub(crate) llm_saved_files: Vec<String>,
    pub(crate) audio_preprocessing_mode: String,
    /// Цветовая схема; все виджеты берут цвета из `palette()`.
    pub(crate) theme: Theme,
}

impl Default for App {
    fn default() -> Self {
        Self {
            lang: Lang::Ru,
            page: Page::Processing,
            hits: HitMap::default(),
            mouse_enabled: true,
            scroll: HashMap::new(),
            help_open: false,
            show_path: false,
            settings_cursor: 0,
            log_follow: true,
            focus: Focus::Input,
            params_cursor: 0,
            connection: Connection::new(0, Instant::now()),
            activity: Activity::Idle,
            reconnect_requested: false,
            worker_stop_requested: false,
            worker_stopped: true,
            stop_confirmation: false,
            queue: QueueState::default(),
            pending_inputs: VecDeque::new(),
            failed_inputs: Vec::new(),
            next_input_id: 0,
            batch: None,
            batch_summary: None,
            outbox: Vec::new(),
            rerun_confirmation: None,
            last_exit_request: None,
            input: crate::input::InputState::default(),
            logs: vec![t(Lang::Ru, "log.ready").into()],
            status: t(Lang::Ru, "status.ready").into(),
            current_file: None,
            file_index: 0,
            total_files: 0,
            stage: "preparing".into(),
            progress: 0.0,
            processed_seconds: None,
            total_seconds: None,
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
            result_files: Vec::new(),
            results_open: false,
            result_cursor: 0,
            result_notice: String::new(),
            result_opening: None,
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
            llm_stream: String::new(),
            llm_results: Vec::new(),
            llm_extra_files: Vec::new(),
            llm_input_cursor: 0,
            llm_mode_cursor: None,
            llm_stream_mode: String::new(),
            llm_saved_files: Vec::new(),
            audio_preprocessing_mode: "auto".into(),
            theme: Theme::default_theme(),
        }
    }
}

impl App {
    pub(crate) fn running(&self) -> bool {
        self.activity.is_active()
    }
    pub(crate) fn llm_running(&self) -> bool {
        self.activity.is_llm()
    }
    pub(crate) fn worker_down(&self) -> bool {
        self.connection.state != ConnectionState::Ready
    }

    pub(crate) fn palette(&self) -> &Palette {
        &self.theme.palette
    }

    pub(crate) fn log(&mut self, line: impl Into<String>) {
        self.logs.push(line.into());
        if self.logs.len() > 200 {
            self.logs.remove(0);
        }
    }

    pub(crate) fn file_state(&self, path: &str) -> FileState {
        self.queue
            .items
            .iter()
            .find(|item| item.path == path)
            .map_or(FileState::Pending, |item| item.state)
    }

    /// Quits on the second press of the same key within 700 ms; the first only
    /// asks for confirmation in the status line.
    pub(crate) fn request_exit(&mut self, trigger: &'static str, label: &str) {
        if self.last_exit_request.is_some_and(|(last_trigger, at)| {
            last_trigger == trigger && at.elapsed() <= Duration::from_millis(700)
        }) {
            self.exit_requested = true;
        } else {
            self.last_exit_request = Some((trigger, Instant::now()));
            self.status = tf(self.lang, "status.press_again_to_exit", &[("key", label)]);
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
                if let Some(batch) = &mut self.batch {
                    batch.error = value["message"].as_str().map(str::to_owned);
                }
                if self.activity.is_starting() {
                    self.finish_batch(false, value["message"].as_str().map(str::to_owned), None);
                    self.activity = Activity::Idle;
                }
                self.status = value["message"]
                    .as_str()
                    .unwrap_or(t(self.lang, "status.worker_error"))
                    .into();
                self.log(tf(self.lang, "log.error", &[("error", &self.status)]));
                if !self.pending_inputs.is_empty() && self.status.contains("resolve_inputs") {
                    self.resolver_unavailable();
                }
            }
            _ => {}
        }
    }
}

/// «вкл» / «выкл» (or `on` / `off`) for the status messages of the toggles.
pub(crate) fn on_off(lang: Lang, value: bool) -> &'static str {
    t(lang, if value { "value.on" } else { "value.off" })
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
    !app.worker_down()
        && !app.running()
        && !llm_input_files(app).is_empty()
        && !app.llm_modes.is_empty()
        && (!app.llm_modes.iter().any(|mode| mode == "custom") || !app.llm_prompt.is_empty())
}

pub(crate) fn request_llm(app: &mut App) -> Vec<Value> {
    if app.worker_down() || app.running() {
        return Vec::new();
    }
    if llm_input_files(app).is_empty() {
        app.status = t(app.lang, "llm.inputs_empty").into();
    } else if app.llm_modes.is_empty() {
        app.status = t(app.lang, "status.llm_no_mode").into();
    } else if app.llm_modes.iter().any(|mode| mode == "custom") && app.llm_prompt.is_empty() {
        app.status = t(app.lang, "status.llm_no_prompt").into();
    } else {
        app.activity.start(JobKind::Llm);
        app.cancelled = false;
        app.status = tf(
            app.lang,
            "status.llm_starting",
            &[(
                "transcripts",
                &tn(app.lang, llm_input_files(app).len(), "plural.transcripts"),
            )],
        );
        return vec![llm_start_payload(app)];
    }
    Vec::new()
}

/// First Esc requests cooperative cancellation; later presses open confirmation.
pub(crate) fn esc_should_soft_cancel(app: &App) -> bool {
    app.llm_running() && !app.activity.is_stopping()
}

/// Help/path overlays consume Esc before cancellation or force-stop confirmation.
pub(crate) fn esc_is_cancel(app: &App) -> bool {
    app.running()
        && !app.help_open
        && !app.show_path
        && !app.stop_confirmation
        && !esc_should_soft_cancel(app)
}

/// The one-line hint under the main area, as an i18n key. The first matching
/// situation wins: a dead worker outranks everything, then the two kinds of run,
/// then the furthest stage the session has reached.
pub(crate) fn next_step(app: &App) -> &'static str {
    if app.connection.state == ConnectionState::Connecting {
        "hint.worker_connecting"
    } else if app.worker_down() {
        "hint.worker_down"
    } else if app.llm_running() {
        "hint.cancel_llm"
    } else if !app.pending_inputs.is_empty() {
        "hint.adding_inputs"
    } else if app.activity == Activity::Starting(JobKind::Asr) {
        "hint.batch_starting"
    } else if app.running() {
        "hint.cancel_batch"
    } else if app.can_start(RunSelection::Pending) {
        "hint.start"
    } else if !app.llm_results.is_empty() {
        "hint.view_result"
    } else if !app.result_files.is_empty() {
        "hint.run_llm"
    } else if !app.queue.items.is_empty() {
        "hint.queue_actions"
    } else {
        "hint.add_files"
    }
}

/// After the worker is killed and respawned nothing it was doing survives, so every
/// "in flight" flag and buffer of both the transcription and the LLM run must go back to
/// idle; the fresh worker will never send the `completed`/`llm_completed` that would.
pub(crate) fn reset_after_worker_restart(app: &mut App) {
    app.batch = None;
    app.rerun_confirmation = None;
    app.failed_inputs
        .extend(app.pending_inputs.drain(..).map(|r| r.original));
    app.outbox.clear();
    for item in &mut app.queue.items {
        if item.state == FileState::Processing {
            item.state = FileState::Cancelled;
        }
    }
    app.activity = Activity::Idle;
    app.cancelled = true;
    app.llm_stream.clear();
}

/// The one place where an action changes state, whether it came from a key or a click.
/// Returns the commands the caller must send to the worker; `dispatch` itself never
/// writes to the process, so the tests need no worker and `main` keeps the only
/// handle that can respawn it.
pub(crate) fn dispatch(app: &mut App, action: Action) -> Vec<Value> {
    if app.stop_confirmation && !matches!(action, Action::ConfirmStop(_)) {
        return Vec::new();
    }
    if app.rerun_confirmation.is_some() && !matches!(action, Action::ConfirmRerun(_)) {
        return Vec::new();
    }
    if app.results_open
        && !matches!(
            action,
            Action::ShowResults(_)
                | Action::SelectResult(_)
                | Action::OpenResult(_)
                | Action::Scroll(AreaId::Results | AreaId::ResultPath, _)
        )
    {
        return Vec::new();
    }
    // During a run the queue is read-only; selection and inspection stay available.
    let edits_idle_state = matches!(
        action,
        Action::RemoveFile(_)
            | Action::UndoRemove
            | Action::AddFiles
            | Action::RetryInputs
            | Action::OpenMenu(_)
            | Action::MenuItem(_)
            | Action::Suggestion(_)
            | Action::ToggleMode(_)
            | Action::RemoveLlmInput(_)
            | Action::EditCommand(_)
            | Action::SettingsRow(_)
            | Action::ToggleSetting(_)
    );
    if edits_idle_state && app.running() {
        return Vec::new();
    }
    match action {
        Action::ShowResults(show) => {
            app.results_open = show;
            app.result_cursor = app
                .result_cursor
                .min(app.saved_results().len().saturating_sub(1));
            if show {
                app.result_notice.clear();
            }
        }
        Action::SelectResult(index) => {
            app.result_cursor = index.min(app.saved_results().len().saturating_sub(1));
            app.scroll.insert(AreaId::ResultPath, 0);
        }
        Action::OpenResult(folder) => app.open_selected_result(folder),
        Action::Scroll(AreaId::Results, delta) => {
            let index = app.result_cursor.saturating_add_signed(delta as isize);
            return dispatch(app, Action::SelectResult(index));
        }
        Action::Reconnect => app.request_reconnect(),
        Action::ForceStop => {
            app.stop_confirmation = true;
        }
        Action::ConfirmStop(confirmed) => {
            app.stop_confirmation = false;
            if confirmed {
                app.worker_failed(t(app.lang, "status.force_stopping").into());
                app.worker_stop_requested = true;
                app.reconnect_requested = true;
            }
        }
        Action::QueueActions => {
            if app.running() {
                return dispatch(app, Action::ShowPath(true));
            }
            open_command_menu(app, "/queue-actions");
        }
        Action::ShowPath(show) => {
            app.show_path = show && app.queue.selected_index().is_some();
            app.scroll.insert(AreaId::Path, 0);
        }
        Action::Run(selection) => return app.begin_batch(selection, false),
        Action::ConfirmRerun(confirmed) => {
            let id = app.rerun_confirmation.take();
            if confirmed && id.is_some() && id == app.queue.selected {
                return app.begin_batch(RunSelection::Selected, true);
            }
        }
        Action::UndoRemove => {
            if app.queue.undo_remove() {
                app.status = t(app.lang, "queue.restored").into();
            }
        }
        Action::AddFiles => {
            app.page = Page::Processing;
            app.command_menu = None;
            app.input
                .open(crate::input::InputMode::Paths, String::new());
            app.focus = Focus::Input;
        }
        Action::RetryInputs => {
            if let Some(original) = app.failed_inputs.last().cloned() {
                if app.submit_paths(original) {
                    app.failed_inputs.pop();
                }
            }
        }
        Action::Tab(page) => app.page = page,
        Action::SelectFile(index) => {
            if index < app.queue.items.len() {
                app.queue.select(index);
                app.focus = Focus::Queue;
            }
        }
        Action::RemoveFile(index) => {
            if index < app.queue.items.len() {
                app.queue.select(index);
                remove_selected_file(app);
            }
        }
        Action::OpenMenu(command) => {
            // A parameter without a choice menu (`/output <dir>`) is typed instead:
            // the command line is pre-filled the way the `/settings` menu does it.
            if !open_command_menu(app, command) && is_command(command) {
                app.input.open(
                    crate::input::InputMode::Argument(command),
                    format!("{command} "),
                );
                app.selected_command = 0;
                app.focus = Focus::Input;
            }
        }
        Action::MenuItem(index) => {
            if index < command_menu_options(app).len() {
                app.command_menu_index = index;
                apply_command_menu(app);
            }
        }
        Action::Suggestion(index) => {
            if let Some((command, _)) = command_suggestions(&app.input).get(index) {
                accept_command_suggestion(app, command);
            }
        }
        Action::ToggleMode(mode) => {
            app.llm_mode_cursor = MODES.iter().position(|(id, _)| *id == mode);
            if let Some(position) = app.llm_modes.iter().position(|item| item == mode) {
                app.llm_modes.remove(position);
            } else {
                app.llm_modes.push(mode.to_owned());
            }
            app.status = tf(
                app.lang,
                "status.llm_modes",
                &[("modes", &app.llm_modes.join(", "))],
            );
        }
        Action::Button(ButtonId::Start) => {
            return dispatch(app, Action::Run(RunSelection::Pending));
        }
        Action::Button(ButtonId::Stop) => {
            // One graceful cancel per batch: the worker answers `cancelling`, which
            // also sets `cancelled`, so a repeated Esc within the double-press
            // window does not queue more cancels behind the first.
            if app.running() && !app.llm_running() && app.activity.stop() {
                app.cancelled = true;
                return vec![json!({"type": "cancel"})];
            }
        }
        Action::Button(ButtonId::RunLlm) => {
            return request_llm(app);
        }
        Action::Button(ButtonId::CancelLlm) => {
            if esc_should_soft_cancel(app) {
                app.activity.stop();
                app.status = t(app.lang, "status.llm_cancelling").into();
                return vec![json!({"type": "llm_cancel"})];
            }
        }
        Action::Button(ButtonId::ClearQueue) => {
            if !app.running() {
                clear_queue(app);
                app.log(app.status.clone());
            }
        }
        Action::FocusInput => {
            app.focus = Focus::Input;
            if app.input.mode == crate::input::InputMode::Hidden {
                app.input
                    .open(crate::input::InputMode::Paths, String::new());
            }
        }
        Action::Button(ButtonId::ClearLog) => app.logs.clear(),
        Action::ToggleLang => {
            app.lang = app.lang.toggle();
            app.status = t(app.lang, "settings.language_changed").into();
            save_app_settings(app);
        }
        Action::Help => app.help_open = !app.help_open,
        // The Settings list is a cursor list: the wheel moves the cursor and the list
        // slides to keep it visible, so wheel and arrows can never fight each other.
        // One tick (any magnitude: the wheel sends ±3 lines) moves exactly one row.
        Action::Scroll(AreaId::Settings, delta) => {
            let last = setting_rows(app).len().saturating_sub(1);
            app.settings_cursor = (app.settings_cursor as i64 + i64::from(delta.signum()))
                .clamp(0, last as i64) as usize;
        }
        Action::Scroll(area, delta) => {
            if area == AreaId::Log && delta < 0 {
                app.log_follow = false;
            }
            let offset = app.scroll.entry(area).or_default();
            *offset = (i64::from(*offset) + i64::from(delta)).clamp(0, i64::from(u16::MAX)) as u16;
        }
        Action::LlmInput(index) => {
            if index < llm_input_files(app).len() {
                app.llm_input_cursor = index;
                app.llm_mode_cursor = None;
            }
        }
        Action::RemoveLlmInput(index) => {
            // Only files added with `/llm-file` can go; session results are the
            // transcription's own output and leave with `/clear`.
            let files = llm_input_files(app);
            let Some(path) = files.get(index) else {
                return Vec::new();
            };
            if app.result_files.contains(path) {
                app.status = t(app.lang, "llm.cannot_remove_session").into();
                return Vec::new();
            }
            app.llm_extra_files.retain(|item| item != path);
            app.llm_input_cursor = index.min(llm_input_files(app).len().saturating_sub(1));
            app.status = tf(app.lang, "llm.removed", &[("name", &short_name(path))]);
        }
        Action::EditCommand(command) => {
            app.command_menu = None;
            app.input.open(
                crate::input::InputMode::Argument(command),
                format!("{command} "),
            );
            app.selected_command = 0;
            app.focus = Focus::Input;
        }
        Action::SettingsRow(index) => {
            let rows = setting_rows(app);
            if let Some(row) = rows.get(index) {
                let action = row.action.clone();
                app.settings_cursor = index;
                return dispatch(app, action);
            }
        }
        Action::ToggleSetting(name) => {
            // Each flip is exactly what the matching command does, status included.
            match name {
                "mouse" => {
                    app.mouse_enabled = !app.mouse_enabled;
                    app.status = t(
                        app.lang,
                        if app.mouse_enabled {
                            "settings.mouse_on"
                        } else {
                            "settings.mouse_off"
                        },
                    )
                    .into();
                }
                "pets" => {
                    toggle_pets(app);
                    return Vec::new();
                }
                "subtitle_split" => {
                    app.subtitle_sentence_split = !app.subtitle_sentence_split;
                    app.status = tf(
                        app.lang,
                        "status.subtitle_split",
                        &[("value", on_off(app.lang, app.subtitle_sentence_split))],
                    );
                }
                "llm_tools" => {
                    app.llm_allow_tools = !app.llm_allow_tools;
                    app.status = tf(
                        app.lang,
                        "status.llm_tools",
                        &[("value", on_off(app.lang, app.llm_allow_tools))],
                    );
                }
                _ => return Vec::new(),
            }
            save_app_settings(app);
        }
    }
    Vec::new()
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;
    use crate::{
        commands::{command_menu_options, selectable_backends, BACK_MENU_OPTION},
        settings::isolated_config_dir,
        ui::{Action, ButtonId},
        worker::provider_from_menu_option,
    };

    #[test]
    fn llm_start_is_locked_before_worker_acknowledges() {
        let mut app = crate::test_support::ready_app();
        app.llm_extra_files.push("/tmp/transcript.txt".into());
        let first = dispatch(&mut app, Action::Button(ButtonId::RunLlm));
        let second = dispatch(&mut app, Action::Button(ButtonId::RunLlm));
        assert_eq!(first.len(), 1);
        assert_eq!(first[0]["type"], "llm_start");
        assert!(second.is_empty(), "duplicate request before llm_started");
    }

    #[test]
    fn dispatch_matches_the_keyboard_equivalents() {
        let _config = isolated_config_dir();
        let mut app = crate::test_support::ready_app();
        app.queue.add("/tmp/a.wav".into());
        app.queue.add("/tmp/b.wav".into());
        dispatch(&mut app, Action::SelectFile(1));
        assert_eq!(app.queue.selected_index(), Some(1));
        dispatch(&mut app, Action::Tab(Page::Settings));
        assert_eq!(app.page, Page::Settings);
        dispatch(&mut app, Action::OpenMenu("/backend"));
        assert_eq!(app.command_menu.as_deref(), Some("/backend"));
        dispatch(&mut app, Action::MenuItem(1));
        assert_eq!(app.backend, selectable_backends()[1]);
        dispatch(&mut app, Action::ToggleLang);
        assert_eq!(app.lang, Lang::En);
        let commands = dispatch(&mut app, Action::Button(ButtonId::Start));
        assert_eq!(commands[0]["type"], "start");
    }

    #[test]
    fn next_step_follows_the_state_machine() {
        let mut app = crate::test_support::ready_app();
        assert_eq!(next_step(&app), "hint.add_files");
        app.queue.add("/tmp/a.wav".into());
        assert_eq!(next_step(&app), "hint.start");
        app.activity = crate::lifecycle::Activity::Running(crate::lifecycle::JobKind::Asr);
        assert_eq!(next_step(&app), "hint.cancel_batch");
        app.activity = crate::lifecycle::Activity::Idle;
        app.result_files.push("/tmp/a.txt".into());
        app.queue.items[0].state = FileState::Done;
        assert_eq!(next_step(&app), "hint.run_llm");
        app.activity = crate::lifecycle::Activity::Running(crate::lifecycle::JobKind::Llm);
        assert_eq!(next_step(&app), "hint.cancel_llm");
        app.activity = crate::lifecycle::Activity::Idle;
        app.llm_results.push(("summary".into(), "…".into()));
        assert_eq!(next_step(&app), "hint.view_result");
        app.connection.state = crate::lifecycle::ConnectionState::Unavailable;
        assert_eq!(next_step(&app), "hint.worker_down");
    }

    #[test]
    fn file_states_follow_worker_events() {
        let mut app = crate::test_support::ready_app();
        app.queue.add("/tmp/a.wav".into());
        app.queue.add("/tmp/b.wav".into());
        assert_eq!(app.file_state("/tmp/a.wav"), FileState::Pending);
        app.activity.start(JobKind::Asr);
        app.handle_message(json!({"type": "started", "total_files": 2, "backend": "auto"}));
        app.handle_message(json!({
            "type": "file_started", "file": "/tmp/a.wav", "file_index": 0, "total_files": 2
        }));
        assert_eq!(app.file_state("/tmp/a.wav"), FileState::Processing);
        app.handle_message(json!({
            "type": "file_completed", "file": "/tmp/a.wav", "file_index": 0,
            "result": {"success": false, "saved_files": [], "error": "x"}
        }));
        assert_eq!(app.file_state("/tmp/a.wav"), FileState::Failed);
        app.handle_message(json!({"type": "completed", "success": false, "cancelled": true}));
        assert_eq!(
            app.file_state("/tmp/a.wav"),
            FileState::Failed,
            "a finished file keeps its outcome"
        );
        assert_eq!(
            app.file_state("/tmp/b.wav"),
            FileState::Pending,
            "unstarted files remain available for the next batch"
        );
        // The next batch starts from a clean slate.
        app.activity.start(JobKind::Asr);
        app.handle_message(json!({"type": "started", "total_files": 2, "backend": "auto"}));
        assert_eq!(app.file_state("/tmp/b.wav"), FileState::Pending);
    }

    #[test]
    fn stop_sends_one_graceful_cancel_per_batch() {
        let mut app = crate::test_support::ready_app();
        assert!(dispatch(&mut app, Action::Button(ButtonId::Stop)).is_empty());
        app.activity.start(JobKind::Asr);
        app.handle_message(json!({"type": "started", "total_files": 1, "backend": "auto"}));
        let commands = dispatch(&mut app, Action::Button(ButtonId::Stop));
        assert_eq!(commands.len(), 1);
        assert_eq!(commands[0]["type"], "cancel");
        assert!(dispatch(&mut app, Action::Button(ButtonId::Stop)).is_empty());
        app.handle_message(json!({"type": "cancelling", "message": "Cancelling…"}));
        assert!(dispatch(&mut app, Action::Button(ButtonId::Stop)).is_empty());
        app.handle_message(json!({"type": "completed", "success": false, "cancelled": true}));
        app.activity.start(JobKind::Asr);
        app.handle_message(json!({"type": "started", "total_files": 1, "backend": "auto"}));
        assert_eq!(
            dispatch(&mut app, Action::Button(ButtonId::Stop)).len(),
            1,
            "a new batch can be cancelled again"
        );
    }

    #[test]
    fn removing_a_file_forgets_its_state() {
        let mut app = crate::test_support::ready_app();
        app.queue.add("/tmp/a.wav".into());
        app.queue.find_mut("/tmp/a.wav").unwrap().state = FileState::Failed;
        dispatch(&mut app, Action::RemoveFile(0));
        assert!(app.queue.items.is_empty());
        app.queue.add("/tmp/a.wav".into());
        assert_eq!(app.file_state("/tmp/a.wav"), FileState::Pending);
    }

    #[test]
    fn open_menu_prefills_the_command_line_for_typed_parameters() {
        let _config = isolated_config_dir();
        let mut app = crate::test_support::ready_app();
        app.focus = Focus::Params;
        dispatch(&mut app, Action::OpenMenu("/output"));
        assert_eq!(app.command_menu, None);
        assert_eq!(app.input.text(), "/output ");
        assert_eq!(app.focus, Focus::Input);
    }

    #[test]
    fn llm_tools_message_fills_providers_and_menu_shows_status() {
        let mut app = crate::test_support::ready_app();
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
        let mut app = crate::test_support::ready_app();
        app.activity.start(JobKind::Llm);
        app.handle_message(
            json!({"type": "llm_started", "mode": "summary", "index": 1, "total": 1}),
        );
        assert!(app.llm_running() && app.running());
        app.handle_message(json!({"type": "llm_chunk", "mode": "summary", "text": "Итог: "}));
        app.handle_message(json!({"type": "llm_chunk", "mode": "summary", "text": "всё хорошо"}));
        assert_eq!(app.llm_stream, "Итог: всё хорошо");
        app.handle_message(json!({
            "type": "llm_completed", "success": true, "saved_files": ["/tmp/session_llm_summary.txt"],
            "results": [{"mode": "summary", "text": "Итог: всё хорошо"}]
        }));
        assert!(!app.llm_running() && !app.running());
        assert_eq!(
            app.llm_results,
            vec![("summary".to_string(), "Итог: всё хорошо".to_string())]
        );
        assert!(app.llm_stream.is_empty());
    }

    #[test]
    fn a_new_llm_run_clears_the_previous_results() {
        let mut app = crate::test_support::ready_app();
        app.activity.start(JobKind::Llm);
        app.handle_message(
            json!({"type": "llm_started", "mode": "summary", "index": 1, "total": 1}),
        );
        app.handle_message(json!({
            "type": "llm_completed", "success": true, "saved_files": [],
            "results": [{"mode": "summary", "text": "old"}]
        }));
        assert!(!app.llm_results.is_empty());

        app.activity.start(JobKind::Llm);
        app.handle_message(json!({"type": "llm_started", "mode": "tasks", "index": 1, "total": 1}));
        assert!(app.llm_results.is_empty());
        // A completion without `results` (worker failure) must not resurrect the old run.
        app.handle_message(json!({"type": "llm_completed", "success": false, "error": "boom"}));
        assert!(app.llm_results.is_empty());
    }

    #[test]
    fn esc_soft_cancels_an_llm_run_only_once_then_falls_through_to_the_kill_path() {
        let mut app = crate::test_support::ready_app();
        assert!(
            !esc_should_soft_cancel(&app),
            "idle: Esc is not an LLM cancel"
        );

        app.activity.start(JobKind::Llm);
        app.handle_message(
            json!({"type": "llm_started", "mode": "summary", "index": 1, "total": 1}),
        );
        assert!(esc_should_soft_cancel(&app), "first Esc sends llm_cancel");

        app.activity.stop(); // what the first Esc arm sets
        assert!(app.llm_running() && app.running());
        assert!(
            !esc_should_soft_cancel(&app),
            "second Esc must reach the `running` double-Esc kill/restart arm"
        );
    }

    #[test]
    fn esc_closes_the_help_before_it_cancels_a_run() {
        let mut app = crate::test_support::ready_app();
        assert!(!esc_is_cancel(&app), "idle: Esc is not a cancel");
        app.activity.start(JobKind::Asr);
        app.handle_message(json!({"type": "started", "total_files": 1, "backend": "auto"}));
        assert!(esc_is_cancel(&app));
        dispatch(&mut app, Action::Help);
        assert!(
            app.help_open,
            "the header button opens the help during a run"
        );
        assert!(
            !esc_is_cancel(&app),
            "Esc goes to handle_key, which closes the overlay"
        );
        let commands = crate::keys::handle_key(
            &mut app,
            crossterm::event::KeyEvent::new(
                crossterm::event::KeyCode::Esc,
                crossterm::event::KeyModifiers::NONE,
            ),
        );
        assert!(commands.is_empty(), "no cancel was sent");
        assert!(!app.help_open);
        assert!(!app.cancelled);
        assert!(esc_is_cancel(&app), "the next Esc is the cancel again");
    }

    #[test]
    fn worker_restart_resets_every_in_flight_flag() {
        let mut app = crate::test_support::ready_app();
        app.activity.start(JobKind::Llm);
        app.handle_message(
            json!({"type": "llm_started", "mode": "summary", "index": 1, "total": 1}),
        );
        app.handle_message(json!({"type": "llm_chunk", "mode": "summary", "text": "partial"}));
        app.activity.stop();
        app.cancelled = false;

        reset_after_worker_restart(&mut app);

        assert!(!app.running());
        assert!(app.cancelled);
        assert!(!app.llm_running());
        assert_eq!(app.activity, Activity::Idle);
        assert!(app.outbox.is_empty());
        assert!(app.llm_stream.is_empty());
    }

    #[test]
    fn cancelled_llm_run_is_reported_without_an_error() {
        let mut app = crate::test_support::ready_app();
        app.lang = Lang::En;
        app.activity.start(JobKind::Llm);
        app.handle_message(json!({"type": "llm_started", "mode": "tasks", "index": 1, "total": 2}));
        app.handle_message(
            json!({"type": "llm_completed", "success": false, "cancelled": true,
                                  "saved_files": [], "results": []}),
        );
        assert_eq!(app.status, "LLM cancelled");
        assert!(!app.llm_running());
    }
}
