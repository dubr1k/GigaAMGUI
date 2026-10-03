use std::{
    fs,
    path::{Path, PathBuf},
};

use super::*;
use crate::{
    app::{llm_input_files, App},
    i18n::t,
    input::{split_paths, InputMode},
    options::selectable_backends,
    results::canonical_path,
};

#[test]
fn modal_confirmation_rejects_paste_and_automatic_input_completion() {
    let path = std::env::temp_dir().join(format!("gigaam-modal-{}.wav", std::process::id()));
    fs::write(&path, []).unwrap();
    let mut app = crate::test_support::ready_app();
    app.queue.add("/done.wav".into());
    app.queue.items[0].state = crate::queue::FileState::Done;
    app.begin_batch(crate::queue::RunSelection::Selected, false);
    let selected = app.queue.selected;
    paste_input(&mut app, &path.to_string_lossy());
    assert!(app.input.is_empty());
    app.input
        .open(InputMode::Paths, path.to_string_lossy().into());
    queue_complete_input(&mut app);
    assert!(app.pending_inputs.is_empty());
    assert!(app.outbox.is_empty());
    assert_eq!(app.queue.selected, selected);
    fs::remove_file(path).unwrap();
}

/// Протокольный ответ для существующих файлов этих тестов; обход папок покрыт в Python.
fn resolve_pending_files(app: &mut App) {
    let requests = app.take_outbox();
    assert!(!requests.is_empty());
    for request in requests {
        assert_eq!(request["type"], "resolve_inputs");
        let files: Vec<_> = request["paths"]
            .as_array()
            .unwrap()
            .iter()
            .map(|path| {
                let path = crate::input::local_path(path.as_str().unwrap()).unwrap();
                fs::canonicalize(path)
                    .unwrap()
                    .to_string_lossy()
                    .into_owned()
            })
            .collect();
        app.handle_message(serde_json::json!({
            "type":"inputs_resolved", "request_id":request["request_id"],
            "files":files, "duplicates":[], "errors":[], "cancelled":false
        }));
    }
}

#[test]
fn explicit_paste_is_submitted_even_with_invalid_files() {
    let mut app = crate::test_support::ready_app();
    paste_input(&mut app, "/missing/a.wav /missing/b.wav");
    assert_eq!(app.pending_inputs.len(), 1);
    assert!(app.input.is_empty());
    paste_input(&mut app, "/missing/c.wav");
    assert_eq!(app.pending_inputs.len(), 2);
    let outbox = app.take_outbox();
    assert_eq!(
        outbox[0]["paths"],
        serde_json::json!(["/missing/a.wav", "/missing/b.wav"])
    );
    assert_eq!(outbox[1]["paths"], serde_json::json!(["/missing/c.wav"]));
}

#[test]
fn raw_drop_consumes_only_complete_prefix_and_preserves_tail_bytes() {
    let file = std::env::temp_dir().join(format!("gigaam-prefix-{}.wav", std::process::id()));
    fs::write(&file, []).unwrap();
    let mut app = crate::test_support::ready_app();
    let tail = r#"/missing/незаконченный\ путь"#;
    app.input
        .open(InputMode::Paths, format!("{} {tail}", file.display()));
    queue_complete_input(&mut app);
    assert_eq!(app.pending_inputs.len(), 1);
    assert_eq!(app.input.text(), tail);
    fs::remove_file(file).unwrap();
}

#[test]
fn argument_paste_never_queues_media_and_invalid_arguments_remain_editable() {
    let _config = isolated_config_dir();
    let mut app = crate::test_support::ready_app();
    crate::app::dispatch(&mut app, crate::action::Action::EditCommand("/output"));
    paste_input(&mut app, "/tmp");
    assert!(app.queue.items.is_empty());
    assert_eq!(app.input.text(), "/output /tmp");
    app.input
        .open(InputMode::Argument("/lang"), "/lang xx".into());
    run_command(&mut app);
    assert_eq!(app.input.text(), "/lang xx");
    app.input.replace("/lang en".into());
    run_command(&mut app);
    assert_eq!(app.input.mode, InputMode::Hidden);
}

