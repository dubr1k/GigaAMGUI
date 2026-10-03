//! A setting change, however it was asked for: a typed command (`/backend onnx`),
//! a pick in its menu, a key or a Settings row. Each used to set the field,
//! write the status line and save on its own, so the paths drifted apart. They
//! now build a [`Setting`] and [`Setting::apply`] it; closing menus, logging and
//! accepting the input line stay with the caller.

use crate::{
    app::{on_off, App},
    i18n::{t, tf, Lang},
    options::{backend_is_supported, is_model, AUDIO_MODES, DIARIZATION_BACKENDS, ONNX_PROVIDERS},
    settings::save_app_settings,
    theme::Theme,
};

#[derive(Clone, Debug, PartialEq)]
pub(crate) enum Setting {
    Backend(String),
    OnnxProvider(String),
    Model(String),
    Diarization(bool),
    AudioMode(String),
    DiarizationBackend(String),
    /// The count (`None`: automatic) and how the status line names it.
    Speakers(Option<u32>, String),
    Mouse(bool),
    SubtitleSplit(bool),
    LlmTools(bool),
    Language(Lang),
    /// A theme name that `Theme::by_name` knows.
    Theme(String),
}

fn on_off_argument(argument: &str) -> Option<bool> {
    match argument {
        "on" => Some(true),
        "off" => Some(false),
        _ => None,
    }
}

impl Setting {
    /// A typed `/command argument`. `None`: not a setting command; `Err`: the
    /// usage text for an argument the command does not accept.
    pub(crate) fn parse(name: &str, argument: &str, app: &App) -> Option<Result<Self, String>> {
        let lang = app.lang;
        let usage = |key: &str| Err(t(lang, key).to_owned());
        let lower = argument.to_ascii_lowercase();
        Some(match name {
            "/backend" if backend_is_supported(&lower) => Ok(Self::Backend(lower)),
            "/backend" => Err(crate::commands::backend_usage(lang)),
            "/onnx-provider" if ONNX_PROVIDERS.contains(&lower.as_str()) => {
                Ok(Self::OnnxProvider(lower))
            }
            "/onnx-provider" => usage("usage.onnx-provider"),
            "/model" if is_model(argument) => Ok(Self::Model(argument.into())),
            "/model" => usage("usage.model"),
            "/diarize" if matches!(argument, "on" | "off") => {
                Ok(Self::Diarization(argument == "on"))
            }
            "/diarize" => usage("usage.diarize"),
            "/audio-mode" if AUDIO_MODES.contains(&argument) => {
                Ok(Self::AudioMode(argument.into()))
            }
            "/audio-mode" => usage("usage.audio-mode"),
            "/diarization-backend" if DIARIZATION_BACKENDS.contains(&argument) => {
                Ok(Self::DiarizationBackend(argument.into()))
            }
            "/diarization-backend" => usage("usage.diarization-backend"),
            // Sortformer counts speakers itself: any argument ends up automatic.
            "/speakers" if app.diarization_backend == "sortformer" => {
                Ok(Self::Speakers(None, String::new()))
            }
            "/speakers" if argument == "auto" => {
                Ok(Self::Speakers(None, t(lang, "value.auto").into()))
            }
            "/speakers" => match argument.parse::<u32>() {
                Ok(count) if count > 0 => Ok(Self::Speakers(Some(count), count.to_string())),
                _ => usage("usage.speakers"),
            },
            "/mouse" => on_off_argument(argument)
                .map(Self::Mouse)
                .map_or_else(|| usage("usage.mouse"), Ok),
            "/subtitle-split" => on_off_argument(argument)
                .map(Self::SubtitleSplit)
                .map_or_else(|| usage("usage.subtitle-split"), Ok),
            "/llm-tools" => on_off_argument(argument)
                .map(Self::LlmTools)
                .map_or_else(|| usage("usage.llm-tools"), Ok),
            "/lang" => Lang::parse(argument)
                .map(Self::Language)
                .map_or_else(|| usage("usage.lang"), Ok),
            // Without a name `/theme` opens its menu: not a change.
            "/theme" if argument.is_empty() => return None,
            "/theme" if Theme::by_name(argument).is_some() => Ok(Self::Theme(argument.into())),
            "/theme" => Err(format!(
                "{} {}",
                tf(lang, "err.theme_unknown", &[("value", argument)]),
                t(lang, "usage.theme")
            )),
            _ => return None,
        })
    }

    /// A pick from the menu of `command`; `None` for menus that are not settings
    /// (or not migrated to this type yet).
    pub(crate) fn from_menu(command: &str, option: &str) -> Option<Self> {
        Some(match command {
            "/backend" => Self::Backend(option.into()),
            "/onnx-provider" => Self::OnnxProvider(option.into()),
            // The menu shows `id · label`.
            "/model" => Self::Model(
                option
                    .split_whitespace()
                    .next()
                    .unwrap_or("v3_e2e_rnnt")
                    .into(),
            ),
            "/diarize" => Self::Diarization(option == "on"),
            "/audio-mode" => Self::AudioMode(option.into()),
            "/diarization-backend" => Self::DiarizationBackend(option.into()),
            // The menu names the pick as listed (`auto`, `2`, …).
            "/speakers" => Self::Speakers(option.parse().ok(), option.into()),
            // The menu lists the language names, Russian first.
            "/lang" if option == t(Lang::En, "lang.name") => Self::Language(Lang::En),
            "/lang" => Self::Language(Lang::Ru),
            "/theme" if Theme::by_name(option).is_some() => Self::Theme(option.into()),
            _ => return None,
        })
    }

