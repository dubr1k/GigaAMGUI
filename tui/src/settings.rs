//! Persisted TUI settings and their sync with the desktop app's config directory.

use std::{
    collections::HashMap,
    fs,
    io::Write,
    path::{Path, PathBuf},
};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::{
    app::App,
    commands::{backend_is_supported, FORMAT_KEYS, MODEL_OPTIONS},
    i18n::{t, tf, Lang},
    theme::{Theme, DEFAULT_THEME},
};

#[derive(Deserialize, Serialize)]
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
        }
    }
}

/// The `GigaAMTranscriber` config directory shared with the desktop app.
pub(crate) fn config_dir() -> Option<PathBuf> {
    if let Some(directory) = std::env::var_os("GIGAAM_CONFIG_DIR") {
        return Some(PathBuf::from(directory));
    }
    #[cfg(target_os = "macos")]
    {
        std::env::var_os("HOME").map(|home| {
            PathBuf::from(home)
                .join("Library")
                .join("Application Support")
                .join("GigaAMTranscriber")
        })
    }
    #[cfg(target_os = "windows")]
    {
        std::env::var_os("APPDATA")
            .or_else(|| std::env::var_os("USERPROFILE"))
            .map(|directory| PathBuf::from(directory).join("GigaAMTranscriber"))
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        std::env::var_os("XDG_CONFIG_HOME")
            .map(PathBuf::from)
            .or_else(|| std::env::var_os("HOME").map(|home| PathBuf::from(home).join(".config")))
            .map(|directory| directory.join("GigaAMTranscriber"))
    }
}

pub(crate) fn settings_path() -> Option<PathBuf> {
    config_dir().map(|directory| directory.join("tui_settings.json"))
}

/// `user_settings.json` of the desktop app; its presence means the app is installed.
pub(crate) fn main_app_settings_path() -> Option<PathBuf> {
    config_dir().map(|directory| directory.join("user_settings.json"))
}

/// The desktop app keeps `LLM_API_KEY` here, not in `user_settings.json`.
pub(crate) fn env_file_path() -> Option<PathBuf> {
    config_dir().map(|directory| directory.join(".env"))
}

/// The single "desktop app is installed" predicate shared by load and save: the
/// file exists, whether or not it currently parses.
fn main_app_installed() -> bool {
    main_app_settings_path().is_some_and(|path| path.is_file())
}

/// (settings-key prefix, bare binary name); a path equal to the bare name is not an override.
const CLI_PREFIXES: [(&str, &str); 5] = [
    ("claude", "claude"),
    ("codex", "codex"),
    ("opencode", "opencode"),
    ("pi", "pi"),
    ("omp", "omp"),
];

