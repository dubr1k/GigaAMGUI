//! The persisted TUI settings as plain data.

use std::collections::HashMap;

use serde::{Deserialize, Serialize};

use crate::theme::DEFAULT_THEME;

#[derive(Clone, Deserialize, Serialize)]
#[serde(default)]
pub(crate) struct TuiSettings {
    pub(crate) pet_enabled: bool,
    /// Interface language, `ru` or `en`; shared with the desktop app's `language`.
    pub(crate) language: String,
    /// Mouse capture in the TUI; off leaves the terminal's own text selection alone.
    pub(crate) mouse: bool,
    pub(crate) backend: String,
    pub(crate) onnx_provider: String,
    pub(crate) diarization_backend: String,
    pub(crate) model: String,
    pub(crate) subtitle_sentence_split: bool,
    pub(crate) subtitle_max_lines: u8,
    pub(crate) subtitle_max_width: u16,
    pub(crate) formats: Vec<String>,
    pub(crate) diarization: bool,
    pub(crate) num_speakers: Option<u32>,
    pub(crate) llm_provider: String,
    pub(crate) llm_api_url: String,
    pub(crate) llm_api_key: String,
    pub(crate) llm_model: String,
    pub(crate) llm_temperature: f64,
    pub(crate) llm_internal_providers: HashMap<String, String>,
    pub(crate) llm_extra_args: HashMap<String, String>,
    pub(crate) llm_tool_paths: HashMap<String, String>,
    pub(crate) llm_allow_tools: bool,
    pub(crate) audio_preprocessing_mode: String,
    /// Colour scheme name (`default`, `mono` or a catalogue theme).
    pub(crate) theme: String,
    /// `/output`; `None` saves results next to each input. TUI-only: the desktop
    /// app's `last_output_dir` is a dialog start folder, not an active choice.
    pub(crate) output_dir: Option<String>,
}

impl Default for TuiSettings {
    fn default() -> Self {
        Self {
            pet_enabled: false,
            language: "ru".into(),
            mouse: true,
            backend: "auto".into(),
            onnx_provider: "auto".into(),
            diarization_backend: "pyannote".into(),
            model: "v3_e2e_rnnt".into(),
            subtitle_sentence_split: true,
            subtitle_max_lines: 2,
            subtitle_max_width: 64,
            formats: vec!["txt".into()],
            diarization: false,
            num_speakers: None,
            llm_provider: std::env::var("LLM_PROVIDER").unwrap_or_else(|_| "API".into()),
            llm_api_url: std::env::var("LLM_API_URL").unwrap_or_default(),
            llm_api_key: std::env::var("LLM_API_KEY").unwrap_or_default(),
            llm_model: std::env::var("LLM_MODEL").unwrap_or_default(),
            llm_temperature: std::env::var("LLM_TEMPERATURE")
                .ok()
                .and_then(|value| value.parse().ok())
                .unwrap_or(0.2),
            llm_internal_providers: HashMap::new(),
            llm_extra_args: HashMap::new(),
            llm_tool_paths: HashMap::new(),
            llm_allow_tools: false,
            audio_preprocessing_mode: "auto".into(),
            theme: DEFAULT_THEME.into(),
            output_dir: None,
        }
    }
}
