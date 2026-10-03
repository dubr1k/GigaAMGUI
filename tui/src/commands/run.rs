//! Running a typed command line and the actions shared with buttons and keys.

use std::path::Path;

use crate::{
    app::{llm_input_files, on_off, request_llm, App, Page},
    i18n::{t, tf, Lang},
    input::InputMode,
    options::{
        backend_is_supported, is_llm_mode, is_model, AUDIO_MODES, DIARIZATION_BACKENDS,
        FORMAT_KEYS, ONNX_PROVIDERS,
    },
    providers::{provider, provider_prefix},
    settings::save_app_settings,
    theme::Theme,
};

use super::*;

pub(crate) fn accept_command_suggestion(app: &mut App, command: &str) {
    if open_command_menu(app, command) {
        app.selected_command = 0;
        return;
    }
    let mode = COMMANDS
        .iter()
        .find(|(name, _)| *name == command)
        .map_or(InputMode::Command, |(name, _)| InputMode::Argument(name));
    app.input.open(mode, command.into());
    if matches!(
        command,
        "/clear"
            | "/pets"
            | "/settings"
            | "/help"
            | "/add"
            | "/undo"
            | "/retry"
            | "/run-selected"
            | "/retry-input"
            | "/reconnect"
    ) {
        run_command(app);
    } else {
        app.input.insert(&' '.to_string());
    }
    app.selected_command = 0;
}

