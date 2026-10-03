//! The keys shared with the desktop app's `user_settings.json`, as one table.
//!
//! The mapping used to be written twice by hand, once per direction, and the
//! save direction rewrote every shared key from the TUI's start-up snapshot. Both
//! directions now read the same table, and a save writes only the keys whose
//! TUI field changed (see `store::save_changes`).
//!
//! Both directions work on the TUI settings as a JSON object (field → value), so
//! the store can merge per field.

use serde_json::{json, Map, Value};

use crate::{options::FORMAT_KEYS, providers::cli_providers};

#[derive(Clone, Copy)]
enum Kind {
    Text,
    /// `ru` or `en` only.
    Language,
    Bool,
    /// An integer clamped to the range the TUI accepts.
    Clamped(u64, u64),
    /// `output_formats`: an object of format → bool on the desktop side.
    Formats,
    /// `num_speakers`: 0 on the desktop side means automatic (`None`).
    Speakers,
    /// The PyQt dialog stores the temperature as a string ("0.2").
    Temperature,
}

/// (TUI field, desktop key, kind), in the order new keys are appended.
const SHARED: [(&str, &str, Kind); 17] = [
    ("language", "language", Kind::Language),
    ("backend", "asr_backend", Kind::Text),
    ("onnx_provider", "onnx_provider", Kind::Text),
    ("diarization_backend", "diarization_backend", Kind::Text),
    ("model", "asr_model", Kind::Text),
    (
        "audio_preprocessing_mode",
        "audio_preprocessing_mode",
        Kind::Text,
    ),
    (
        "subtitle_sentence_split",
        "subtitle_sentence_split",
        Kind::Bool,
    ),
    (
        "subtitle_max_lines",
        "subtitle_max_line_count",
        Kind::Clamped(1, 4),
    ),
    (
        "subtitle_max_width",
        "subtitle_max_line_width",
        Kind::Clamped(20, 100),
    ),
    ("formats", "output_formats", Kind::Formats),
    ("diarization", "enable_diarization", Kind::Bool),
    ("num_speakers", "num_speakers", Kind::Speakers),
    ("llm_provider", "llm_provider", Kind::Text),
    ("llm_api_url", "llm_api_url", Kind::Text),
    ("llm_model", "llm_model", Kind::Text),
    ("llm_temperature", "llm_temperature", Kind::Temperature),
    ("llm_allow_tools", "llm_allow_tools", Kind::Bool),
];

/// Per-provider CLI keys `llm_<prefix>_<suffix>` and the TUI map they live in.
const PATHS: &str = "llm_tool_paths";
const ARGS: &str = "llm_extra_args";
const PROVIDERS: &str = "llm_internal_providers";

/// The desktop value as the TUI field's JSON value; `None` leaves the field alone.
fn decode(kind: Kind, value: &Value) -> Option<Value> {
    match kind {
        Kind::Text => value.as_str().map(|text| json!(text)),
        Kind::Language => value
            .as_str()
            .filter(|text| matches!(*text, "ru" | "en"))
            .map(|text| json!(text)),
        Kind::Bool => value.as_bool().map(Value::Bool),
        Kind::Clamped(low, high) => value.as_u64().map(|number| json!(number.clamp(low, high))),
        Kind::Formats => {
            let formats = value.as_object()?;
            let selected: Vec<&str> = FORMAT_KEYS
                .iter()
                .copied()
                .filter(|key| formats.get(*key).and_then(Value::as_bool).unwrap_or(false))
                .collect();
            (!selected.is_empty()).then(|| json!(selected))
        }
        Kind::Speakers => value
            .as_u64()
            .map(|count| if count > 0 { json!(count) } else { Value::Null }),
        Kind::Temperature => value
            .as_f64()
            .or_else(|| value.as_str().and_then(|text| text.trim().parse().ok()))
            .and_then(serde_json::Number::from_f64)
            .map(Value::Number),
    }
}

/// The TUI field's JSON value as the desktop app stores it.
fn encode(kind: Kind, value: &Value) -> Value {
    match kind {
        Kind::Text | Kind::Language | Kind::Bool | Kind::Clamped(..) => value.clone(),
        Kind::Formats => Value::Object(
            FORMAT_KEYS
                .iter()
                .map(|key| {
                    let selected = value
                        .as_array()
                        .is_some_and(|items| items.iter().any(|item| item == key));
                    ((*key).to_owned(), Value::Bool(selected))
                })
                .collect(),
        ),
        Kind::Speakers => json!(value.as_u64().unwrap_or(0)),
        Kind::Temperature => Value::String(format!("{}", value.as_f64().unwrap_or_default())),
    }
}

