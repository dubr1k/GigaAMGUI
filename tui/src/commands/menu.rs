//! Choice menus: their options, opening one on the current value, applying a pick.

use crate::{
    app::{on_off, App},
    i18n::{t, tf, Lang},
    input::InputMode,
    options::{
        selectable_backends, AUDIO_MODES, DIARIZATION_BACKENDS, FORMAT_KEYS, LLM_MODES,
        MODEL_OPTIONS, ONNX_PROVIDERS, SPEAKER_CHOICES,
    },
    providers::{provider, PROVIDERS},
    settings::save_app_settings,
    theme::Theme,
};

use super::*;

pub(crate) fn command_menu_options(app: &App) -> Vec<String> {
    match app.command_menu.as_deref() {
        Some("/queue-actions") => [
            "queue.run_pending",
            "queue.run_failed",
            "queue.run_selected",
            "queue.full_path",
            "queue.undo",
        ]
        .iter()
        .map(|key| t(app.lang, key).to_owned())
        .chain(std::iter::once(BACK_MENU_OPTION.to_owned()))
        .collect(),
        Some("/backend") => with_back(selectable_backends().iter().copied()),
        Some("/onnx-provider") => with_back(ONNX_PROVIDERS),
        Some("/model") => MODEL_OPTIONS
            .iter()
            .map(|(id, label)| format!("{id} · {label}"))
            .chain(std::iter::once(BACK_MENU_OPTION.to_owned()))
            .collect(),
        Some("/diarize") => with_back(["on", "off"]),
        Some("/audio-mode") => with_back(AUDIO_MODES),
        Some("/diarization-backend") => with_back(DIARIZATION_BACKENDS),
        Some("/speakers") => with_back(SPEAKER_CHOICES),
        Some("/settings-provider") => provider_menu_options(app),
        Some("/settings-model") => llm_model_options(&app.llm_provider),
        Some("/lang") => vec![
            t(Lang::Ru, "lang.name").to_owned(),
            t(Lang::En, "lang.name").to_owned(),
            BACK_MENU_OPTION.to_owned(),
        ],
        Some("/theme") => with_back(Theme::names()),
        Some("/llm-mode") => checkboxes(LLM_MODES.iter().map(|(id, _)| *id), &app.llm_modes),
        Some("/formats") => checkboxes(FORMAT_KEYS, &app.formats),
        _ => Vec::new(),
    }
}

/// Menu values followed by the Back entry.
fn with_back<'a>(values: impl IntoIterator<Item = &'a str>) -> Vec<String> {
    values
        .into_iter()
        .map(str::to_owned)
        .chain(std::iter::once(BACK_MENU_OPTION.to_owned()))
        .collect()
}

/// A multi-select menu: `[x] value` / `[ ] value`, then Back.
fn checkboxes<'a>(values: impl IntoIterator<Item = &'a str>, selected: &[String]) -> Vec<String> {
    values
        .into_iter()
        .map(|value| {
            let mark = if selected.iter().any(|item| item == value) {
                "x"
            } else {
                " "
            };
            format!("[{mark}] {value}")
        })
        .chain(std::iter::once(BACK_MENU_OPTION.to_owned()))
        .collect()
}

/// The provider menu: the worker's registry once it answered `llm_tools`, the
/// built-in table before that, each with its install status.
pub(crate) fn provider_menu_options(app: &App) -> Vec<String> {
    let providers: Vec<String> = if app.llm_providers.is_empty() {
        PROVIDERS
            .iter()
            .map(|provider| provider.name.to_owned())
            .collect()
    } else {
        app.llm_providers.clone()
    };
    providers
        .into_iter()
        .map(|provider| match app.llm_tool(&provider) {
            Some(tool) if tool.status == "found" => format!(
                "{provider} · {}",
                tool.version
                    .as_deref()
                    .unwrap_or(t(app.lang, "value.found"))
            ),
            Some(tool) if tool.status == "broken" => {
                format!("{provider} · {}", t(app.lang, "llm.broken"))
            }
            Some(tool) if tool.status == "missing" => {
                format!("{provider} · {}", t(app.lang, "llm.not_installed"))
            }
            _ => provider,
        })
        .chain(std::iter::once(BACK_MENU_OPTION.to_owned()))
        .collect()
}

