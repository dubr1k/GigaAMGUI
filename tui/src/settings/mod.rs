//! Persisted TUI settings: the data (`model`), where and how they are stored
//! (`store`), the keys shared with the desktop app (`desktop_sync`), and the
//! bridge to the interactive state below.

mod desktop_sync;
mod model;
mod store;

use std::path::Path;

use crate::{
    app::App,
    i18n::{t, tf, Lang},
    options::{backend_is_supported, is_model, AUDIO_MODES, DIARIZATION_BACKENDS, ONNX_PROVIDERS},
    theme::Theme,
};

pub(crate) use model::TuiSettings;
pub(crate) use store::load_settings;
#[cfg(test)]
pub(crate) use store::save_settings;

/// Copies the loaded settings into the app state, dropping values the current build
/// cannot honour (an unsupported backend, an unknown model) so that the UI never
/// offers a choice the worker would reject.
pub(crate) fn apply_settings(app: &mut App, settings: TuiSettings, lang_override: Option<Lang>) {
    app.mouse_enabled = settings.mouse;
    // A theme this build does not know (renamed, removed) must not break the start.
    app.theme = Theme::by_name(&settings.theme).unwrap_or_else(Theme::default_theme);
    let stored_language = Lang::parse(&settings.language).unwrap_or(Lang::Ru);
    app.lang = lang_override.unwrap_or(stored_language);
    app.pet.enabled = settings.pet_enabled;
    if backend_is_supported(&settings.backend) {
        app.backend = settings.backend;
    }
    if ONNX_PROVIDERS.contains(&settings.onnx_provider.as_str()) {
        app.onnx_provider = settings.onnx_provider;
    }
    if DIARIZATION_BACKENDS.contains(&settings.diarization_backend.as_str()) {
        app.diarization_backend = settings.diarization_backend;
    }
    if is_model(&settings.model) {
        app.model = settings.model;
    }
    if AUDIO_MODES.contains(&settings.audio_preprocessing_mode.as_str()) {
        app.audio_preprocessing_mode = settings.audio_preprocessing_mode;
    }
    app.llm_provider = settings.llm_provider;
    app.llm_api_url = settings.llm_api_url;
    app.llm_api_key = settings.llm_api_key;
    app.llm_model = settings.llm_model;
    app.llm_temperature = settings.llm_temperature;
    app.llm_internal_providers = settings.llm_internal_providers;
    app.llm_extra_args = settings.llm_extra_args;
    app.llm_tool_paths = settings.llm_tool_paths;
    app.llm_allow_tools = settings.llm_allow_tools;
    app.subtitle_sentence_split = settings.subtitle_sentence_split;
    app.subtitle_max_lines = settings.subtitle_max_lines.clamp(1, 4);
    app.subtitle_max_width = settings.subtitle_max_width.clamp(20, 100);
    if !settings.formats.is_empty() {
        app.formats = settings.formats;
    }
    app.diarization = settings.diarization;
    app.num_speakers = settings.num_speakers;
    // A folder removed since the last run falls back to "next to the file"
    // instead of failing every file of the first batch.
    app.output_dir = settings
        .output_dir
        .filter(|directory| Path::new(directory).is_dir());
    // `App::default()` greets in Russian before the language is known: redo the
    // greeting in the language that was just chosen.
    app.status = t(app.lang, "status.ready").into();
    app.logs = vec![t(app.lang, "log.ready").into()];
    // What this session starts from, after dropping what it cannot use: a save
    // writes only fields that differ from it. The language is the stored one, so
    // a `--lang` override is kept by the next save, as before.
    let mut baseline = TuiSettings::from(&*app);
    baseline.language = stored_language.code().to_owned();
    app.settings_baseline = match serde_json::to_value(baseline) {
        Ok(serde_json::Value::Object(map)) => Some(map),
        _ => None,
    };
}

impl From<&App> for TuiSettings {
    fn from(app: &App) -> Self {
        Self {
            pet_enabled: app.pet.enabled,
            language: app.lang.code().to_owned(),
            mouse: app.mouse_enabled,
            backend: app.backend.clone(),
            onnx_provider: app.onnx_provider.clone(),
            diarization_backend: app.diarization_backend.clone(),
            model: app.model.clone(),
            subtitle_sentence_split: app.subtitle_sentence_split,
            subtitle_max_lines: app.subtitle_max_lines,
            subtitle_max_width: app.subtitle_max_width,
            formats: app.formats.clone(),
            diarization: app.diarization,
            num_speakers: app.num_speakers,
            llm_provider: app.llm_provider.clone(),
            llm_api_url: app.llm_api_url.clone(),
            llm_api_key: app.llm_api_key.clone(),
            llm_model: app.llm_model.clone(),
            llm_temperature: app.llm_temperature,
            llm_internal_providers: app.llm_internal_providers.clone(),
            llm_extra_args: app.llm_extra_args.clone(),
            llm_tool_paths: app.llm_tool_paths.clone(),
            llm_allow_tools: app.llm_allow_tools,
            audio_preprocessing_mode: app.audio_preprocessing_mode.clone(),
            theme: app.theme.name.to_owned(),
            output_dir: app.output_dir.clone(),
        }
    }
}

/// Saves what this session changed (see `store::save_changes`) and remembers it
/// as the new baseline.
pub(crate) fn save_app_settings(app: &mut App) {
    match store::save_changes(&TuiSettings::from(&*app), app.settings_baseline.as_ref()) {
        Ok(baseline) => app.settings_baseline = Some(baseline),
        Err(error) => {
            app.status = tf(app.lang, "err.settings_save", &[("error", &error)]);
            app.log(app.status.clone());
        }
    }
}

#[cfg(test)]
use std::{
    fs,
    path::PathBuf,
    sync::{Mutex, MutexGuard, PoisonError},
};

/// Guards a per-test settings directory: `GIGAAM_CONFIG_DIR` is process-global, so
/// every test that reads or writes settings must hold this until it is done.
#[cfg(test)]
pub(crate) struct IsolatedConfigDir {
    path: PathBuf,
    _guard: MutexGuard<'static, ()>,
}

#[cfg(test)]
impl std::ops::Deref for IsolatedConfigDir {
    type Target = Path;

    fn deref(&self) -> &Path {
        &self.path
    }
}

#[cfg(test)]
static CONFIG_DIR_LOCK: Mutex<()> = Mutex::new(());

/// Tests must never touch the developer's real settings directory.
#[cfg(test)]
pub(crate) fn isolated_config_dir() -> IsolatedConfigDir {
    let guard = CONFIG_DIR_LOCK
        .lock()
        .unwrap_or_else(PoisonError::into_inner);
    let directory = std::env::temp_dir().join(format!(
        "gigaam-tui-config-{}-{}",
        std::process::id(),
        std::thread::current()
            .name()
            .unwrap_or("test")
            .replace("::", "-")
    ));
    let _ = fs::remove_dir_all(&directory);
    fs::create_dir_all(&directory).unwrap();
    std::env::set_var("GIGAAM_CONFIG_DIR", &directory);
    IsolatedConfigDir {
        path: directory,
        _guard: guard,
    }
}

#[cfg(test)]
mod tests;
