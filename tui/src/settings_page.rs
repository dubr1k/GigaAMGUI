//! The Settings page as data: every setting with its current value and what
//! `Enter` or a click does. Drawing lives in `ui/settings.rs`; the cursor and
//! `Action::SettingsRow` in `app` use these rows without depending on the UI.

use crate::{action::Action, app::App, i18n::t, providers::provider_prefix};

/// One row of the list: the label key, the value to show and what `Enter` does.
pub(crate) struct SettingRow {
    pub(crate) key: &'static str,
    pub(crate) value: String,
    pub(crate) action: Action,
}

fn row(key: &'static str, value: impl Into<String>, action: Action) -> SettingRow {
    SettingRow {
        key,
        value: value.into(),
        action,
    }
}

fn on_off(app: &App, on: bool) -> &'static str {
    t(app.lang, if on { "value.on" } else { "value.off" })
}

/// A per-provider value (`/llm-path`, `/llm-args`, …) or its placeholder.
fn provider_value<'a>(
    app: &'a App,
    map: &'a std::collections::HashMap<String, String>,
    placeholder: &'static str,
) -> &'a str {
    map.get(provider_prefix(&app.llm_provider))
        .map_or_else(|| t(app.lang, placeholder), String::as_str)
}

/// The rows in the order of the spec: interface, engine, subtitles, LLM.
pub(crate) fn rows(app: &App) -> Vec<SettingRow> {
    let text_or = |value: &str, placeholder: &'static str| {
        if value.is_empty() {
            t(app.lang, placeholder).to_owned()
        } else {
            value.to_owned()
        }
    };
    vec![
        row(
            "settings.language",
            t(app.lang, "lang.name"),
            Action::OpenMenu("/lang"),
        ),
        row(
            "settings.mouse",
            on_off(app, app.mouse_enabled),
            Action::ToggleSetting("mouse"),
        ),
        row("settings.theme", app.theme.name, Action::OpenMenu("/theme")),
        row(
            "settings.pets",
            on_off(app, app.pet.enabled),
            Action::ToggleSetting("pets"),
        ),
        row(
            "settings.backend",
            app.backend.as_str(),
            Action::OpenMenu("/backend"),
        ),
        row(
            "settings.onnx_provider",
            app.onnx_provider.as_str(),
            Action::OpenMenu("/onnx-provider"),
        ),
        row(
            "settings.model",
            app.model.as_str(),
            Action::OpenMenu("/model"),
        ),
        row(
            "settings.diarization_backend",
            app.diarization_backend.as_str(),
            Action::OpenMenu("/diarization-backend"),
        ),
        row(
            "settings.audio",
            app.audio_preprocessing_mode.as_str(),
            Action::OpenMenu("/audio-mode"),
        ),
        row(
            "settings.output",
            app.output_dir
                .clone()
                .unwrap_or_else(|| t(app.lang, "value.next_to_file").to_owned()),
            Action::EditCommand("/output"),
        ),
        row(
            "settings.subtitle_split",
            on_off(app, app.subtitle_sentence_split),
            Action::ToggleSetting("subtitle_split"),
        ),
        row(
            "settings.subtitle_lines",
            app.subtitle_max_lines.to_string(),
            Action::EditCommand("/subtitle-lines"),
        ),
        row(
            "settings.subtitle_width",
            app.subtitle_max_width.to_string(),
            Action::EditCommand("/subtitle-width"),
        ),
        row(
            "settings.llm_provider",
            app.llm_provider.as_str(),
            Action::OpenMenu("/settings-provider"),
        ),
        row(
            "settings.llm_model",
            text_or(&app.llm_model, "value.default"),
            Action::OpenMenu("/settings-model"),
        ),
        row(
            "settings.llm_api_url",
            text_or(&app.llm_api_url, "value.not_set"),
            Action::EditCommand("/llm-api-url"),
        ),
        row(
            "settings.llm_api_key",
            if app.llm_api_key.is_empty() {
                t(app.lang, "value.not_set")
            } else {
                "••••"
            },
            Action::EditCommand("/llm-api-key"),
        ),
        row(
            "settings.llm_temperature",
            app.llm_temperature.to_string(),
            Action::EditCommand("/llm-temperature"),
        ),
        row(
            "settings.llm_path",
            provider_value(app, &app.llm_tool_paths, "value.auto"),
            Action::EditCommand("/llm-path"),
        ),
        row(
            "settings.llm_provider_name",
            provider_value(app, &app.llm_internal_providers, "value.default"),
            Action::EditCommand("/llm-provider-name"),
        ),
        row(
            "settings.llm_args",
            provider_value(app, &app.llm_extra_args, "value.none"),
            Action::EditCommand("/llm-args"),
        ),
        row(
            "settings.llm_tools",
            on_off(app, app.llm_allow_tools),
            Action::ToggleSetting("llm_tools"),
        ),
    ]
}
