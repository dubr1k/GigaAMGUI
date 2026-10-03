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