/// Copies the keys shared with the desktop app from its `user_settings.json` map.
fn shared_settings_from_main_app(map: &serde_json::Map<String, Value>, settings: &mut TuiSettings) {
    let text = |key: &str| map.get(key).and_then(Value::as_str).map(str::to_owned);
    if let Some(v) = text("language").filter(|v| v == "ru" || v == "en") {
        settings.language = v;
    }
    if let Some(v) = text("asr_backend") {
        settings.backend = v;
    }
    if let Some(v) = text("onnx_provider") {
        settings.onnx_provider = v;
    }
    if let Some(v) = text("diarization_backend") {
        settings.diarization_backend = v;
    }
    if let Some(v) = text("asr_model") {
        settings.model = v;
    }
    if let Some(v) = text("audio_preprocessing_mode") {
        settings.audio_preprocessing_mode = v;
    }
    if let Some(v) = map.get("subtitle_sentence_split").and_then(Value::as_bool) {
        settings.subtitle_sentence_split = v;
    }
    if let Some(v) = map.get("subtitle_max_line_count").and_then(Value::as_u64) {
        settings.subtitle_max_lines = v.clamp(1, 4) as u8;
    }
    if let Some(v) = map.get("subtitle_max_line_width").and_then(Value::as_u64) {
        settings.subtitle_max_width = v.clamp(20, 100) as u16;
    }
    if let Some(formats) = map.get("output_formats").and_then(Value::as_object) {
        let selected: Vec<String> = FORMAT_KEYS
            .iter()
            .filter(|key| formats.get(**key).and_then(Value::as_bool).unwrap_or(false))
            .map(|key| (*key).to_owned())
            .collect();
        if !selected.is_empty() {
            settings.formats = selected;
        }
    }
    if let Some(v) = map.get("enable_diarization").and_then(Value::as_bool) {
        settings.diarization = v;
    }
    if let Some(v) = map.get("num_speakers").and_then(Value::as_u64) {
        settings.num_speakers = (v > 0).then_some(v as u32);
    }
    if let Some(v) = text("llm_provider") {
        settings.llm_provider = v;
    }
    if let Some(v) = text("llm_api_url") {
        settings.llm_api_url = v;
    }
    if let Some(v) = text("llm_model") {
        settings.llm_model = v;
    }
    if let Some(v) = map.get("llm_temperature") {
        // PyQt stores the temperature as a string ("0.2"); accept a number too.
        if let Some(t) = v
            .as_f64()
            .or_else(|| v.as_str().and_then(|s| s.trim().parse().ok()))
        {
            settings.llm_temperature = t;
        }
    }
    if let Some(v) = map.get("llm_allow_tools").and_then(Value::as_bool) {
        settings.llm_allow_tools = v;
    }
    for (prefix, binary) in CLI_PREFIXES {
        match text(&format!("llm_{prefix}_path")).map(|p| p.trim().to_owned()) {
            Some(p) if !p.is_empty() && p != binary => {
                settings.llm_tool_paths.insert(prefix.into(), p);
            }
            Some(_) => {
                settings.llm_tool_paths.remove(prefix);
            }
            None => {}
        }
        match text(&format!("llm_{prefix}_args")) {
            Some(a) if !a.trim().is_empty() => {
                settings.llm_extra_args.insert(prefix.into(), a);
            }
            Some(_) => {
                settings.llm_extra_args.remove(prefix);
            }
            None => {}
        }
        if matches!(prefix, "pi" | "omp") {
            match text(&format!("llm_{prefix}_provider")) {
                Some(p) if !p.trim().is_empty() => {
                    settings.llm_internal_providers.insert(prefix.into(), p);
                }
                Some(_) => {
                    settings.llm_internal_providers.remove(prefix);
                }
                None => {}
            }
        }
    }
    match text("llm_other_path") {
        Some(p) if !p.trim().is_empty() => {
            settings.llm_tool_paths.insert("other".into(), p);
        }
        Some(_) => {
            settings.llm_tool_paths.remove("other");
        }
        None => {}
    }
    match text("llm_other_args") {
        Some(a) if !a.trim().is_empty() => {
            settings.llm_extra_args.insert("other".into(), a);
        }
        Some(_) => {
            settings.llm_extra_args.remove("other");
        }
        None => {}
    }
}

