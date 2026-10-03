//! Where the settings live and how they are read and written.
//!
//! `tui_settings.json` holds everything; when the desktop app is installed
//! (its `user_settings.json` exists) the shared keys live there and the API key
//! in its `.env`, and those win on load.

use std::{
    collections::HashSet,
    fs,
    io::Write,
    path::{Path, PathBuf},
};

use serde_json::{Map, Value};

use super::{desktop_sync, model::TuiSettings};

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

fn read_object(path: &Path) -> Option<Map<String, Value>> {
    fs::read_to_string(path)
        .ok()
        .and_then(|contents| serde_json::from_str::<Value>(&contents).ok())
        .and_then(|value| match value {
            Value::Object(map) => Some(map),
            _ => None,
        })
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

fn settings_object(settings: &TuiSettings) -> Result<Map<String, Value>, String> {
    match serde_json::to_value(settings) {
        Ok(Value::Object(map)) => Ok(map),
        Ok(_) => Err("Cannot encode settings: not an object".into()),
        Err(error) => Err(format!("Cannot encode settings: {error}")),
    }
}

/// What is on disk right now, field by field: only the values actually stored.
fn disk_state() -> Map<String, Value> {
    let mut state = settings_path()
        .and_then(|path| read_object(&path))
        .unwrap_or_default();
    if main_app_installed() {
        // The desktop app is installed: its file wins for the shared keys, its .env for
        // the key. The key is read even when the JSON is corrupt, so that the next save
        // never treats "could not read" as "the user cleared it".
        if let Some(desktop) = main_app_settings_path().and_then(|path| read_object(&path)) {
            desktop_sync::read(&desktop, &mut state);
        }
        if let Some(key) = env_file_path().and_then(|path| read_env_value(&path, "LLM_API_KEY")) {
            state.insert("llm_api_key".into(), Value::String(key));
        }
    }
    state
}

/// `base` with each stored value laid over it that still fits its field: one
/// malformed value falls back to its default instead of resetting every setting.
fn overlay(mut base: Map<String, Value>, stored: Map<String, Value>) -> Map<String, Value> {
    for (field, value) in stored {
        if !base.contains_key(&field) {
            continue; // a key of another version, not a field of this one
        }
        let previous = base.insert(field.clone(), value);
        if serde_json::from_value::<TuiSettings>(Value::Object(base.clone())).is_err() {
            base.insert(field, previous.expect("known field"));
        }
    }
    base
}

fn settings_from(object: Map<String, Value>) -> TuiSettings {
    serde_json::from_value(Value::Object(object)).unwrap_or_default()
}

pub(crate) fn load_settings() -> TuiSettings {
    let defaults = settings_object(&TuiSettings::default()).unwrap_or_default();
    settings_from(overlay(defaults, disk_state()))
}

/// Writes every field: the TUI's own file and, when the desktop app is installed,
/// all shared keys. For a full snapshot that is meant to win.
#[cfg(test)]
pub(crate) fn save_settings(settings: &TuiSettings) -> Result<(), String> {
    write(settings, &|_| true)
}

/// Saves the fields that differ from `baseline` (what this TUI last loaded or
/// wrote) and nothing else, on top of what is on disk now; returns the new
/// baseline. Every save used to rewrite all ~25 shared keys from the start-up
/// snapshot, reverting what the desktop app changed while the TUI was open, and
/// a value this build cannot use (dropped on load) was overwritten with the
/// default. `None` (no baseline) writes every field.
pub(crate) fn save_changes(
    current: &TuiSettings,
    baseline: Option<&Map<String, Value>>,
) -> Result<Map<String, Value>, String> {
    let current = settings_object(current)?;
    let changed: HashSet<String> = current
        .iter()
        .filter(|(field, value)| baseline.is_none_or(|base| base.get(*field) != Some(*value)))
        .map(|(field, _)| field.clone())
        .collect();
    let mut next = baseline.cloned().unwrap_or_else(|| current.clone());
    if changed.is_empty() {
        return Ok(next);
    }
    // Unchanged fields take the value on disk; changed ones the TUI's, which is
    // also the fallback when the files cannot be read.
    let mut merged = overlay(current.clone(), disk_state());
    for field in &changed {
        merged.insert(field.clone(), current[field].clone());
    }
    write(&settings_from(merged), &|field| changed.contains(field))?;
    for field in changed {
        next.insert(field.clone(), current[&field].clone());
    }
    Ok(next)
}

/// The TUI file gets all of `settings` (keeping keys a newer TUI wrote); the
/// desktop file only the shared keys `changed` accepts.
fn write(settings: &TuiSettings, changed: &dyn Fn(&str) -> bool) -> Result<(), String> {
    let path = settings_path().ok_or("Cannot determine the settings directory")?;
    let parent = path
        .parent()
        .ok_or("Cannot determine the settings directory")?;
    fs::create_dir_all(parent)
        .map_err(|error| format!("Cannot create settings directory: {error}"))?;
    let fields = settings_object(settings)?;
    let mut own = read_object(&path).unwrap_or_default();
    for (field, value) in &fields {
        own.insert(field.clone(), value.clone());
    }
    if main_app_installed() {
        // The main app is installed: shared keys live in its file, the key in its .env.
        let main_path =
            main_app_settings_path().ok_or("Cannot determine the settings directory")?;
        let contents = fs::read_to_string(&main_path)
            .map_err(|error| format!("Cannot read {}: {error}", main_path.display()))?;
        // Corrupt or empty: leave the user's file for inspection rather than replace
        // it with a bare object of TUI keys; the shared values still reach
        // tui_settings.json below.
        if let Some(mut desktop) = serde_json::from_str::<Value>(&contents)
            .ok()
            .and_then(|value| value.as_object().cloned())
        {
            desktop_sync::write(&fields, &mut desktop, changed);
            write_json_atomic(&main_path, &Value::Object(desktop))?;
        }
        // Like config.save_env_value, only a non-empty key is ever written: an empty one
        // means "not loaded", never "delete the desktop app's key". And only a key that
        // differs from the stored one: `TuiSettings::default()` seeds the key from the
        // shell's LLM_API_KEY, so an unconditional write would copy a shell secret into
        // the desktop app's .env on any unrelated save and rewrite the file when nothing
        // changed. A partial save also skips a key it did not change: with no key in
        // .env, the shell's LLM_API_KEY would otherwise be copied there.
        if changed("llm_api_key") && !settings.llm_api_key.is_empty() {
            if let Some(env_path) = env_file_path() {
                if read_env_value(&env_path, "LLM_API_KEY").as_deref()
                    != Some(settings.llm_api_key.as_str())
                {
                    write_env_value(&env_path, "LLM_API_KEY", &settings.llm_api_key)?;
                }
            }
        }
        own.remove("llm_api_key"); // never duplicate the secret into tui_settings.json
    }
    write_json_atomic(&path, &Value::Object(own))
}
