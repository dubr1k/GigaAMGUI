use serde_json::json;

use super::*;
use crate::{
    action::{Action, ButtonId},
    commands::provider_from_menu_option,
    commands::{command_menu_options, BACK_MENU_OPTION},
    i18n::Lang,
    lifecycle::{Activity, JobKind},
    options::selectable_backends,
    settings::isolated_config_dir,
};

#[test]
fn llm_start_is_locked_before_worker_acknowledges() {
    let mut app = crate::test_support::ready_app();
    app.llm_extra_files.push("/tmp/transcript.txt".into());
    let first = dispatch(&mut app, Action::Button(ButtonId::RunLlm));
    let second = dispatch(&mut app, Action::Button(ButtonId::RunLlm));
    assert_eq!(first.len(), 1);
    assert_eq!(first[0]["type"], "llm_start");
    assert!(second.is_empty(), "duplicate request before llm_started");
}

#[test]
fn dispatch_matches_the_keyboard_equivalents() {
    let _config = isolated_config_dir();
    let mut app = crate::test_support::ready_app();
    app.queue.add("/tmp/a.wav".into());
    app.queue.add("/tmp/b.wav".into());
    dispatch(&mut app, Action::SelectFile(1));
    assert_eq!(app.queue.selected_index(), Some(1));
    dispatch(&mut app, Action::Tab(Page::Settings));
    assert_eq!(app.page, Page::Settings);
    dispatch(&mut app, Action::OpenMenu("/backend"));
    assert_eq!(app.command_menu.as_deref(), Some("/backend"));
    dispatch(&mut app, Action::MenuItem(1));
    assert_eq!(app.backend, selectable_backends()[1]);
    dispatch(&mut app, Action::ToggleLang);
    assert_eq!(app.lang, Lang::En);
    let commands = dispatch(&mut app, Action::Button(ButtonId::Start));
    assert_eq!(commands[0]["type"], "start");
}

#[test]
fn next_step_follows_the_state_machine() {
    let mut app = crate::test_support::ready_app();
    assert_eq!(next_step(&app), "hint.add_files");
    app.queue.add("/tmp/a.wav".into());
    assert_eq!(next_step(&app), "hint.start");
    app.activity = crate::lifecycle::Activity::Running(crate::lifecycle::JobKind::Asr);
    assert_eq!(next_step(&app), "hint.cancel_batch");
    app.activity = crate::lifecycle::Activity::Idle;
    app.result_files.push("/tmp/a.txt".into());
    app.queue.items[0].state = FileState::Done;
    assert_eq!(next_step(&app), "hint.run_llm");
    app.activity = crate::lifecycle::Activity::Running(crate::lifecycle::JobKind::Llm);
    assert_eq!(next_step(&app), "hint.cancel_llm");
    app.activity = crate::lifecycle::Activity::Idle;
    app.llm_results.push(("summary".into(), "…".into()));
    assert_eq!(next_step(&app), "hint.view_result");
    app.connection.state = crate::lifecycle::ConnectionState::Unavailable;
    assert_eq!(next_step(&app), "hint.worker_down");
}

#[test]
fn file_states_follow_worker_events() {
    let mut app = crate::test_support::ready_app();
    app.queue.add("/tmp/a.wav".into());
    app.queue.add("/tmp/b.wav".into());
    assert_eq!(app.file_state("/tmp/a.wav"), FileState::Pending);
    app.activity.start(JobKind::Asr);
    app.handle_message(json!({"type": "started", "total_files": 2, "backend": "auto"}));
    app.handle_message(json!({
        "type": "file_started", "file": "/tmp/a.wav", "file_index": 0, "total_files": 2
    }));
    assert_eq!(app.file_state("/tmp/a.wav"), FileState::Processing);
    app.handle_message(json!({
        "type": "file_completed", "file": "/tmp/a.wav", "file_index": 0,
        "result": {"success": false, "saved_files": [], "error": "x"}
    }));
    assert_eq!(app.file_state("/tmp/a.wav"), FileState::Failed);
    app.handle_message(json!({"type": "completed", "success": false, "cancelled": true}));
    assert_eq!(
        app.file_state("/tmp/a.wav"),
        FileState::Failed,
        "a finished file keeps its outcome"
    );
    assert_eq!(
        app.file_state("/tmp/b.wav"),
        FileState::Pending,
        "unstarted files remain available for the next batch"
    );
    // The next batch starts from a clean slate.
    app.activity.start(JobKind::Asr);
    app.handle_message(json!({"type": "started", "total_files": 2, "backend": "auto"}));
    assert_eq!(app.file_state("/tmp/b.wav"), FileState::Pending);
}