/// Writes the shared keys into the desktop app's map, leaving its other keys and order alone.
fn shared_settings_into_main_app(settings: &TuiSettings, map: &mut serde_json::Map<String, Value>) {
    let mut put = |key: &str, value: Value| {
        map.insert(key.to_owned(), value);
    };
    put("language", json!(settings.language));
    put("asr_backend", json!(settings.backend));
    put("onnx_provider", json!(settings.onnx_provider));
    put("diarization_backend", json!(settings.diarization_backend));
    put("asr_model", json!(settings.model));
    put(
        "audio_preprocessing_mode",
        json!(settings.audio_preprocessing_mode),
    );
    put(
        "subtitle_sentence_split",
        json!(settings.subtitle_sentence_split),
    );
    put(
        "subtitle_max_line_count",
        json!(settings.subtitle_max_lines),
    );
    put(
        "subtitle_max_line_width",
        json!(settings.subtitle_max_width),
    );
    let mut formats = serde_json::Map::new();
    for key in FORMAT_KEYS {
        formats.insert(
            key.to_owned(),
            json!(settings.formats.iter().any(|selected| selected == key)),
        );
    }
    put("output_formats", Value::Object(formats));
    put("enable_diarization", json!(settings.diarization));
    put("num_speakers", json!(settings.num_speakers.unwrap_or(0)));
    put("llm_provider", json!(settings.llm_provider));
    put("llm_api_url", json!(settings.llm_api_url));
    put("llm_model", json!(settings.llm_model));
    // Stored as a string, exactly like the PyQt settings dialog does.
    put(
        "llm_temperature",
        Value::String(format!("{}", settings.llm_temperature)),
    );
    put("llm_allow_tools", json!(settings.llm_allow_tools));
    let lookup =
        |table: &HashMap<String, String>, key: &str| table.get(key).cloned().unwrap_or_default();
    // Empty strings mean "cleared" so that a reset in the TUI reaches the desktop app.
    for (prefix, _) in CLI_PREFIXES {
        put(
            &format!("llm_{prefix}_path"),
            json!(lookup(&settings.llm_tool_paths, prefix)),
        );
        put(
            &format!("llm_{prefix}_args"),
            json!(lookup(&settings.llm_extra_args, prefix)),
        );
        if matches!(prefix, "pi" | "omp") {
            put(
                &format!("llm_{prefix}_provider"),
                json!(lookup(&settings.llm_internal_providers, prefix)),
            );
        }
    }
    put(
        "llm_other_path",
        json!(lookup(&settings.llm_tool_paths, "other")),
    );
    put(
        "llm_other_args",
        json!(lookup(&settings.llm_extra_args, "other")),
    );
}

/// Reads `KEY=value` from a dotenv-style file; quotes and surrounding spaces are stripped.
fn read_env_value(path: &Path, key: &str) -> Option<String> {
    let contents = fs::read_to_string(path).ok()?;
    contents.lines().find_map(|line| {
        let line = line.trim();
        let (name, value) = line.split_once('=')?;
        (name.trim() == key).then(|| value.trim().trim_matches(['\'', '"']).to_owned())
    })
}

/// Replaces (or appends) `KEY=value` in a dotenv-style file; an empty value removes the line.
fn write_env_value(path: &Path, key: &str, value: &str) -> Result<(), String> {
    let contents = fs::read_to_string(path).unwrap_or_default();
    let mut lines: Vec<String> = contents.lines().map(str::to_owned).collect();
    let is_key = |line: &str| {
        line.trim()
            .split_once('=')
            .is_some_and(|(name, _)| name.trim() == key)
    };
    let value = value.trim();
    match lines.iter().position(|line| is_key(line)) {
        Some(index) if value.is_empty() => {
            lines.remove(index);
        }
        Some(index) => lines[index] = format!("{key}={value}"),
        None if value.is_empty() => return Ok(()), // nothing to clear, nothing to create
        None => lines.push(format!("{key}={value}")),
    }
    let mut text = lines.join("\n");
    if !text.is_empty() {
        text.push('\n');
    }
    write_text_atomic(path, &text)
}

/// Same temp-file-then-rename scheme as `save_json_atomic` in the Python side.
fn write_json_atomic(path: &Path, value: &Value) -> Result<(), String> {
    let contents = serde_json::to_string_pretty(value)
        .map_err(|error| format!("Cannot encode settings: {error}"))?;
    write_text_atomic(path, &format!("{contents}\n"))
}

/// Per-process temp name so two TUI instances never rename over each other's file;
/// `sync_all` before the rename so a crash cannot leave a truncated settings file.
fn write_text_atomic(path: &Path, contents: &str) -> Result<(), String> {
    let name = path
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or_else(|| format!("Cannot save {}: no file name", path.display()))?;
    let temp = path.with_file_name(format!("{name}.{}.tmp", std::process::id()));
    let written = fs::File::create(&temp)
        .and_then(|mut file| {
            file.write_all(contents.as_bytes())?;
            file.sync_all()
        })
        .map_err(|error| format!("Cannot write {}: {error}", temp.display()))
        .and_then(|_| {
            fs::rename(&temp, path)
                .map_err(|error| format!("Cannot save {}: {error}", path.display()))
        });
    if written.is_err() {
        let _ = fs::remove_file(&temp);
    }
    written
}

