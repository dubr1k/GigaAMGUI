//! The interactive state and its small accessors.

use std::{
    collections::{HashMap, VecDeque},
    time::{Duration, Instant},
};

use serde_json::Value;

use crate::{
    action::{AreaId, HitMap},
    batch::{BatchRun, BatchSummary, FileProgress},
    i18n::{t, tf, Lang},
    lifecycle::{Activity, Connection, ConnectionState},
    pets::PetState,
    queue::QueueState,
    session::PendingInput,
    theme::{Palette, Theme},
    worker::LlmTool,
};

#[cfg(test)]
use super::FileState;
use super::{Focus, Page};

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
    /// The file the running batch is on and how far it got.
    pub(crate) progress: FileProgress,
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
    /// The image companion (`/pets`).
    pub(crate) pet: PetState,
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
    /// The persisted settings as this session last loaded or saved them; a save
    /// writes only the fields that differ (see `settings::save_app_settings`).
    pub(crate) settings_baseline: Option<serde_json::Map<String, Value>>,
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
            progress: FileProgress::default(),
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
            pet: PetState::default(),
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
            settings_baseline: None,
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

    /// The worker's last word on a provider's CLI (`llm_tools`/`llm_tool_check`).
    pub(crate) fn llm_tool(&self, provider: &str) -> Option<&LlmTool> {
        self.llm_tools.iter().find(|tool| tool.provider == provider)
    }

    pub(crate) fn log(&mut self, line: impl Into<String>) {
        self.logs.push(line.into());
        if self.logs.len() > 200 {
            self.logs.remove(0);
        }
    }

    /// Lookup by path for the tests; drawing uses each row's own state.
    #[cfg(test)]
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
}