#[test]
fn stop_sends_one_graceful_cancel_per_batch() {
    let mut app = crate::test_support::ready_app();
    assert!(dispatch(&mut app, Action::Button(ButtonId::Stop)).is_empty());
    app.activity.start(JobKind::Asr);
    app.handle_message(json!({"type": "started", "total_files": 1, "backend": "auto"}));
    let commands = dispatch(&mut app, Action::Button(ButtonId::Stop));
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0]["type"], "cancel");
    assert!(dispatch(&mut app, Action::Button(ButtonId::Stop)).is_empty());
    app.handle_message(json!({"type": "cancelling", "message": "Cancelling…"}));
    assert!(dispatch(&mut app, Action::Button(ButtonId::Stop)).is_empty());
    app.handle_message(json!({"type": "completed", "success": false, "cancelled": true}));
    app.activity.start(JobKind::Asr);
    app.handle_message(json!({"type": "started", "total_files": 1, "backend": "auto"}));
    assert_eq!(
        dispatch(&mut app, Action::Button(ButtonId::Stop)).len(),
        1,
        "a new batch can be cancelled again"
    );
}

#[test]
fn removing_a_file_forgets_its_state() {
    let mut app = crate::test_support::ready_app();
    app.queue.add("/tmp/a.wav".into());
    app.queue.find_mut("/tmp/a.wav").unwrap().state = FileState::Failed;
    dispatch(&mut app, Action::RemoveFile(0));
    assert!(app.queue.items.is_empty());
    app.queue.add("/tmp/a.wav".into());
    assert_eq!(app.file_state("/tmp/a.wav"), FileState::Pending);
}

#[test]
fn open_menu_prefills_the_command_line_for_typed_parameters() {
    let _config = isolated_config_dir();
    let mut app = crate::test_support::ready_app();
    app.focus = Focus::Params;
    dispatch(&mut app, Action::OpenMenu("/output"));
    assert_eq!(app.command_menu, None);
    assert_eq!(app.input.text(), "/output ");
    assert_eq!(app.focus, Focus::Input);
}

#[test]
fn llm_tools_message_fills_providers_and_menu_shows_status() {
    let mut app = crate::test_support::ready_app();
    app.handle_message(json!({
        "type": "llm_tools",
        "providers": ["API", "Claude Code", "Other"],
        "tools": [
            {"id": "claude", "provider": "Claude Code", "status": "found",
             "path": "/opt/homebrew/bin/claude", "version": "2.1.275",
             "detail": null, "install_hint": "npm install -g @anthropic-ai/claude-code"},
            {"id": "codex", "provider": "Codex", "status": "missing",
             "path": null, "version": null, "detail": null,
             "install_hint": "npm install -g @openai/codex"}
        ]
    }));

    assert_eq!(app.llm_providers, vec!["API", "Claude Code", "Other"]);
    assert_eq!(app.llm_tools.len(), 2);
    app.command_menu = Some("/settings-provider".into());
    assert_eq!(
        command_menu_options(&app),
        vec!["API", "Claude Code · 2.1.275", "Other", BACK_MENU_OPTION]
    );
    assert_eq!(
        provider_from_menu_option("Claude Code · 2.1.275"),
        "Claude Code"
    );
    assert_eq!(provider_from_menu_option("Codex · not installed"), "Codex");
}

#[test]
fn llm_chunks_stream_into_the_view_and_results_are_kept() {
    let mut app = crate::test_support::ready_app();
    app.activity.start(JobKind::Llm);
    app.handle_message(json!({"type": "llm_started", "mode": "summary", "index": 1, "total": 1}));
    assert!(app.llm_running() && app.running());
    app.handle_message(json!({"type": "llm_chunk", "mode": "summary", "text": "Итог: "}));
    app.handle_message(json!({"type": "llm_chunk", "mode": "summary", "text": "всё хорошо"}));
    assert_eq!(app.llm_stream, "Итог: всё хорошо");
    app.handle_message(json!({
        "type": "llm_completed", "success": true, "saved_files": ["/tmp/session_llm_summary.txt"],
        "results": [{"mode": "summary", "text": "Итог: всё хорошо"}]
    }));
    assert!(!app.llm_running() && !app.running());
    assert_eq!(
        app.llm_results,
        vec![("summary".to_string(), "Итог: всё хорошо".to_string())]
    );
    assert!(app.llm_stream.is_empty());
}

