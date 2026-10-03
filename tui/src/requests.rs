//! Worker commands built from the interactive state. They used to live in
//! worker.rs, which made the process/protocol module depend on `App` (and on
//! the command menus) and tied app, commands and worker into one cycle.

use serde_json::{json, Value};

use crate::{
    app::{llm_input_files, App},
    settings::TuiSettings,
    worker::llm_settings_from,
};

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

pub(crate) fn start_payload(app: &App, files: &[String]) -> Value {
    json!({
        "type": "start",
        "files": files,
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::worker::LlmTool;

    #[test]
    fn llm_settings_payload_uses_discovered_tool_paths() {
        let mut app = crate::test_support::ready_app();
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