pub(crate) fn run_command(app: &mut App) {
    if app.running() || app.rerun_confirmation.is_some() {
        return;
    }
    let command = app.input.trim().to_owned();
    let mut parts = command.splitn(2, char::is_whitespace);
    // Pasted commands often contain a typographic dash (–/—/−) instead of
    // ASCII `-`; accept them so settings commands remain usable.
    let name = parts
        .next()
        .unwrap_or_default()
        .replace(['–', '—', '−'], "-");
    let argument = parts.next().unwrap_or_default().trim();
    let action = match name.as_str() {
        "/add" => Some(crate::action::Action::AddFiles),
        "/undo" => Some(crate::action::Action::UndoRemove),
        "/retry" => Some(crate::action::Action::Run(
            crate::queue::RunSelection::Failed,
        )),
        "/run-selected" => Some(crate::action::Action::Run(
            crate::queue::RunSelection::Selected,
        )),
        "/retry-input" => Some(crate::action::Action::RetryInputs),
        "/reconnect" => Some(crate::action::Action::Reconnect),
        _ => None,
    };
    if let Some(action) = action {
        app.input.close();
        let messages = crate::app::dispatch(app, action);
        app.outbox.extend(messages);
        return;
    }
    let mut accepted = true;
    match name.as_str() {
        "/exit" => {
            app.exit_requested = true;
            app.status = t(app.lang, "status.exiting").into();
        }
        "/settings" => {
            app.page = Page::Settings;
            app.status = t(app.lang, "settings.opened").into();
        }
        "/help" => app.help_open = !app.help_open,
        "/lang" => match Lang::parse(argument) {
            Some(lang) => {
                app.lang = lang;
                app.status = t(lang, "settings.language_changed").into();
                save_app_settings(app);
            }
            None => {
                accepted = false;
                app.status = t(app.lang, "usage.lang").into();
            }
        },
        "/theme" if argument.is_empty() => {
            open_command_menu(app, "/theme");
        }
        "/theme" => match Theme::by_name(argument) {
            Some(theme) => {
                set_theme(app, theme);
                save_app_settings(app);
            }
            None => {
                accepted = false;
                app.status = format!(
                    "{} {}",
                    tf(app.lang, "err.theme_unknown", &[("value", argument)]),
                    t(app.lang, "usage.theme")
                );
            }
        },
        "/mouse" => match argument {
            "on" | "off" => {
                app.mouse_enabled = argument == "on";
                app.status = t(
                    app.lang,
                    if app.mouse_enabled {
                        "settings.mouse_on"
                    } else {
                        "settings.mouse_off"
                    },
                )
                .into();
                save_app_settings(app);
            }
            _ => {
                accepted = false;
                app.status = t(app.lang, "usage.mouse").into();
            }
        },
        "/llm-mode" if is_llm_mode(argument) => {
            app.llm_modes = vec![argument.into()];
            app.status = tf(
                app.lang,
                "status.llm_modes",
                &[("modes", &app.llm_modes.join(", "))],
            );
        }
        "/llm-mode" => {
            accepted = false;
            app.status = t(app.lang, "usage.llm-mode").into();
        }
        "/llm-prompt" if !argument.is_empty() => {
            app.llm_prompt = argument.into();
            app.status = t(app.lang, "status.llm_prompt_saved").into();
        }
        "/llm-prompt" => {
            accepted = false;
            app.status = t(app.lang, "usage.llm-prompt").into();
        }
        "/llm-run" => {
            let commands = request_llm(app);
            app.outbox.extend(commands);
        }
        "/llm-file" => match normalize_path(argument) {
            Ok(path)
                if matches!(
                    Path::new(&path)
                        .extension()
                        .and_then(|value| value.to_str()),
                    Some("txt" | "md" | "srt" | "vtt")
                ) =>
            {
                app.llm_extra_files.push(path);
                app.status = tf(
                    app.lang,
                    "status.llm_inputs",
                    &[("n", &llm_input_files(app).len().to_string())],
                );
            }
            Ok(_) => {
                accepted = false;
                app.status = t(app.lang, "status.llm_file_type").into();
            }
            Err(error) => {
                accepted = false;
                app.status = error.message(app.lang);
            }
        },
        "/llm-api-url" if !argument.is_empty() => {
            app.llm_api_url = argument.into();
            app.status = t(app.lang, "status.llm_api_url_saved").into();
            save_app_settings(app);
        }
        "/llm-api-key" if !argument.is_empty() => {
            app.llm_api_key = argument.into();
            app.status = t(app.lang, "status.llm_api_key_saved").into();
            save_app_settings(app);
        }
        "/llm-model" if !argument.is_empty() => {
            app.llm_model = argument.into();
            app.status = t(app.lang, "status.llm_model_saved").into();
            save_app_settings(app);
        }
        "/llm-model" => {
            let _ = open_command_menu(app, "/settings-model");
        }
        "/llm-temperature" => match argument.parse::<f64>() {
            Ok(value) if (0.0..=2.0).contains(&value) => {
                app.llm_temperature = value;
                app.status = t(app.lang, "status.llm_temperature_saved").into();
                save_app_settings(app);
            }
            _ => {
                accepted = false;
                app.status = t(app.lang, "status.temperature_range").into();
            }
        },
        "/llm-provider-name" if !provider(&app.llm_provider).internal_provider => {
            accepted = false;
            app.status = t(app.lang, "status.provider_name_pi_only").into();
        }
        "/llm-provider-name" => {
            let prefix = provider_prefix(&app.llm_provider).to_owned();
            if argument.is_empty() {
                app.llm_internal_providers.remove(&prefix);
                app.status = t(app.lang, "status.provider_name_cleared").into();
            } else {
                app.llm_internal_providers.insert(prefix, argument.into());
                app.status = tf(app.lang, "status.provider_name", &[("value", argument)]);
            }
            save_app_settings(app);
        }
        "/llm-args" if app.llm_provider == "API" => {
            accepted = false;
            app.status = t(app.lang, "status.args_cli_only").into();
        }
        "/llm-args" => {
            let prefix = provider_prefix(&app.llm_provider).to_owned();
            if argument.is_empty() {
                app.llm_extra_args.remove(&prefix);
                app.status = t(app.lang, "status.args_cleared").into();
            } else {
                app.llm_extra_args.insert(prefix, argument.into());
                app.status = tf(app.lang, "status.args", &[("value", argument)]);
            }
            save_app_settings(app);
        }
        "/llm-path" if app.llm_provider == "API" => {
            accepted = false;
            app.status = t(app.lang, "status.path_cli_only").into();
        }
        "/llm-path" => {
            let prefix = provider_prefix(&app.llm_provider).to_owned();
            if argument.is_empty() {
                app.llm_tool_paths.remove(&prefix);
                app.status = t(app.lang, "status.path_reset").into();
            } else {
                app.llm_tool_paths.insert(prefix, argument.into());
                app.status = tf(app.lang, "status.path_checking", &[("value", argument)]);
            }
            if app.llm_provider != "Other" {
                app.llm_tool_check_requested = Some((app.llm_provider.clone(), argument.into()));
            }
            save_app_settings(app);
        }
        "/llm-tools" if matches!(argument, "on" | "off") => {
            app.llm_allow_tools = argument == "on";
            app.status = tf(
                app.lang,
                "status.llm_tools",
                &[("value", on_off(app.lang, app.llm_allow_tools))],
            );
            save_app_settings(app);
        }
        "/llm-tools" => {
            accepted = false;
            app.status = t(app.lang, "usage.llm-tools").into();
        }
        "/pets" => toggle_pets(app),
        "/output" if argument.is_empty() => {
            accepted = false;
            app.status = t(app.lang, "usage.output").into();
        }
        "/output" if argument == "-" => {
            app.output_dir = None;
            app.status = t(app.lang, "status.output_dir_reset").into();
            save_app_settings(app);
        }
        "/output" => match prepare_output_dir(argument, app.lang) {
            Ok(path) => {
                app.output_dir = Some(path);
                app.status = t(app.lang, "status.output_dir_updated").into();
                save_app_settings(app);
            }
            Err(error) => {
                accepted = false;
                app.status = tf(app.lang, "status.output_dir_error", &[("error", &error)]);
            }
        },
        "/backend" if backend_is_supported(&argument.to_ascii_lowercase()) => {
            app.backend = argument.to_ascii_lowercase();
            app.status = tf(app.lang, "status.backend", &[("value", &app.backend)]);
            save_app_settings(app);
        }
        "/backend" => {
            accepted = false;
            app.status = backend_usage(app.lang);
        }
        "/onnx-provider" if ONNX_PROVIDERS.contains(&argument.to_ascii_lowercase().as_str()) => {
            app.onnx_provider = argument.to_ascii_lowercase();
            app.status = tf(
                app.lang,
                "status.onnx_provider",
                &[("value", &app.onnx_provider)],
            );
            save_app_settings(app);
        }
        "/onnx-provider" => {
            accepted = false;
            app.status = t(app.lang, "usage.onnx-provider").into();
        }
        "/model" if is_model(argument) => {
            app.model = argument.into();
            app.status = tf(app.lang, "status.model", &[("value", &app.model)]);
            save_app_settings(app);
        }
        "/model" => {
            accepted = false;
            app.status = t(app.lang, "usage.model").into();
        }
        "/formats" => {
            let formats: Vec<String> = argument
                .split(',')
                .map(str::trim)
                .filter(|format| FORMAT_KEYS.contains(format))
                .map(str::to_owned)
                .collect();
            if formats.is_empty() {
                app.status = t(app.lang, "usage.formats").into();
            } else {
                app.formats = formats;
                app.status = tf(
                    app.lang,
                    "status.formats",
                    &[("formats", &app.formats.join(", "))],
                );
                save_app_settings(app);
            }
        }
        "/subtitle-split" if matches!(argument, "on" | "off") => {
            app.subtitle_sentence_split = argument == "on";
            app.status = tf(
                app.lang,
                "status.subtitle_split",
                &[("value", on_off(app.lang, app.subtitle_sentence_split))],
            );
            save_app_settings(app);
        }
        "/subtitle-split" => {
            accepted = false;
            app.status = t(app.lang, "usage.subtitle-split").into();
        }
        "/subtitle-lines" => match argument.parse::<u8>() {
            Ok(value) if (1..=4).contains(&value) => {
                app.subtitle_max_lines = value;
                app.status = tf(
                    app.lang,
                    "status.subtitle_lines",
                    &[("value", &value.to_string())],
                );
                save_app_settings(app);
            }
            _ => app.status = t(app.lang, "status.subtitle_lines_range").into(),
        },
        "/subtitle-width" => match argument.parse::<u16>() {
            Ok(value) if (20..=100).contains(&value) => {
                app.subtitle_max_width = value;
                app.status = tf(
                    app.lang,
                    "status.subtitle_width",
                    &[("value", &value.to_string())],
                );
                save_app_settings(app);
            }
            _ => app.status = t(app.lang, "status.subtitle_width_range").into(),
        },
        "/diarize" if matches!(argument, "on" | "off") => {
            app.diarization = argument == "on";
            app.status = tf(
                app.lang,
                "status.diarization",
                &[("value", on_off(app.lang, app.diarization))],
            );
            save_app_settings(app);
        }
        "/diarize" => {
            accepted = false;
            app.status = t(app.lang, "usage.diarize").into();
        }
        "/audio-mode" if AUDIO_MODES.contains(&argument) => {
            app.audio_preprocessing_mode = argument.into();
            app.status = tf(app.lang, "status.audio_mode", &[("value", argument)]);
            save_app_settings(app);
        }
        "/audio-mode" => {
            accepted = false;
            app.status = t(app.lang, "usage.audio-mode").into();
        }
        "/diarization-backend" if DIARIZATION_BACKENDS.contains(&argument) => {
            app.diarization_backend = argument.into();
            if app.diarization_backend == "sortformer" {
                app.num_speakers = None;
            }
            app.status = tf(
                app.lang,
                "status.diarization_backend",
                &[("value", &app.diarization_backend)],
            );
            save_app_settings(app);
        }
        "/diarization-backend" => {
            accepted = false;
            app.status = t(app.lang, "usage.diarization-backend").into();
        }
        "/speakers" if app.diarization_backend == "sortformer" => {
            app.num_speakers = None;
            app.status = t(app.lang, "status.sortformer_auto").into();
            save_app_settings(app);
        }
        "/speakers" if argument == "auto" => {
            app.num_speakers = None;
            app.status = tf(
                app.lang,
                "status.speakers",
                &[("value", t(app.lang, "value.auto"))],
            );
            save_app_settings(app);
        }
        "/speakers" => match argument.parse::<u32>() {
            Ok(value) if value > 0 => {
                app.num_speakers = Some(value);
                app.status = tf(
                    app.lang,
                    "status.speakers",
                    &[("value", &value.to_string())],
                );
                save_app_settings(app);
            }
            _ => {
                accepted = false;
                app.status = t(app.lang, "usage.speakers").into();
            }
        },
        "/clear" => clear_queue(app),
        "/remove" => match argument.parse::<usize>() {
            Ok(index) if index > 0 && index <= app.queue.items.len() => {
                app.queue.select(index - 1);
                remove_selected_file(app);
            }
            _ => {
                accepted = false;
                app.status = t(app.lang, "usage.remove").into();
            }
        },
        _ => {
            accepted = false;
            app.status = tf(app.lang, "status.unknown_command", &[("name", &name)]);
        }
    }
    app.log(app.status.clone());
    if accepted {
        app.input.close();
    }
}

/// `/pets` and the Settings row: shows or hides the companion and persists it.
pub(crate) fn toggle_pets(app: &mut App) {
    if app.pet_enabled {
        app.clear_pet_layer();
        app.pet_enabled = false;
        app.pet_image = None;
        app.status = t(app.lang, "status.pets_off").into();
        save_app_settings(app);
    } else if let Err(error) = app.refresh_pet_image() {
        app.status = error;
    } else {
        app.pet_enabled = true;
        app.status = t(app.lang, "status.pets_on").into();
        save_app_settings(app);
    }
}

pub(crate) fn clear_queue(app: &mut App) {
    app.cancel_inputs();
    app.queue.clear();
    app.status = t(app.lang, "status.queue_cleared").into();
}

pub(crate) fn remove_selected_file(app: &mut App) {
    let Some(file) = app.queue.remove_selected() else {
        app.status = t(app.lang, "status.no_file_selected").into();
        return;
    };
    app.status = tf(app.lang, "queue.removed", &[("name", &short_name(&file))]);
    app.log(app.status.clone());
}