#[test]
fn a_new_llm_run_clears_the_previous_results() {
    let mut app = crate::test_support::ready_app();
    app.activity.start(JobKind::Llm);
    app.handle_message(json!({"type": "llm_started", "mode": "summary", "index": 1, "total": 1}));
    app.handle_message(json!({
        "type": "llm_completed", "success": true, "saved_files": [],
        "results": [{"mode": "summary", "text": "old"}]
    }));
    assert!(!app.llm_results.is_empty());

    app.activity.start(JobKind::Llm);
    app.handle_message(json!({"type": "llm_started", "mode": "tasks", "index": 1, "total": 1}));
    assert!(app.llm_results.is_empty());
    // A completion without `results` (worker failure) must not resurrect the old run.
    app.handle_message(json!({"type": "llm_completed", "success": false, "error": "boom"}));
    assert!(app.llm_results.is_empty());
}

#[test]
fn esc_soft_cancels_an_llm_run_only_once_then_falls_through_to_the_kill_path() {
    let mut app = crate::test_support::ready_app();
    assert!(
        !esc_should_soft_cancel(&app),
        "idle: Esc is not an LLM cancel"
    );

    app.activity.start(JobKind::Llm);
    app.handle_message(json!({"type": "llm_started", "mode": "summary", "index": 1, "total": 1}));
    assert!(esc_should_soft_cancel(&app), "first Esc sends llm_cancel");

    app.activity.stop(); // what the first Esc arm sets
    assert!(app.llm_running() && app.running());
    assert!(
        !esc_should_soft_cancel(&app),
        "second Esc must reach the `running` double-Esc kill/restart arm"
    );
}

#[test]
fn esc_closes_the_help_before_it_cancels_a_run() {
    let mut app = crate::test_support::ready_app();
    assert!(!esc_is_cancel(&app), "idle: Esc is not a cancel");
    app.activity.start(JobKind::Asr);
    app.handle_message(json!({"type": "started", "total_files": 1, "backend": "auto"}));
    assert!(esc_is_cancel(&app));
    dispatch(&mut app, Action::Help);
    assert!(
        app.help_open,
        "the header button opens the help during a run"
    );
    assert!(
        !esc_is_cancel(&app),
        "Esc goes to handle_key, which closes the overlay"
    );
    let commands = crate::keys::handle_key(
        &mut app,
        crossterm::event::KeyEvent::new(
            crossterm::event::KeyCode::Esc,
            crossterm::event::KeyModifiers::NONE,
        ),
    );
    assert!(commands.is_empty(), "no cancel was sent");
    assert!(!app.help_open);
    assert!(!app.cancelled);
    assert!(esc_is_cancel(&app), "the next Esc is the cancel again");
}

#[test]
fn worker_restart_resets_every_in_flight_flag() {
    let mut app = crate::test_support::ready_app();
    app.activity.start(JobKind::Llm);
    app.handle_message(json!({"type": "llm_started", "mode": "summary", "index": 1, "total": 1}));
    app.handle_message(json!({"type": "llm_chunk", "mode": "summary", "text": "partial"}));
    app.activity.stop();
    app.cancelled = false;

    reset_after_worker_restart(&mut app);

    assert!(!app.running());
    assert!(app.cancelled);
    assert!(!app.llm_running());
    assert_eq!(app.activity, Activity::Idle);
    assert!(app.outbox.is_empty());
    assert!(app.llm_stream.is_empty());
}

#[test]
fn cancelled_llm_run_is_reported_without_an_error() {
    let mut app = crate::test_support::ready_app();
    app.lang = Lang::En;
    app.activity.start(JobKind::Llm);
    app.handle_message(json!({"type": "llm_started", "mode": "tasks", "index": 1, "total": 2}));
    app.handle_message(
        json!({"type": "llm_completed", "success": false, "cancelled": true,
                              "saved_files": [], "results": []}),
    );
    assert_eq!(app.status, "LLM cancelled");
    assert!(!app.llm_running());
}