/// Inserts into (or, for `None`, removes from) one of the per-provider maps.
fn set_entry(state: &mut Map<String, Value>, field: &str, prefix: &str, value: Option<String>) {
    let map = state
        .entry(field)
        .or_insert_with(|| Value::Object(Map::new()));
    if !map.is_object() {
        *map = Value::Object(Map::new());
    }
    let map = map.as_object_mut().expect("object");
    match value {
        Some(value) => {
            map.insert(prefix.to_owned(), Value::String(value));
        }
        None => {
            map.remove(prefix);
        }
    }
}

/// Copies the desktop app's values over `state` (TUI field → JSON value). A key
/// the desktop file lacks leaves the TUI value alone; an empty one clears it.
pub(super) fn read(desktop: &Map<String, Value>, state: &mut Map<String, Value>) {
    for (field, key, kind) in SHARED {
        if let Some(value) = desktop.get(key).and_then(|value| decode(kind, value)) {
            state.insert(field.to_owned(), value);
        }
    }
    let text = |key: String| desktop.get(&key).and_then(Value::as_str).map(str::to_owned);
    // A path equal to the bare binary name is not an override.
    for provider in cli_providers() {
        let prefix = provider.prefix;
        let binary = provider.binary.unwrap_or_default();
        if let Some(path) = text(format!("llm_{prefix}_path")).map(|path| path.trim().to_owned()) {
            let keep = !path.is_empty() && path != binary;
            set_entry(state, PATHS, prefix, keep.then_some(path));
        }
        if let Some(args) = text(format!("llm_{prefix}_args")) {
            set_entry(
                state,
                ARGS,
                prefix,
                (!args.trim().is_empty()).then_some(args),
            );
        }
        if provider.internal_provider {
            if let Some(name) = text(format!("llm_{prefix}_provider")) {
                set_entry(
                    state,
                    PROVIDERS,
                    prefix,
                    (!name.trim().is_empty()).then_some(name),
                );
            }
        }
    }
    if let Some(path) = text("llm_other_path".into()) {
        set_entry(
            state,
            PATHS,
            "other",
            (!path.trim().is_empty()).then_some(path),
        );
    }
    if let Some(args) = text("llm_other_args".into()) {
        set_entry(
            state,
            ARGS,
            "other",
            (!args.trim().is_empty()).then_some(args),
        );
    }
}

/// Writes the shared keys of the fields `changed` accepts into the desktop map,
/// leaving its other keys and their order alone.
pub(super) fn write(
    settings: &Map<String, Value>,
    desktop: &mut Map<String, Value>,
    changed: &dyn Fn(&str) -> bool,
) {
    for (field, key, kind) in SHARED {
        if changed(field) {
            if let Some(value) = settings.get(field) {
                desktop.insert(key.to_owned(), encode(kind, value));
            }
        }
    }
    // Empty strings mean "cleared" so that a reset in the TUI reaches the desktop app.
    let lookup = |field: &str, prefix: &str| {
        Value::String(
            settings
                .get(field)
                .and_then(|map| map.get(prefix))
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_owned(),
        )
    };
    for provider in cli_providers() {
        let prefix = provider.prefix;
        if changed(PATHS) {
            desktop.insert(format!("llm_{prefix}_path"), lookup(PATHS, prefix));
        }
        if changed(ARGS) {
            desktop.insert(format!("llm_{prefix}_args"), lookup(ARGS, prefix));
        }
        if provider.internal_provider && changed(PROVIDERS) {
            desktop.insert(format!("llm_{prefix}_provider"), lookup(PROVIDERS, prefix));
        }
    }
    if changed(PATHS) {
        desktop.insert("llm_other_path".into(), lookup(PATHS, "other"));
    }
    if changed(ARGS) {
        desktop.insert("llm_other_args".into(), lookup(ARGS, "other"));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_shared_field_round_trips_through_the_desktop_format() {
        let settings = serde_json::to_value(crate::settings::TuiSettings {
            num_speakers: Some(3),
            formats: vec!["txt".into(), "vtt".into()],
            llm_temperature: 0.7,
            ..Default::default()
        })
        .unwrap();
        let settings = settings.as_object().unwrap();
        let mut desktop = Map::new();
        write(settings, &mut desktop, &|_| true);
        assert_eq!(desktop["llm_temperature"], "0.7");
        assert_eq!(desktop["output_formats"]["vtt"], true);
        assert_eq!(desktop["output_formats"]["srt"], false);
        let mut state = Map::new();
        read(&desktop, &mut state);
        for (field, _, _) in SHARED {
            assert_eq!(state.get(field), settings.get(field), "{field}");
        }
    }
}