#[test]
fn duplicate_paste_is_consumed_without_resetting_the_completed_item() {
    let path = std::env::temp_dir().join(format!("gigaam-duplicate-{}.wav", std::process::id()));
    std::fs::write(&path, b"test").unwrap();
    let raw = path.to_string_lossy().to_string();
    let mut app = crate::test_support::ready_app();
    queue_paths(&mut app, &raw);
    resolve_pending_files(&mut app);
    app.queue.items[0].state = crate::queue::FileState::Done;
    app.input.replace(raw.clone());
    queue_paths(&mut app, &raw);
    resolve_pending_files(&mut app);
    assert_eq!(app.queue.items.len(), 1);
    assert_eq!(app.queue.items[0].state, crate::queue::FileState::Done);
    assert_eq!(app.queue.selected_index(), Some(0));
    assert!(app.input.is_empty());
    std::fs::remove_file(path).unwrap();
}

#[test]
fn clear_queue_keeps_transcripts_and_llm_results() {
    let mut app = crate::test_support::ready_app();
    app.result_files = vec!["/done.txt".into()];
    app.llm_extra_files = vec!["/notes.md".into()];
    app.llm_results = vec![("summary".into(), "answer".into())];
    clear_queue(&mut app);
    assert_eq!(app.result_files, vec!["/done.txt"]);
    assert_eq!(app.llm_extra_files, vec!["/notes.md"]);
    assert_eq!(app.llm_results, vec![("summary".into(), "answer".into())]);
}
use crate::{
    app::llm_can_run,
    i18n::Lang,
    requests::{llm_settings_payload, start_payload},
    settings::{isolated_config_dir, load_settings, TuiSettings},
    theme::Theme,
};

#[test]
fn theme_command_applies_the_theme_and_persists_it() {
    let _config = isolated_config_dir();
    let mut app = crate::test_support::ready_app();
    assert_eq!(app.theme.name, "default");
    app.input.replace("/theme dark-monokai".into());
    run_command(&mut app);
    assert_eq!(app.theme.name, "dark-monokai");
    assert_eq!(
        app.palette().accent,
        Theme::by_name("dark-monokai").unwrap().palette.accent
    );
    assert_eq!(
        app.status,
        t(Lang::Ru, "status.theme_set").replace("{value}", "dark-monokai")
    );
    assert_eq!(load_settings().theme, "dark-monokai");
}

#[test]
fn theme_command_rejects_an_unknown_name_and_points_at_the_menu() {
    let _config = isolated_config_dir();
    let mut app = crate::test_support::ready_app();
    app.input.replace("/theme nope".into());
    run_command(&mut app);
    assert_eq!(app.theme.name, "default");
    assert!(app.status.contains("nope"), "{}", app.status);
    assert!(app.status.contains("/theme"), "{}", app.status);
    assert_eq!(load_settings().theme, "default");
}

#[test]
fn theme_command_without_a_name_opens_the_menu_on_the_current_theme() {
    let _config = isolated_config_dir();
    let mut app = crate::test_support::ready_app();
    app.theme = Theme::by_name("dark-nord").unwrap();
    app.input.replace("/theme".into());
    run_command(&mut app);
    assert_eq!(app.command_menu.as_deref(), Some("/theme"));
    let options = command_menu_options(&app);
    let mut expected: Vec<String> = Theme::names().into_iter().map(str::to_owned).collect();
    expected.push(BACK_MENU_OPTION.to_owned());
    assert_eq!(options, expected);
    assert_eq!(options[app.command_menu_index], "dark-nord");

    app.command_menu_index = options.iter().position(|o| o == "mono").unwrap();
    apply_command_menu(&mut app);
    assert_eq!(app.theme.name, "mono");
    assert!(app.command_menu.is_none());
    assert!(app.input.is_empty());
    assert_eq!(load_settings().theme, "mono");
}

#[test]
fn theme_name_completion_extends_the_common_prefix() {
    assert_eq!(
        complete_theme_name("/theme dark-mono"),
        Some("/theme dark-mono".into())
    );
    assert_eq!(
        complete_theme_name("/theme dark-monok"),
        Some("/theme dark-monokai".into())
    );
    assert_eq!(
        complete_theme_name("/theme light-gr"),
        Some("/theme light-gruvbox".into())
    );
    assert_eq!(complete_theme_name("/theme zzz"), None);
    assert_eq!(complete_theme_name("/backend a"), None);
    assert_eq!(complete_theme_name("/theme"), None);
    assert!(complete_theme_name("/theme ").is_some());
}

#[test]
fn mouse_setting_persists_and_defaults_on() {
    let _config = isolated_config_dir();
    let mut app = crate::test_support::ready_app();
    assert!(app.mouse_enabled);
    app.input.replace("/mouse off".into());
    run_command(&mut app);
    assert!(!app.mouse_enabled);
    assert!(!load_settings().mouse);
}