/// Copies the loaded settings into the app state, dropping values the current build
/// cannot honour (an unsupported backend, an unknown model) so that the UI never
/// offers a choice the worker would reject.
pub(crate) fn apply_settings(app: &mut App, settings: TuiSettings, lang_override: Option<Lang>) {
    app.mouse_enabled = settings.mouse;
    // A theme this build does not know (renamed, removed) must not break the start.
    app.theme = Theme::by_name(&settings.theme).unwrap_or_else(Theme::default_theme);
    app.lang = lang_override
        .or_else(|| Lang::parse(&settings.language))
        .unwrap_or(Lang::Ru);
    app.pet_enabled = settings.pet_enabled;
    if backend_is_supported(&settings.backend) {
        app.backend = settings.backend;
    }
    if matches!(
        settings.onnx_provider.as_str(),
        "auto" | "cpu" | "cuda" | "tensorrt" | "coreml" | "directml"
    ) {
        app.onnx_provider = settings.onnx_provider;
    }
    if matches!(
        settings.diarization_backend.as_str(),
        "pyannote" | "onnx" | "sortformer"
    ) {
        app.diarization_backend = settings.diarization_backend;
    }
    if MODEL_OPTIONS.iter().any(|(id, _)| *id == settings.model) {
        app.model = settings.model;
    }
    if matches!(
        settings.audio_preprocessing_mode.as_str(),
        "auto" | "off" | "light" | "denoise"
    ) {
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
    // `App::default()` greets in Russian before the language is known: redo the
    // greeting in the language that was just chosen.
    app.status = t(app.lang, "status.ready").into();
    app.logs = vec![t(app.lang, "log.ready").into()];
}

pub(crate) fn load_settings() -> TuiSettings {
    let mut settings: TuiSettings = settings_path()
        .and_then(|path| fs::read_to_string(path).ok())
        .and_then(|contents| serde_json::from_str(&contents).ok())
        .unwrap_or_default();
    if main_app_installed() {
        // The desktop app is installed: its file wins for the shared keys, its .env for
        // the key. The key is read even when the JSON is corrupt, so that the next save
        // never treats "could not read" as "the user cleared it".
        if let Some(map) = main_app_settings_path()
            .and_then(|path| fs::read_to_string(path).ok())
            .and_then(|contents| serde_json::from_str::<Value>(&contents).ok())
            .and_then(|value| value.as_object().cloned())
        {
            shared_settings_from_main_app(&map, &mut settings);
        }
        if let Some(key) = env_file_path().and_then(|path| read_env_value(&path, "LLM_API_KEY")) {
            settings.llm_api_key = key;
        }
    }
    settings
}

pub(crate) fn save_settings(settings: &TuiSettings) -> Result<(), String> {
    let path = settings_path().ok_or("Cannot determine the settings directory")?;
    let parent = path
        .parent()
        .ok_or("Cannot determine the settings directory")?;
    fs::create_dir_all(parent)
        .map_err(|error| format!("Cannot create settings directory: {error}"))?;
    let mut own = serde_json::to_value(settings)
        .map_err(|error| format!("Cannot encode settings: {error}"))?;
    if main_app_installed() {
        // The main app is installed: shared keys live in its file, the key in its .env.
        let main_path =
            main_app_settings_path().ok_or("Cannot determine the settings directory")?;
        let contents = fs::read_to_string(&main_path)
            .map_err(|error| format!("Cannot read {}: {error}", main_path.display()))?;
        match serde_json::from_str::<Value>(&contents)
            .ok()
            .and_then(|v| v.as_object().cloned())
        {
            Some(mut map) => {
                shared_settings_into_main_app(settings, &mut map);
                write_json_atomic(&main_path, &Value::Object(map))?;
            }
            // Corrupt or empty: leave the user's file for inspection rather than replace
            // it with a bare object of TUI keys; the shared values still reach
            // tui_settings.json below.
            None => {}
        }
        // Like config.save_env_value, only a non-empty key is ever written: an empty one
        // means "not loaded", never "delete the desktop app's key". And only a key that
        // differs from the stored one: `TuiSettings::default()` seeds the key from the
        // shell's LLM_API_KEY, so an unconditional write would copy a shell secret into
        // the desktop app's .env on any unrelated save and rewrite the file when nothing
        // changed.
        if !settings.llm_api_key.is_empty() {
            if let Some(env_path) = env_file_path() {
                if read_env_value(&env_path, "LLM_API_KEY").as_deref()
                    != Some(settings.llm_api_key.as_str())
                {
                    write_env_value(&env_path, "LLM_API_KEY", &settings.llm_api_key)?;
                }
            }
        }
        if let Some(object) = own.as_object_mut() {
            object.remove("llm_api_key"); // never duplicate the secret into tui_settings.json
        }
    }
    write_json_atomic(&path, &own)
}

impl From<&App> for TuiSettings {
    fn from(app: &App) -> Self {
        Self {
            pet_enabled: app.pet_enabled,
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
        }
    }
}

pub(crate) fn save_app_settings(app: &mut App) {
    if let Err(error) = save_settings(&TuiSettings::from(&*app)) {
        app.status = tf(app.lang, "err.settings_save", &[("error", &error)]);
        app.log(app.status.clone());
    }
}

#[cfg(test)]
use std::sync::{Mutex, MutexGuard, PoisonError};

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
mod tests {
    use super::*;

    #[test]
    fn settings_preserve_the_selected_backend_and_model() {
        let settings = TuiSettings {
            pet_enabled: true,
            backend: "mlx".into(),
            diarization_backend: "sortformer".into(),
            model: "multilingual_ctc".into(),
            subtitle_sentence_split: false,
            subtitle_max_lines: 3,
            subtitle_max_width: 72,
            ..TuiSettings::default()
        };
        let restored: TuiSettings =
            serde_json::from_str(&serde_json::to_string(&settings).expect("settings serialize"))
                .expect("settings deserialize");

        assert!(restored.pet_enabled);
        assert_eq!(restored.backend, "mlx");
        assert_eq!(restored.diarization_backend, "sortformer");
        assert_eq!(restored.model, "multilingual_ctc");
        assert!(!restored.subtitle_sentence_split);
        assert_eq!(restored.subtitle_max_lines, 3);
        assert_eq!(restored.subtitle_max_width, 72);
    }

    #[test]
    fn provider_extras_survive_settings_roundtrip() {
        let mut settings = TuiSettings::default();
        settings
            .llm_internal_providers
            .insert("pi".into(), "google".into());
        settings
            .llm_extra_args
            .insert("claude".into(), "--verbose".into());
        settings
            .llm_tool_paths
            .insert("other".into(), "/x/llm".into());
        settings.llm_allow_tools = true;
        let restored: TuiSettings =
            serde_json::from_str(&serde_json::to_string(&settings).unwrap()).unwrap();
        assert_eq!(restored.llm_internal_providers["pi"], "google");
        assert_eq!(restored.llm_extra_args["claude"], "--verbose");
        assert_eq!(restored.llm_tool_paths["other"], "/x/llm");
        assert!(restored.llm_allow_tools);
    }

    #[test]
    fn settings_come_from_the_main_app_when_it_is_installed() {
        let directory = isolated_config_dir();
        fs::write(
            directory.join("user_settings.json"),
            r#"{"asr_backend":"mlx","asr_model":"multilingual_ctc","output_formats":{"txt":true,"srt":true,"md":false},
                "enable_diarization":true,"num_speakers":3,"llm_provider":"oh-my-pi","llm_temperature":"0.7",
                "llm_omp_provider":"anthropic","llm_omp_args":"--thinking low","llm_claude_path":"claude",
                "llm_opencode_path":"/opt/homebrew/bin/opencode","llm_allow_tools":true,
                "subtitle_max_line_count":3,"window_geometry":"keep-me"}"#,
        )
        .unwrap();
        fs::write(
            directory.join(".env"),
            "HF_TOKEN=hf_x\nLLM_API_KEY=sk-main\n",
        )
        .unwrap();
        fs::write(
            directory.join("tui_settings.json"),
            r#"{"pet_enabled":true,"backend":"onnx"}"#,
        )
        .unwrap();

        let settings = load_settings();

        assert!(settings.pet_enabled);
        assert_eq!(
            settings.backend, "mlx",
            "main app wins over tui_settings.json for shared keys"
        );
        assert_eq!(settings.model, "multilingual_ctc");
        assert_eq!(settings.formats, vec!["txt", "srt"]);
        assert!(settings.diarization);
        assert_eq!(settings.num_speakers, Some(3));
        assert_eq!(settings.llm_provider, "oh-my-pi");
        assert_eq!(settings.llm_temperature, 0.7);
        assert_eq!(settings.llm_internal_providers["omp"], "anthropic");
        assert_eq!(settings.llm_extra_args["omp"], "--thinking low");
        assert!(
            !settings.llm_tool_paths.contains_key("claude"),
            "bare binary name is not an override"
        );
        assert_eq!(
            settings.llm_tool_paths["opencode"],
            "/opt/homebrew/bin/opencode"
        );
        assert!(settings.llm_allow_tools);
        assert_eq!(settings.subtitle_max_lines, 3);
        assert_eq!(settings.llm_api_key, "sk-main");
    }

    #[test]
    fn saving_writes_shared_keys_back_to_the_main_app_and_keeps_its_other_keys() {
        let directory = isolated_config_dir();
        fs::write(
            directory.join("user_settings.json"),
            "{\n  \"window_geometry\": \"keep-me\",\n  \"asr_backend\": \"auto\",\n  \"theme\": \"dark\"\n}\n",
        )
        .unwrap();
        fs::write(directory.join(".env"), "HF_TOKEN=hf_x\n").unwrap();
        let settings = TuiSettings {
            pet_enabled: true,
            backend: "mlx".into(),
            formats: vec!["txt".into(), "vtt".into()],
            num_speakers: Some(2),
            llm_temperature: 0.3,
            llm_api_key: "sk-new".into(),
            ..TuiSettings::default()
        };

        save_settings(&settings).unwrap();

        let main: Value = serde_json::from_str(
            &fs::read_to_string(directory.join("user_settings.json")).unwrap(),
        )
        .unwrap();
        assert_eq!(main["window_geometry"], "keep-me");
        assert_eq!(main["theme"], "dark");
        assert_eq!(main["asr_backend"], "mlx");
        assert_eq!(main["output_formats"]["vtt"], true);
        assert_eq!(main["output_formats"]["srt"], false);
        assert_eq!(main["num_speakers"], 2);
        assert_eq!(main["llm_temperature"], "0.3");
        assert!(
            main.get("pet_enabled").is_none(),
            "TUI-only keys stay out of the main app file"
        );
        let keys: Vec<&String> = main.as_object().unwrap().keys().collect();
        assert_eq!(
            keys[0], "window_geometry",
            "existing key order is preserved"
        );
        let tui: Value =
            serde_json::from_str(&fs::read_to_string(directory.join("tui_settings.json")).unwrap())
                .unwrap();
        assert_eq!(tui["pet_enabled"], true);
        let env = fs::read_to_string(directory.join(".env")).unwrap();
        assert!(env.contains("HF_TOKEN=hf_x\n"));
        assert!(env.contains("LLM_API_KEY=sk-new\n"));
    }

    #[test]
    fn theme_defaults_when_missing_and_round_trips() {
        let directory = isolated_config_dir();
        let old: TuiSettings = serde_json::from_str(r#"{"mouse": false}"#).unwrap();
        assert_eq!(old.theme, "default");
        assert!(!old.mouse);

        let mut app = crate::test_support::ready_app();
        apply_settings(&mut app, old, None);
        assert_eq!(app.theme.name, "default");

        let mut settings = TuiSettings::default();
        settings.theme = "dark-gruvbox".into();
        save_settings(&settings).unwrap();
        let text = fs::read_to_string(directory.join("tui_settings.json")).unwrap();
        assert!(text.contains("\"theme\": \"dark-gruvbox\""), "{text}");
        assert_eq!(load_settings().theme, "dark-gruvbox");
        apply_settings(&mut app, load_settings(), None);
        assert_eq!(app.theme.name, "dark-gruvbox");
        assert_eq!(TuiSettings::from(&app).theme, "dark-gruvbox");

        // A name this build does not know falls back to the default look.
        let mut settings = TuiSettings::default();
        settings.theme = "removed-theme".into();
        apply_settings(&mut app, settings, None);
        assert_eq!(app.theme.name, "default");
    }

    #[test]
    fn language_round_trips_and_syncs_with_the_desktop_app() {
        let directory = isolated_config_dir();
        fs::write(directory.join("user_settings.json"), r#"{"language":"en"}"#).unwrap();
        let settings = load_settings();
        assert_eq!(settings.language, "en");
        let mut updated = settings;
        updated.language = "ru".into();
        save_settings(&updated).unwrap();
        let main: Value = serde_json::from_str(
            &fs::read_to_string(directory.join("user_settings.json")).unwrap(),
        )
        .unwrap();
        assert_eq!(main["language"], "ru");
    }

    #[test]
    fn without_the_main_app_everything_lives_in_tui_settings() {
        let directory = isolated_config_dir();
        let settings = TuiSettings {
            backend: "onnx".into(),
            llm_api_key: "sk-only".into(),
            ..TuiSettings::default()
        };

        save_settings(&settings).unwrap();

        assert!(!directory.join("user_settings.json").exists());
        assert!(
            !directory.join(".env").exists(),
            "no main app → no .env is created"
        );
        let restored = load_settings();
        assert_eq!(restored.backend, "onnx");
        assert_eq!(restored.llm_api_key, "sk-only");
    }

    #[test]
    fn unchanged_api_key_leaves_the_desktop_env_file_untouched() {
        let directory = isolated_config_dir();
        fs::write(
            directory.join("user_settings.json"),
            "{\"theme\": \"dark\"}\n",
        )
        .unwrap();
        let original = "HF_TOKEN=hf_x\nLLM_API_KEY=sk-same\n";
        fs::write(directory.join(".env"), original).unwrap();
        let before = fs::metadata(directory.join(".env"))
            .unwrap()
            .modified()
            .unwrap();

        let settings = TuiSettings {
            llm_api_key: "sk-same".into(),
            ..TuiSettings::default()
        };
        save_settings(&settings).unwrap();

        assert_eq!(
            fs::read(directory.join(".env")).unwrap(),
            original.as_bytes()
        );
        assert_eq!(
            fs::metadata(directory.join(".env"))
                .unwrap()
                .modified()
                .unwrap(),
            before,
            "an unchanged key must not rewrite .env"
        );
        let leftovers: Vec<_> = fs::read_dir(&*directory)
            .unwrap()
            .filter_map(Result::ok)
            .filter(|entry| entry.path().extension().is_some_and(|ext| ext == "tmp"))
            .collect();
        assert!(
            leftovers.is_empty(),
            "no temp file may be left behind: {leftovers:?}"
        );
    }

    #[test]
    fn corrupt_main_settings_file_never_wipes_the_desktop_api_key() {
        let directory = isolated_config_dir();
        fs::write(directory.join("user_settings.json"), "{not json").unwrap();
        fs::write(
            directory.join(".env"),
            "HF_TOKEN=hf_x\nLLM_API_KEY=sk-keep\n",
        )
        .unwrap();

        let loaded = load_settings();
        assert_eq!(loaded.llm_api_key, "sk-keep");

        save_settings(&TuiSettings::default()).unwrap();

        let env = fs::read_to_string(directory.join(".env")).unwrap();
        assert!(env.contains("LLM_API_KEY=sk-keep\n"));
        assert!(env.contains("HF_TOKEN=hf_x\n"));
        assert_eq!(
            fs::read_to_string(directory.join("user_settings.json")).unwrap(),
            "{not json",
            "a corrupt desktop file is left alone, not replaced by an empty object"
        );
        let tui: Value =
            serde_json::from_str(&fs::read_to_string(directory.join("tui_settings.json")).unwrap())
                .unwrap();
        assert!(tui.get("llm_api_key").is_none());
    }
}