    /// Sets the value, reports it in the status line and saves the settings.
    pub(crate) fn apply(self, app: &mut App) {
        let lang: Lang = app.lang;
        match self {
            Self::Backend(value) => {
                app.backend = value;
                app.status = tf(lang, "status.backend", &[("value", &app.backend)]);
            }
            Self::OnnxProvider(value) => {
                app.onnx_provider = value;
                app.status = tf(
                    lang,
                    "status.onnx_provider",
                    &[("value", &app.onnx_provider)],
                );
            }
            Self::Model(value) => {
                app.model = value;
                app.status = tf(lang, "status.model", &[("value", &app.model)]);
            }
            Self::Diarization(on) => {
                app.diarization = on;
                app.status = tf(lang, "status.diarization", &[("value", on_off(lang, on))]);
            }
            Self::AudioMode(value) => {
                app.status = tf(lang, "status.audio_mode", &[("value", &value)]);
                app.audio_preprocessing_mode = value;
            }
            Self::DiarizationBackend(value) => {
                app.diarization_backend = value;
                if app.diarization_backend == "sortformer" {
                    app.num_speakers = None;
                }
                app.status = tf(
                    lang,
                    "status.diarization_backend",
                    &[("value", &app.diarization_backend)],
                );
            }
            Self::Speakers(_, _) if app.diarization_backend == "sortformer" => {
                app.num_speakers = None;
                app.status = t(lang, "status.sortformer_auto").into();
            }
            Self::Speakers(count, label) => {
                app.num_speakers = count;
                app.status = tf(lang, "status.speakers", &[("value", &label)]);
            }
            Self::Mouse(on) => {
                app.mouse_enabled = on;
                app.status = t(
                    lang,
                    if on {
                        "settings.mouse_on"
                    } else {
                        "settings.mouse_off"
                    },
                )
                .into();
            }
            Self::SubtitleSplit(on) => {
                app.subtitle_sentence_split = on;
                app.status = tf(
                    lang,
                    "status.subtitle_split",
                    &[("value", on_off(lang, on))],
                );
            }
            Self::LlmTools(on) => {
                app.llm_allow_tools = on;
                app.status = tf(lang, "status.llm_tools", &[("value", on_off(lang, on))]);
            }
            Self::Language(language) => {
                app.lang = language;
                app.status = t(language, "settings.language_changed").into();
            }
            // `Theme` is parsed once here, never per frame.
            Self::Theme(name) => {
                if let Some(theme) = Theme::by_name(&name) {
                    app.status = tf(lang, "status.theme_set", &[("value", theme.name)]);
                    app.theme = theme;
                }
            }
        }
        save_app_settings(app);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::settings::isolated_config_dir;

    #[test]
    fn a_command_and_its_menu_build_the_same_change() {
        let app = crate::test_support::ready_app();
        for (command, argument, option) in [
            ("/backend", "ONNX", "onnx"),
            ("/onnx-provider", "cpu", "cpu"),
            (
                "/model",
                "multilingual_ctc",
                "multilingual_ctc · Multilingual CTC (220M)",
            ),
            ("/diarize", "on", "on"),
            ("/audio-mode", "denoise", "denoise"),
            ("/diarization-backend", "onnx", "onnx"),
            ("/speakers", "3", "3"),
        ] {
            assert_eq!(
                Setting::parse(command, argument, &app),
                Some(Ok(Setting::from_menu(command, option).unwrap())),
                "{command}"
            );
        }
        assert!(Setting::parse("/backend", "tpu", &app).unwrap().is_err());
        assert!(Setting::parse("/llm-prompt", "x", &app).is_none());
    }

    #[test]
    fn rows_commands_and_menus_agree_on_the_interface_settings() {
        let _config = isolated_config_dir();
        let app = crate::test_support::ready_app();
        assert_eq!(
            Setting::parse("/mouse", "off", &app),
            Some(Ok(Setting::Mouse(false)))
        );
        assert_eq!(
            Setting::parse("/lang", "en", &app),
            Some(Ok(Setting::Language(Lang::En)))
        );
        assert_eq!(
            Setting::from_menu("/lang", t(Lang::En, "lang.name")),
            Some(Setting::Language(Lang::En))
        );
        assert_eq!(Setting::parse("/theme", "", &app), None, "opens the menu");
        assert!(Setting::parse("/theme", "nope", &app).unwrap().is_err());
        assert!(Setting::parse("/llm-tools", "maybe", &app)
            .unwrap()
            .is_err());

        let mut by_row = crate::test_support::ready_app();
        crate::app::dispatch(
            &mut by_row,
            crate::action::Action::ToggleSetting("subtitle_split"),
        );
        let mut by_command = crate::test_support::ready_app();
        by_command.input.replace("/subtitle-split off".into());
        crate::commands::run_command(&mut by_command);
        assert!(!by_row.subtitle_sentence_split && !by_command.subtitle_sentence_split);
        assert_eq!(by_row.status, by_command.status);
    }

    #[test]
    fn sortformer_keeps_speakers_automatic_whatever_the_path() {
        let _config = isolated_config_dir();
        let mut app = crate::test_support::ready_app();
        app.num_speakers = Some(2);
        Setting::DiarizationBackend("sortformer".into()).apply(&mut app);
        assert_eq!(app.num_speakers, None);
        let parsed = Setting::parse("/speakers", "not-a-number", &app).unwrap();
        parsed.unwrap().apply(&mut app);
        Setting::from_menu("/speakers", "4")
            .unwrap()
            .apply(&mut app);
        assert_eq!(app.num_speakers, None);
    }
}