#[test]
fn lang_command_switches_and_persists() {
    let _config = isolated_config_dir();
    let mut app = crate::test_support::ready_app();
    app.input.replace("/lang en".into());
    run_command(&mut app);
    assert_eq!(app.lang, Lang::En);
    assert_eq!(app.status, "Language: English");
    assert_eq!(load_settings().language, "en");
    app.input.replace("/lang xx".into());
    run_command(&mut app);
    assert_eq!(app.status, "Usage: /lang ru|en");
    assert_eq!(app.lang, Lang::En);
}

#[test]
fn output_command_reads_the_path_like_every_other_path_and_persists_it() {
    let config = isolated_config_dir();
    let mut app = crate::test_support::ready_app();
    let canonical = |path: &Path| canonical_path(path).unwrap().to_string_lossy().into_owned();

    let quoted = config.join("Quoted Dir");
    app.input
        .replace(format!("/output \"{}\"", quoted.display()));
    run_command(&mut app);
    assert_eq!(app.output_dir.as_deref(), Some(canonical(&quoted).as_str()));
    assert_eq!(load_settings().output_dir, app.output_dir, "persisted");

    #[cfg(not(windows))]
    {
        // A dropped folder arrives shell-escaped; the old code created `My\ Dir`.
        let target = config.join("My Dir");
        app.input.replace(format!(
            "/output {}",
            target.display().to_string().replace(' ', "\\ ")
        ));
        run_command(&mut app);
        assert_eq!(app.output_dir.as_deref(), Some(canonical(&target).as_str()));
        assert!(!config.join("My\\ Dir").exists());
    }

    app.input.replace("/output -".into());
    run_command(&mut app);
    assert_eq!(
        app.output_dir, None,
        "`-` puts results next to each file again"
    );
    assert_eq!(load_settings().output_dir, None);
}

#[test]
fn home_and_percent_encoded_urls_expand_in_one_place() {
    if let Some(home) = std::env::var_os("HOME") {
        assert_eq!(
            user_path("~/Записи").unwrap(),
            PathBuf::from(home).join("Записи")
        );
    }
    let directory = std::env::temp_dir().join(format!("gigaam-url-{}", std::process::id()));
    fs::create_dir_all(&directory).unwrap();
    let file = directory.join("é ё.txt");
    fs::write(&file, "x").unwrap();
    #[cfg(not(windows))]
    {
        let canonical = fs::canonicalize(&file).unwrap();
        let url = format!(
            "file://{}",
            canonical
                .to_string_lossy()
                .replace('é', "%C3%A9")
                .replace('ё', "%D1%91")
                .replace(' ', "%20")
        );
        assert_eq!(
            normalize_path(&url).unwrap(),
            canonical.to_string_lossy(),
            "only %20 used to be decoded"
        );
    }
    assert_eq!(
        normalize_path(&format!("'{}'", file.display())).unwrap(),
        normalize_path(&file.to_string_lossy()).unwrap()
    );
    fs::remove_dir_all(directory).unwrap();
}

#[test]
fn parent_name_uses_the_platform_separators() {
    assert_eq!(parent_name("/tmp/записи/a.wav"), "/tmp/записи");
    assert_eq!(parent_name("a.wav"), "");
    #[cfg(windows)]
    assert_eq!(parent_name(r"C:\Записи\a.wav"), r"C:\Записи");
}