pub(crate) fn provider_from_menu_option(option: &str) -> &str {
    option.split(" · ").next().unwrap_or(option).trim()
}

fn llm_model_options(provider_name: &str) -> Vec<String> {
    provider(provider_name)
        .models
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
            | "/queue-actions"
            | "/onnx-provider"
            | "/model"
            | "/diarize"
            | "/diarization-backend"
            | "/audio-mode"
            | "/formats"
            | "/speakers"
            | "/llm-mode"
            | "/lang"
            | "/theme"
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
        } else if command == "/theme" {
            Theme::names()
                .iter()
                .position(|name| *name == app.theme.name)
                .unwrap_or(0)
        } else {
            0
        };
        app.status = t(app.lang, "status.choose_option").into();
        true
    } else {
        false
    }
}

/// Switches the palette; `Theme` is parsed once here, never per frame.
pub(super) fn set_theme(app: &mut App, theme: Theme) {
    app.status = tf(app.lang, "status.theme_set", &[("value", theme.name)]);
    app.theme = theme;
}

pub(crate) fn apply_command_menu(app: &mut App) {
    let options = command_menu_options(app);
    let Some(option) = options.get(app.command_menu_index.min(options.len().saturating_sub(1)))
    else {
        return;
    };
    if option == BACK_MENU_OPTION {
        app.command_menu = None;
        app.input.close();
        app.status = t(app.lang, "status.menu_closed").into();
        app.log(app.status.clone());
        return;
    }
    let command = app.command_menu.clone().unwrap_or_default();
    match command.as_str() {
        "/queue-actions" => {
            use crate::{queue::RunSelection, ui::Action};
            let action = match app.command_menu_index {
                0 => Action::Run(RunSelection::Pending),
                1 => Action::Run(RunSelection::Failed),
                2 => Action::Run(RunSelection::Selected),
                3 => Action::ShowPath(true),
                _ => Action::UndoRemove,
            };
            app.command_menu = None;
            app.input.close();
            let messages = crate::app::dispatch(app, action);
            app.outbox.extend(messages);
        }
        "/backend" => {
            app.backend = option.clone();
            app.status = tf(app.lang, "status.backend", &[("value", &app.backend)]);
            app.command_menu = None;
            app.input.close();
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
            app.input.close();
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
            app.input.close();
            save_app_settings(app);
        }
        "/settings-provider" => {
            app.llm_provider = provider_from_menu_option(option).to_owned();
            app.command_menu = None;
            app.input.close();
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
            app.input.close();
            app.status = t(app.lang, "settings.language_changed").into();
            save_app_settings(app);
        }
        "/theme" => {
            if let Some(theme) = Theme::by_name(option) {
                set_theme(app, theme);
                app.command_menu = None;
                app.input.close();
                save_app_settings(app);
            }
        }
        "/settings-model" if option == ENTER_MANUALLY_OPTION => {
            app.command_menu = None;
            app.input
                .open(InputMode::Argument("/llm-model"), "/llm-model ".into());
            app.status = t(app.lang, "status.enter_model_name").into();
        }
        "/settings-model" => {
            app.llm_model = if option == "default" {
                String::new()
            } else {
                option.clone()
            };
            app.command_menu = None;
            app.input.close();
            app.status = if app.llm_model.is_empty() {
                t(app.lang, "status.llm_default_model").into()
            } else {
                tf(app.lang, "status.llm_model", &[("value", &app.llm_model)])
            };
            save_app_settings(app);
        }
        "/llm-mode" => {
            let mode = option.trim_start_matches(['[', 'x', ' ', ']']).trim();
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
            app.input.close();
            save_app_settings(app);
        }
        "/audio-mode" => {
            app.audio_preprocessing_mode = option.clone();
            app.status = tf(app.lang, "status.audio_mode", &[("value", option)]);
            app.command_menu = None;
            app.input.close();
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
            app.input.close();
            save_app_settings(app);
        }
        "/speakers" if app.diarization_backend == "sortformer" => {
            app.num_speakers = None;
            app.status = t(app.lang, "status.sortformer_auto").into();
            app.command_menu = None;
            app.input.close();
            save_app_settings(app);
        }
        "/speakers" => {
            app.num_speakers = option.parse().ok();
            app.status = tf(app.lang, "status.speakers", &[("value", option)]);
            app.command_menu = None;
            app.input.close();
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
