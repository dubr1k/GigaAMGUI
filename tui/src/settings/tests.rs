use std::fs;

use serde_json::Value;

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

    let main: Value =
        serde_json::from_str(&fs::read_to_string(directory.join("user_settings.json")).unwrap())
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

    let settings = TuiSettings {
        theme: "dark-gruvbox".into(),
        ..TuiSettings::default()
    };
    save_settings(&settings).unwrap();
    let text = fs::read_to_string(directory.join("tui_settings.json")).unwrap();
    assert!(text.contains("\"theme\": \"dark-gruvbox\""), "{text}");
    assert_eq!(load_settings().theme, "dark-gruvbox");
    apply_settings(&mut app, load_settings(), None);
    assert_eq!(app.theme.name, "dark-gruvbox");
    assert_eq!(TuiSettings::from(&app).theme, "dark-gruvbox");

    // A name this build does not know falls back to the default look.
    let settings = TuiSettings {
        theme: "removed-theme".into(),
        ..TuiSettings::default()
    };
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
    let main: Value =
        serde_json::from_str(&fs::read_to_string(directory.join("user_settings.json")).unwrap())
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

fn read_json(path: &Path) -> Value {
    serde_json::from_str(&fs::read_to_string(path).unwrap()).unwrap()
}

#[test]
fn a_save_writes_only_what_the_tui_changed_and_keeps_desktop_edits() {
    let directory = isolated_config_dir();
    let main = directory.join("user_settings.json");
    fs::write(
        &main,
        r#"{"window_geometry":"keep-me","asr_backend":"auto","asr_model":"v3_e2e_rnnt","llm_provider":"API"}"#,
    )
    .unwrap();
    let mut app = crate::test_support::ready_app();
    apply_settings(&mut app, load_settings(), None);
    // The desktop app changes the model and the provider while the TUI is open.
    fs::write(
        &main,
        r#"{"window_geometry":"keep-me","asr_backend":"auto","asr_model":"multilingual_ctc","llm_provider":"Codex","future_key":1}"#,
    )
    .unwrap();

    app.mouse_enabled = false; // a TUI-only setting
    save_app_settings(&mut app);
    let saved = read_json(&main);
    assert_eq!(saved["asr_model"], "multilingual_ctc", "desktop edit kept");
    assert_eq!(saved["llm_provider"], "Codex");
    assert_eq!(saved["future_key"], 1);
    assert!(!load_settings().mouse);

    app.backend = "onnx".into();
    save_app_settings(&mut app);
    let saved = read_json(&main);
    assert_eq!(
        saved["asr_backend"], "onnx",
        "the TUI's own change is written"
    );
    assert_eq!(saved["asr_model"], "multilingual_ctc");
    assert_eq!(saved["llm_provider"], "Codex");
}

#[test]
fn values_this_build_cannot_use_are_left_alone() {
    let directory = isolated_config_dir();
    let main = directory.join("user_settings.json");
    fs::write(
        &main,
        r#"{"asr_backend":"tpu","diarization_backend":"future-engine","asr_model":"v9"}"#,
    )
    .unwrap();
    let mut app = crate::test_support::ready_app();
    apply_settings(&mut app, load_settings(), None);
    assert_eq!(
        app.backend, "auto",
        "the UI never offers what it cannot run"
    );
    app.diarization = true;
    save_app_settings(&mut app);
    let saved = read_json(&main);
    assert_eq!(saved["asr_backend"], "tpu");
    assert_eq!(saved["diarization_backend"], "future-engine");
    assert_eq!(saved["asr_model"], "v9");
    assert_eq!(saved["enable_diarization"], true);
}

#[test]
fn keys_of_a_newer_tui_survive_a_save_of_its_settings_file() {
    let directory = isolated_config_dir();
    let own = directory.join("tui_settings.json");
    fs::write(&own, r#"{"mouse":true,"future_tui_key":"keep"}"#).unwrap();
    let mut app = crate::test_support::ready_app();
    apply_settings(&mut app, load_settings(), None);
    app.mouse_enabled = false;
    save_app_settings(&mut app);
    let saved = read_json(&own);
    assert_eq!(saved["future_tui_key"], "keep");
    assert_eq!(saved["mouse"], false);
}

#[test]
fn one_malformed_value_does_not_reset_every_setting() {
    let directory = isolated_config_dir();
    fs::write(
        directory.join("tui_settings.json"),
        r#"{"mouse":"yes","pet_enabled":true,"theme":"dark-nord"}"#,
    )
    .unwrap();
    let settings = load_settings();
    assert!(
        settings.mouse,
        "the malformed value falls back to its default"
    );
    assert!(settings.pet_enabled);
    assert_eq!(settings.theme, "dark-nord");
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