#[test]
fn shell_path_split_keeps_escaped_and_quoted_spaces() {
    assert_eq!(
        split_paths(r#"/tmp/first\ file.mp3 "/tmp/second file.mp3""#, true),
        vec!["/tmp/first file.mp3", "/tmp/second file.mp3"]
    );
}

#[test]
fn subtitle_commands_validate_and_update_limits() {
    let _config = isolated_config_dir();
    let mut app = crate::test_support::ready_app();
    app.lang = Lang::En;
    app.input.replace("/subtitle-split off".into());
    run_command(&mut app);
    app.input.replace("/subtitle-lines 3".into());
    run_command(&mut app);
    app.input.replace("/subtitle-width 72".into());
    run_command(&mut app);

    assert!(!app.subtitle_sentence_split);
    assert_eq!(app.subtitle_max_lines, 3);
    assert_eq!(app.subtitle_max_width, 72);

    app.input.replace("/subtitle-width 5".into());
    run_command(&mut app);
    assert_eq!(app.subtitle_max_width, 72);
    assert_eq!(app.status, "Subtitle width must be between 20 and 100");
}

#[test]
fn diarization_backend_command_offers_both_backends() {
    let mut app = crate::test_support::ready_app();

    assert!(open_command_menu(&mut app, "/diarization-backend"));
    assert_eq!(
        command_menu_options(&app),
        vec!["pyannote", "onnx", "sortformer", BACK_MENU_OPTION]
    );
}

#[test]
fn backend_command_offers_onnx_and_provider_is_persisted() {
    let mut app = crate::test_support::ready_app();
    app.onnx_provider = "coreml".into();

    assert!(open_command_menu(&mut app, "/backend"));
    assert!(command_menu_options(&app).contains(&"onnx".to_string()));
    let serialized = serde_json::to_value(TuiSettings {
        onnx_provider: app.onnx_provider.clone(),
        ..TuiSettings::default()
    })
    .expect("settings serialize");
    assert_eq!(serialized["onnx_provider"], "coreml");
}

#[test]
fn sortformer_rejects_fixed_speaker_count() {
    let _config = isolated_config_dir();
    let mut app = crate::test_support::ready_app();
    app.lang = Lang::En;
    app.diarization_backend = "sortformer".into();
    app.input.replace("/speakers 2".into());

    run_command(&mut app);

    assert_eq!(app.num_speakers, None);
    assert_eq!(
        app.status,
        "Sortformer detects the speaker count automatically"
    );
}

#[test]
fn backend_command_opens_its_choices_with_the_current_value_selected() {
    let mut app = crate::test_support::ready_app();
    app.backend = selectable_backends()[0].into();

    assert!(open_command_menu(&mut app, "/backend"));

    assert_eq!(app.command_menu.as_deref(), Some("/backend"));
    assert_eq!(app.command_menu_index, 0);
    assert!(command_menu_options(&app)
        .iter()
        .any(|option| option == &app.backend));
}

#[test]
fn llm_model_command_is_accepted() {
    let _config = isolated_config_dir();
    let mut app = crate::test_support::ready_app();
    app.lang = Lang::En;
    app.input.replace("/llm-model gpt-4.1-mini".into());

    run_command(&mut app);

    assert_eq!(app.llm_model, "gpt-4.1-mini");
    assert_eq!(app.status, "LLM model saved");
}

#[test]
fn settings_menu_offers_all_llm_providers() {
    let mut app = crate::test_support::ready_app();
    assert!(
        !open_command_menu(&mut app, "/settings"),
        "/settings is a tab now"
    );
    assert!(open_command_menu(&mut app, "/settings-provider"));

    assert_eq!(
        command_menu_options(&app),
        vec![
            "API",
            "Claude Code",
            "Codex",
            "OpenCode",
            "Pi",
            "oh-my-pi",
            "Other",
            BACK_MENU_OPTION
        ]
    );
}

#[test]
fn provider_extras_reach_the_worker_settings_payload() {
    let _config = isolated_config_dir();
    let mut app = crate::test_support::ready_app();
    app.llm_provider = "oh-my-pi".into();
    app.input.replace("/llm-provider-name anthropic".into());
    run_command(&mut app);
    app.input.replace("/llm-args --thinking low".into());
    run_command(&mut app);
    app.input.replace("/llm-tools on".into());
    run_command(&mut app);
    app.input.replace("/llm-path /opt/homebrew/bin/omp".into());
    run_command(&mut app);

    let payload = llm_settings_payload(&app);
    assert_eq!(payload["omp_provider"], "anthropic");
    assert_eq!(payload["omp_args"], "--thinking low");
    assert_eq!(payload["omp_path"], "/opt/homebrew/bin/omp");
    assert_eq!(payload["llm_allow_tools"], true);
    assert!(app.llm_tool_check_requested.is_some());
}

#[test]
fn other_provider_uses_its_path_and_args() {
    let _config = isolated_config_dir();
    let mut app = crate::test_support::ready_app();
    app.llm_provider = "Other".into();
    app.input.replace("/llm-path /usr/local/bin/my-llm".into());
    run_command(&mut app);
    app.input.replace("/llm-args --stdin {stdin}".into());
    run_command(&mut app);

    let payload = llm_settings_payload(&app);
    assert_eq!(payload["other_path"], "/usr/local/bin/my-llm");
    assert_eq!(payload["other_args"], "--stdin {stdin}");
}

#[test]
fn provider_name_command_is_only_for_pi_like_providers() {
    let _config = isolated_config_dir();
    let mut app = crate::test_support::ready_app();
    app.lang = Lang::En;
    app.llm_provider = "Claude Code".into();
    app.input.replace("/llm-provider-name openai".into());
    run_command(&mut app);
    assert_eq!(
        app.status,
        "Internal provider applies to Pi and oh-my-pi only"
    );
}

#[test]
fn clear_suggestion_executes_without_an_extra_enter() {
    let _config = isolated_config_dir();
    let mut app = crate::test_support::ready_app();
    app.queue.add("/tmp/input.wav".into());
    app.result_files.push("/tmp/output.txt".into());

    accept_command_suggestion(&mut app, "/clear");

    assert!(app.queue.items.is_empty());
    assert_eq!(app.result_files, vec!["/tmp/output.txt"]);
    assert!(app.input.is_empty());
}

#[test]
fn pets_suggestion_executes_without_an_extra_enter() {
    let _config = isolated_config_dir();
    let mut app = crate::test_support::ready_app();
    app.lang = Lang::En;

    accept_command_suggestion(&mut app, "/pets");

    assert_eq!(
        app.status,
        "Pets require Kitty, iTerm2, or Sixel image support."
    );
    assert!(app.input.is_empty());
}

#[test]
fn back_option_closes_a_settings_menu_without_applying_a_change() {
    let _config = isolated_config_dir();
    let mut app = crate::test_support::ready_app();
    app.command_menu = Some("/diarize".into());
    app.command_menu_index = command_menu_options(&app)
        .iter()
        .position(|option| option == BACK_MENU_OPTION)
        .expect("Back option must be present");

    apply_command_menu(&mut app);

    assert!(app.command_menu.is_none());
    assert!(!app.diarization);
}

#[test]
fn shell_paths_preserve_nonbreaking_spaces_in_finder_copy_names() {
    let raw = "/Users/dubr1k/Downloads/Ректорат\\ 07.09\\ \\(1\\)\u{a0}—\\ копия.mp3";
    assert_eq!(
        split_paths(raw, true),
        vec!["/Users/dubr1k/Downloads/Ректорат 07.09 (1)\u{a0}— копия.mp3"]
    );
}

// Terminal drops are shell-escaped (`\ `) only on POSIX.
#[cfg(not(windows))]
#[test]
fn concatenated_drops_are_split_only_after_existing_files() {
    let directory =
        std::env::temp_dir().join(format!("gigaam-concatenated-{}", std::process::id()));
    fs::create_dir_all(&directory).unwrap();
    let first = directory.join("Ректорат (1).mp3");
    let second = directory.join("Ректорат (1) — копия.mp3");
    fs::write(&first, []).unwrap();
    fs::write(&second, []).unwrap();
    let escape = |path: &Path| {
        path.to_string_lossy()
            .replace(' ', "\\ ")
            .replace('(', "\\(")
            .replace(')', "\\)")
    };
    let mut app = crate::test_support::ready_app();
    app.queue.add("already-queued.wav".into());
    app.input
        .replace(format!("{}{}", escape(&first), escape(&second)));
    queue_complete_input(&mut app);
    resolve_pending_files(&mut app);
    assert_eq!(app.queue.items.len(), 3);
    assert!(app.queue.items[1].path.ends_with("Ректорат (1).mp3"));
    assert!(app.queue.items[2]
        .path
        .ends_with("Ректорат (1) — копия.mp3"));
    assert!(app.input.is_empty());
    app.input
        .replace(format!("{}/missing-partial-path", escape(&first)));
    queue_complete_input(&mut app);
    assert_eq!(app.input.text(), "/missing-partial-path");
    resolve_pending_files(&mut app);
    assert_eq!(app.queue.items.len(), 3);
    fs::remove_dir_all(directory).unwrap();
}

#[test]
fn automatic_queue_keeps_incomplete_paths_and_command_arguments() {
    let mut app = crate::test_support::ready_app();
    for input in [
        "/path/that/does/not/exist.wav",
        "/output /tmp",
        "/prompt объясни запись",
    ] {
        app.input.replace(input.into());
        queue_complete_input(&mut app);
        assert_eq!(app.input.text(), input);
        assert!(app.queue.items.is_empty());
    }
}

// Terminal drops are shell-escaped (`\ `) only on POSIX.
#[cfg(not(windows))]
#[test]
fn successive_pastes_queue_files_without_concatenating_paths() {
    let directory = std::env::temp_dir().join(format!("gigaam-paste-{}", std::process::id()));
    fs::create_dir_all(&directory).unwrap();
    let first = directory.join("Ректорат (1).mp3");
    let second = directory.join("Ректорат (1) — копия.mp3");
    fs::write(&first, []).unwrap();
    fs::write(&second, []).unwrap();
    let mut app = crate::test_support::ready_app();
    let escape = |path: &Path| {
        path.to_string_lossy()
            .replace(' ', "\\ ")
            .replace('(', "\\(")
            .replace(')', "\\)")
    };
    paste_input(&mut app, &escape(&first));
    paste_input(&mut app, &escape(&second));
    assert_eq!(app.pending_inputs.len(), 2);
    resolve_pending_files(&mut app);
    assert_eq!(app.queue.items.len(), 2);
    assert!(app.input.is_empty());
    assert_eq!(app.queue.selected_index(), Some(1));
    app.input.replace("/output ".into());
    paste_input(&mut app, &directory.to_string_lossy());
    assert!(app.input.starts_with("/output /"));
    assert_eq!(app.queue.items.len(), 2);
    fs::remove_dir_all(directory).unwrap();
}

#[test]
fn queue_paths_accepts_multiple_pasted_lines() {
    let directory = std::env::temp_dir().join(format!(
        "gigaam-tui-queue-paths-{}-{}",
        std::process::id(),
        std::thread::current().name().unwrap_or("test")
    ));
    fs::create_dir_all(&directory).unwrap();
    let first = directory.join("first.wav");
    let second = directory.join("second file.mp3");
    fs::write(&first, []).unwrap();
    fs::write(&second, []).unwrap();

    let mut app = crate::test_support::ready_app();
    queue_paths(
        &mut app,
        &format!("{}\n{}", first.display(), second.display()),
    );
    resolve_pending_files(&mut app);

    assert_eq!(app.queue.items.len(), 2);
    assert!(app
        .queue
        .items
        .iter()
        .any(|item| item.path.ends_with("first.wav")));
    assert!(app
        .queue
        .items
        .iter()
        .any(|item| item.path.ends_with("second file.mp3")));
    fs::remove_dir_all(directory).unwrap();
}

#[test]
fn llm_file_command_adds_text_inputs_and_rejects_media() {
    let _config = isolated_config_dir();
    let directory =
        std::env::temp_dir().join(format!("gigaam-tui-llm-file-{}", std::process::id()));
    fs::create_dir_all(&directory).unwrap();
    let transcript = directory.join("meeting.txt");
    let audio = directory.join("meeting.wav");
    fs::write(&transcript, "hello").unwrap();
    fs::write(&audio, []).unwrap();

    let mut app = crate::test_support::ready_app();
    app.lang = Lang::En;
    app.input
        .replace(format!("/llm-file {}", transcript.display()));
    run_command(&mut app);
    assert_eq!(llm_input_files(&app).len(), 1);
    assert!(llm_can_run(&app));

    app.input.replace(format!("/llm-file {}", audio.display()));
    run_command(&mut app);
    assert_eq!(app.status, "LLM input must be .txt, .md, .srt or .vtt");
    assert_eq!(llm_input_files(&app).len(), 1);

    app.input.replace("/clear".into());
    run_command(&mut app);
    assert_eq!(
        llm_input_files(&app).len(),
        1,
        "queue clear preserves LLM inputs"
    );
    fs::remove_dir_all(directory).unwrap();
}

#[test]
fn audio_mode_is_selectable_persisted_and_sent_to_the_worker() {
    let _config = isolated_config_dir();
    let mut app = crate::test_support::ready_app();
    app.lang = Lang::En;
    assert!(open_command_menu(&mut app, "/audio-mode"));
    assert_eq!(
        command_menu_options(&app),
        vec!["auto", "off", "light", "denoise", BACK_MENU_OPTION]
    );
    app.input.replace("/audio-mode denoise".into());
    run_command(&mut app);
    assert_eq!(app.audio_preprocessing_mode, "denoise");
    app.input.replace("/audio-mode loud".into());
    run_command(&mut app);
    assert_eq!(app.status, "Usage: /audio-mode auto|off|light|denoise");

    let payload = start_payload(&app, &app.queue.paths(crate::queue::RunSelection::Pending));
    assert_eq!(payload["audio_preprocessing_mode"], "denoise");
    let restored: TuiSettings = serde_json::from_str(
        &serde_json::to_string(&TuiSettings {
            audio_preprocessing_mode: "light".into(),
            ..TuiSettings::default()
        })
        .unwrap(),
    )
    .unwrap();
    assert_eq!(restored.audio_preprocessing_mode, "light");
}
