//! Keyboard handling: every key press becomes state changes on [`App`] and, for the
//! keys that start or stop work, the commands the caller must send to the worker.
//! The function never touches the terminal or the worker process, so the tests
//! drive it with synthetic [`KeyEvent`]s.
//!
//! Cancellation keys emit actions too; the runtime alone owns process shutdown.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use serde_json::Value;

use crate::{
    app::{dispatch, esc_is_cancel, esc_should_soft_cancel, llm_can_run, on_off, App, Focus, Page},
    commands::{
        apply_command_menu, command_menu_options, command_suggestions, complete_path,
        complete_theme_name, is_command, open_command_menu, queue_paths, remove_selected_file,
        run_command, COMMANDS,
    },
    i18n::{t, tf},
    input::InputMode,
    settings::save_app_settings,
    ui::{llm::MODES, processing::PARAM_ROWS, Action, AreaId, ButtonId},
};

/// Lines `PgUp` / `PgDn` move the LLM answer, the log and the help by.
const ANSWER_PAGE: i32 = 10;

/// Terminal events contain text, not physical key codes. Cyrillic aliases cover
/// ЙЦУКЕН layouts; function keys provide unambiguous alternatives in any layout.
/// Keep the original event for text insertion when a shortcut is not applicable.
fn shortcut_code(code: KeyCode, ctrl: bool) -> KeyCode {
    match code {
        KeyCode::Char(c) => KeyCode::Char(match c {
            'й' | 'Й' => 'q',
            'ы' | 'Ы' | 'і' | 'І' => 's',
            'в' | 'В' => 'd',
            'а' | 'А' => 'f',
            'к' | 'К' => 'r',
            'д' => 'l',
            'Д' if !ctrl => 'L',
            'Д' => 'l',
            'с' | 'С' if ctrl => 'c',
            'Q' | 'S' | 'D' | 'F' | 'R' => c.to_ascii_lowercase(),
            'C' | 'L' if ctrl => c.to_ascii_lowercase(),
            _ => c,
        }),
        _ => code,
    }
}

pub(crate) fn handle_key(app: &mut App, key: KeyEvent) -> Vec<Value> {
    let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
    let code = shortcut_code(key.code, ctrl);
    if app.stop_confirmation {
        return match key.code {
            KeyCode::Char('y' | 'Y' | 'н' | 'Н') => dispatch(app, Action::ConfirmStop(true)),
            KeyCode::Esc | KeyCode::Char('n' | 'N' | 'т' | 'Т') => {
                dispatch(app, Action::ConfirmStop(false))
            }
            _ => Vec::new(),
        };
    }
    if app.rerun_confirmation.is_some() {
        return match key.code {
            KeyCode::Char('y' | 'Y' | 'н' | 'Н') => dispatch(app, Action::ConfirmRerun(true)),
            KeyCode::Esc | KeyCode::Char('n' | 'N' | 'т' | 'Т') => {
                dispatch(app, Action::ConfirmRerun(false))
            }
            _ => Vec::new(),
        };
    }
    if app.show_path {
        return match code {
            KeyCode::Esc | KeyCode::Enter => dispatch(app, Action::ShowPath(false)),
            KeyCode::Up => dispatch(app, Action::Scroll(AreaId::Path, -1)),
            KeyCode::Down => dispatch(app, Action::Scroll(AreaId::Path, 1)),
            KeyCode::PageUp => dispatch(app, Action::Scroll(AreaId::Path, -ANSWER_PAGE)),
            KeyCode::PageDown => dispatch(app, Action::Scroll(AreaId::Path, ANSWER_PAGE)),
            _ => Vec::new(),
        };
    }
    if app.results_open {
        return match code {
            KeyCode::Esc | KeyCode::F(9) => dispatch(app, Action::ShowResults(false)),
            KeyCode::Enter => dispatch(app, Action::OpenResult(false)),
            KeyCode::Char('o' | 'O' | 'щ' | 'Щ') => dispatch(app, Action::OpenResult(true)),
            KeyCode::Up => dispatch(app, Action::Scroll(AreaId::Results, -1)),
            KeyCode::Down => dispatch(app, Action::Scroll(AreaId::Results, 1)),
            KeyCode::Home => dispatch(app, Action::SelectResult(0)),
            KeyCode::End => dispatch(app, Action::SelectResult(usize::MAX)),
            KeyCode::PageUp => dispatch(app, Action::Scroll(AreaId::ResultPath, -ANSWER_PAGE)),
            KeyCode::PageDown => dispatch(app, Action::Scroll(AreaId::ResultPath, ANSWER_PAGE)),
            _ => Vec::new(),
        };
    }
    // The help overlay is modal: it scrolls, closes, and swallows everything else
    // except Ctrl+C, which must always reach the quit path.
    if app.help_open && !(ctrl && code == KeyCode::Char('c')) {
        return match code {
            KeyCode::Esc | KeyCode::Char('?') | KeyCode::F(12) => dispatch(app, Action::Help),
            KeyCode::Up => dispatch(app, Action::Scroll(AreaId::Help, -1)),
            KeyCode::Down => dispatch(app, Action::Scroll(AreaId::Help, 1)),
            KeyCode::PageUp => dispatch(app, Action::Scroll(AreaId::Help, -ANSWER_PAGE)),
            KeyCode::PageDown => dispatch(app, Action::Scroll(AreaId::Help, ANSWER_PAGE)),
            _ => Vec::new(),
        };
    }
    let idle = !app.running();
    let menu_open = app.command_menu.is_some();
    if code == KeyCode::Esc && !app.pending_inputs.is_empty() {
        app.cancel_inputs();
        app.status = t(app.lang, "status.inputs_cancelled").into();
        return Vec::new();
    }
    if idle && !menu_open && app.input.mode != InputMode::Hidden {
        let handled = match key.code {
            KeyCode::Char('u' | 'U' | 'г' | 'Г') if ctrl => {
                app.input.clear();
                true
            }
            KeyCode::Char(c) if !ctrl && !key.modifiers.contains(KeyModifiers::ALT) => {
                app.input.insert(&c.to_string());
                app.selected_command = 0;
                true
            }
            KeyCode::Left => {
                app.input.left();
                true
            }
            KeyCode::Right => {
                app.input.right();
                true
            }
            KeyCode::Home => {
                app.input.home();
                true
            }
            KeyCode::End => {
                app.input.end();
                true
            }
            KeyCode::Backspace => {
                app.input.backspace();
                true
            }
            KeyCode::Delete => {
                app.input.delete();
                true
            }
            KeyCode::Esc => {
                app.input.close();
                app.focus = Focus::Queue;
                true
            }
            _ => false,
        };
        if handled {
            return Vec::new();
        }
    }
    let no_input = app.input.mode == InputMode::Hidden && !menu_open;
    let on_page = |page: Page| app.page == page && no_input && !menu_open;
    match code {
        KeyCode::Char('r') if ctrl && no_input => return dispatch(app, Action::Reconnect),
        KeyCode::Char('z' | 'Z' | 'я' | 'Я') if ctrl && idle && no_input => {
            return dispatch(app, Action::UndoRemove)
        }
        KeyCode::Insert if idle && no_input => return dispatch(app, Action::AddFiles),
        KeyCode::F(11) => return dispatch(app, Action::Button(ButtonId::ClearLog)),
        KeyCode::Char('l') if ctrl => return dispatch(app, Action::Button(ButtonId::ClearLog)),
        KeyCode::Char('c') if idle && key.modifiers.contains(KeyModifiers::CONTROL) => {
            app.request_exit("ctrl-c", "Ctrl+C");
        }
        KeyCode::Char('q') | KeyCode::F(10) if idle && no_input => app.exit_requested = true,
        KeyCode::F(1) => return dispatch(app, Action::Tab(Page::Processing)),
        KeyCode::F(2) => return dispatch(app, Action::Tab(Page::Llm)),
        KeyCode::F(3) => return dispatch(app, Action::Tab(Page::Settings)),
        KeyCode::F(4) => return dispatch(app, Action::Tab(Page::Log)),
        KeyCode::Tab if no_input && !menu_open => {
            return dispatch(app, Action::Tab(app.page.next()));
        }
        KeyCode::BackTab => return dispatch(app, Action::Tab(app.page.previous())),
        KeyCode::Right if on_page(Page::Processing) => app.focus = Focus::Params,
        KeyCode::Left if on_page(Page::Processing) => app.focus = Focus::Queue,
        KeyCode::Char('L') | KeyCode::F(6) if idle && no_input => {
            return dispatch(app, Action::Button(ButtonId::RunLlm));
        }
        KeyCode::Char('l') if idle && no_input && llm_can_run(app) => {
            return dispatch(app, Action::Button(ButtonId::RunLlm));
        }
        KeyCode::F(9) if no_input => {
            return dispatch(app, Action::ShowResults(true));
        }
        KeyCode::Char('r') if no_input && !app.saved_results().is_empty() => {
            return dispatch(app, Action::ShowResults(true));
        }
        KeyCode::PageUp if app.page == Page::Llm && !menu_open => {
            return dispatch(app, Action::Scroll(AreaId::LlmOutput, -ANSWER_PAGE));
        }
        KeyCode::PageDown if app.page == Page::Llm && !menu_open => {
            return dispatch(app, Action::Scroll(AreaId::LlmOutput, ANSWER_PAGE));
        }
        // The Log page: the wheel keys move the view, `End` re-arms the follow.
        KeyCode::Up if on_page(Page::Log) => return dispatch(app, Action::Scroll(AreaId::Log, -1)),
        KeyCode::Down if on_page(Page::Log) => {
            return dispatch(app, Action::Scroll(AreaId::Log, 1))
        }
        KeyCode::PageUp if on_page(Page::Log) => {
            return dispatch(app, Action::Scroll(AreaId::Log, -ANSWER_PAGE));
        }
        KeyCode::PageDown if on_page(Page::Log) => {
            return dispatch(app, Action::Scroll(AreaId::Log, ANSWER_PAGE));
        }
        KeyCode::End if on_page(Page::Log) => app.log_follow = true,
        // The Settings page: a cursor list, `Enter` performs the row's action.
        KeyCode::Up if on_page(Page::Settings) => {
            return dispatch(app, Action::Scroll(AreaId::Settings, -1));
        }
        KeyCode::Down if on_page(Page::Settings) => {
            return dispatch(app, Action::Scroll(AreaId::Settings, 1));
        }
        KeyCode::Enter if idle && on_page(Page::Settings) => {
            return dispatch(app, Action::SettingsRow(app.settings_cursor));
        }
        // The LLM page: one cursor walks the transcripts and then the mode rows.
        KeyCode::Up if idle && on_page(Page::Llm) => match app.llm_mode_cursor {
            Some(0) => app.llm_mode_cursor = None,
            Some(index) => app.llm_mode_cursor = Some(index - 1),
            None => {
                let index = app.llm_input_cursor.saturating_sub(1);
                return dispatch(app, Action::LlmInput(index));
            }
        },
        KeyCode::Down if idle && on_page(Page::Llm) => match app.llm_mode_cursor {
            Some(index) => app.llm_mode_cursor = Some((index + 1).min(MODES.len() - 1)),
            None if app.llm_input_cursor + 1 < crate::app::llm_input_files(app).len() => {
                return dispatch(app, Action::LlmInput(app.llm_input_cursor + 1));
            }
            None => app.llm_mode_cursor = Some(0),
        },
        KeyCode::Char(' ') if idle && on_page(Page::Llm) => {
            if let Some(index) = app.llm_mode_cursor {
                return dispatch(app, Action::ToggleMode(MODES[index.min(MODES.len() - 1)].0));
            }
        }
        KeyCode::Delete | KeyCode::Backspace
            if idle && on_page(Page::Llm) && app.llm_mode_cursor.is_none() =>
        {
            return dispatch(app, Action::RemoveLlmInput(app.llm_input_cursor));
        }
        KeyCode::Char('d') | KeyCode::F(7) if idle && no_input => {
            app.diarization = !app.diarization;
            app.log(tf(
                app.lang,
                "status.diarization",
                &[("value", on_off(app.lang, app.diarization))],
            ));
            save_app_settings(app);
        }
        KeyCode::Char('f') | KeyCode::F(8) if idle && no_input => {
            app.formats = if app.formats.len() == 1 {
                vec!["txt".into(), "srt".into()]
            } else {
                vec!["txt".into()]
            };
            app.log(tf(
                app.lang,
                "status.formats",
                &[("formats", &app.formats.join(", "))],
            ));
            save_app_settings(app);
        }
        KeyCode::Char('s') | KeyCode::F(5) if idle && no_input && !app.queue.items.is_empty() => {
            return dispatch(app, Action::Button(ButtonId::Start));
        }
        // Reachable during a run too: the overlay only reads, it never touches the worker.
        KeyCode::Char('?') | KeyCode::F(12) if no_input => return dispatch(app, Action::Help),
        KeyCode::Esc if esc_should_soft_cancel(app) => {
            return dispatch(app, Action::Button(ButtonId::CancelLlm));
        }
        KeyCode::Esc if esc_is_cancel(app) => {
            if app.activity.is_stopping() {
                return dispatch(app, Action::ForceStop);
            }
            return dispatch(app, Action::Button(ButtonId::Stop));
        }
        KeyCode::Esc if menu_open => {
            app.command_menu = None;
            app.input.close();
            app.status = t(app.lang, "status.menu_closed").into();
        }
        KeyCode::Esc if idle && app.focus != Focus::Input => {
            app.focus = Focus::Queue;
        }
        KeyCode::Esc if no_input => app.request_exit("esc", "Esc"),
        KeyCode::Esc => {
            app.input.close();
            app.status = t(app.lang, "status.input_cleared").into();
        }
        KeyCode::Char(digit) if idle && menu_open && digit.is_ascii_digit() => {
            let count = command_menu_options(app).len();
            let index = if digit == '0' {
                count.saturating_sub(1)
            } else {
                digit.to_digit(10).unwrap_or_default().saturating_sub(1) as usize
            };
            if index < count {
                return dispatch(app, Action::MenuItem(index));
            }
        }
        KeyCode::Char(' ') | KeyCode::Enter if idle && menu_open => apply_command_menu(app),
        KeyCode::Enter if idle && on_page(Page::Processing) && app.focus == Focus::Params => {
            let (_, command) = PARAM_ROWS[app.params_cursor.min(PARAM_ROWS.len() - 1)];
            return dispatch(app, Action::OpenMenu(command));
        }
        KeyCode::Enter if on_page(Page::Processing) && app.queue.selected_index().is_some() => {
            return dispatch(
                app,
                if idle {
                    Action::QueueActions
                } else {
                    Action::ShowPath(true)
                },
            );
        }
        KeyCode::Enter if idle => {
            if let InputMode::Argument(command) = app.input.mode {
                if app.input.split_whitespace().next() != Some(command) {
                    app.input.replace(format!("{command} {}", app.input.text()));
                }
                run_command(app);
                return Vec::new();
            }
            let raw = app.input.trim().to_string();
            let suggestions = command_suggestions(&raw);
            let has_argument = raw.split_whitespace().count() > 1;
            if !suggestions.is_empty()
                && (!has_argument || !raw.contains(' '))
                && !COMMANDS.iter().any(|(name, _)| *name == raw)
            {
                let index = app.selected_command.min(suggestions.len() - 1);
                return dispatch(app, Action::Suggestion(index));
            } else if open_command_menu(app, &raw) {
            } else if is_command(&raw) {
                run_command(app);
            } else if !raw.is_empty() {
                queue_paths(app, &raw);
            }
        }
        KeyCode::Tab if idle => {
            let suggestions = command_suggestions(&app.input);
            if let Some(completed) = complete_theme_name(&app.input) {
                app.input.replace(completed);
            } else if !suggestions.is_empty() {
                let index = app.selected_command.min(suggestions.len() - 1);
                app.input.replace(format!("{} ", suggestions[index].0));
                app.selected_command = 0;
            } else if let Some(path) = complete_path(&app.input) {
                app.input.replace(path);
            }
        }
        KeyCode::Up if idle && menu_open => {
            let count = command_menu_options(app).len();
            app.command_menu_index = (app.command_menu_index + count - 1) % count;
        }
        KeyCode::Down if idle && menu_open => {
            let count = command_menu_options(app).len();
            app.command_menu_index = (app.command_menu_index + 1) % count;
        }
        KeyCode::Up if idle && !command_suggestions(&app.input).is_empty() => {
            let count = command_suggestions(&app.input).len();
            app.selected_command = (app.selected_command + count - 1) % count;
        }
        KeyCode::Down if idle && !command_suggestions(&app.input).is_empty() => {
            let count = command_suggestions(&app.input).len();
            app.selected_command = (app.selected_command + 1) % count;
        }
        KeyCode::Up if on_page(Page::Processing) && app.focus == Focus::Params => {
            app.params_cursor = (app.params_cursor + PARAM_ROWS.len() - 1) % PARAM_ROWS.len();
        }
        KeyCode::Down if on_page(Page::Processing) && app.focus == Focus::Params => {
            app.params_cursor = (app.params_cursor + 1) % PARAM_ROWS.len();
        }
        KeyCode::Up if on_page(Page::Processing) => {
            if ctrl {
                if idle {
                    app.queue.move_selected(-1);
                }
            } else if !app.queue.items.is_empty() {
                let index = app.queue.selected_index().unwrap_or(0).saturating_sub(1);
                return dispatch(app, Action::SelectFile(index));
            }
        }
        KeyCode::Down if on_page(Page::Processing) => {
            if ctrl {
                if idle {
                    app.queue.move_selected(1);
                }
            } else if !app.queue.items.is_empty() {
                let index =
                    (app.queue.selected_index().unwrap_or(0) + 1).min(app.queue.items.len() - 1);
                return dispatch(app, Action::SelectFile(index));
            }
        }
        KeyCode::Delete | KeyCode::Backspace
            if idle && on_page(Page::Processing) && app.focus != Focus::Params =>
        {
            match app.queue.selected_index() {
                Some(index) => return dispatch(app, Action::RemoveFile(index)),
                None => remove_selected_file(app),
            }
        }
        KeyCode::Backspace if idle => {
            app.input.backspace();
        }
        KeyCode::Char(_) if idle && !ctrl && !key.modifiers.contains(KeyModifiers::ALT) => {
            app.command_menu = None;
            app.focus = Focus::Input;
            if let KeyCode::Char(c) = key.code {
                app.input.open(
                    if c == '/' {
                        InputMode::Command
                    } else {
                        InputMode::Paths
                    },
                    String::new(),
                );
                app.input.insert(&c.to_string());
            }
            app.selected_command = 0;
        }
        _ => {}
    }
    Vec::new()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::settings::isolated_config_dir;

    fn press(app: &mut App, code: KeyCode) -> Vec<Value> {
        handle_key(app, KeyEvent::new(code, KeyModifiers::NONE))
    }

    #[test]
    fn escape_cancels_pending_inputs_and_ignores_late_responses() {
        let mut app = crate::test_support::ready_app();
        app.submit_paths("/slow".into());
        let id = app.pending_inputs[0].id;
        app.take_outbox();
        press(&mut app, KeyCode::Esc);
        assert!(app.pending_inputs.is_empty());
        assert_eq!(
            app.take_outbox(),
            vec![serde_json::json!({"type":"cancel_inputs", "request_id":id})]
        );
        app.handle_message(
            serde_json::json!({"type":"inputs_resolved", "request_id":id,
            "files":["/slow/a.wav"], "duplicates":[], "errors":[], "cancelled":false}),
        );
        assert!(app.queue.items.is_empty());
        assert!(!app.exit_requested);
    }

    #[test]
    fn cleared_argument_editor_still_submits_to_its_command() {
        let _config = isolated_config_dir();
        let mut app = crate::test_support::ready_app();
        dispatch(&mut app, Action::EditCommand("/llm-prompt"));
        handle_key(
            &mut app,
            KeyEvent::new(KeyCode::Char('u'), KeyModifiers::CONTROL),
        );
        crate::commands::paste_input(&mut app, "/tmp/recording.wav");
        press(&mut app, KeyCode::Enter);
        assert!(app.pending_inputs.is_empty());
        assert!(app.queue.items.is_empty());
        assert_eq!(app.llm_prompt, "/tmp/recording.wav");
    }

    #[test]
    fn running_queue_allows_selection_and_inspection_but_not_reordering() {
        let mut app = crate::test_support::ready_app();
        app.queue.add("/first.wav".into());
        app.queue.add("/second.wav".into());
        app.queue.select(0);
        app.activity = crate::lifecycle::Activity::Running(crate::lifecycle::JobKind::Asr);
        press(&mut app, KeyCode::Down);
        assert_eq!(app.queue.selected_index(), Some(1));
        handle_key(&mut app, KeyEvent::new(KeyCode::Up, KeyModifiers::CONTROL));
        assert_eq!(app.queue.items[0].path, "/first.wav");
        press(&mut app, KeyCode::Enter);
        assert!(app.show_path);
    }

    #[test]
    fn empty_active_editor_is_not_a_shortcut_context() {
        for ch in ['q', 'й', 's', 'ы'] {
            let mut app = crate::test_support::ready_app();
            app.input
                .open(crate::input::InputMode::Paths, String::new());
            app.queue.add("/a.wav".into());
            assert!(press(&mut app, KeyCode::Char(ch)).is_empty());
            assert_eq!(app.input.text(), ch.to_string());
            assert!(!app.exit_requested && !app.running());
        }
    }

    #[test]
    fn undo_key_in_editor_does_not_mutate_queue_or_text() {
        let mut app = crate::test_support::ready_app();
        app.queue.add("/a.wav".into());
        app.queue.remove_selected();
        app.input.open(InputMode::Paths, "/new".into());
        handle_key(
            &mut app,
            KeyEvent::new(KeyCode::Char('z'), KeyModifiers::CONTROL),
        );
        assert_eq!(app.input.text(), "/new");
        assert!(app.queue.items.is_empty());
    }

    #[test]
    fn retry_has_identical_mouse_keyboard_and_command_results() {
        let mut outcomes = Vec::new();
        for route in 0..3 {
            let mut app = crate::test_support::ready_app();
            app.queue.add("/done.wav".into());
            app.queue.items[0].state = crate::queue::FileState::Done;
            app.queue.add("/failed.wav".into());
            app.queue.items[1].state = crate::queue::FileState::Failed;
            app.result_files.push("/done.txt".into());
            match route {
                0 => {
                    let mut terminal =
                        ratatui::Terminal::new(ratatui::backend::TestBackend::new(100, 30))
                            .unwrap();
                    terminal.draw(|f| crate::ui::draw(f, &mut app)).unwrap();
                    let rect = app
                        .hits
                        .items()
                        .iter()
                        .find(|(_, a)| *a == Action::QueueActions)
                        .unwrap()
                        .0;
                    let action = app.hits.hit(rect.x, rect.y).unwrap();
                    dispatch(&mut app, action);
                    terminal.draw(|f| crate::ui::draw(f, &mut app)).unwrap();
                    let rect = app
                        .hits
                        .items()
                        .iter()
                        .find(|(_, a)| *a == Action::MenuItem(1))
                        .unwrap()
                        .0;
                    let action = app.hits.hit(rect.x, rect.y).unwrap();
                    dispatch(&mut app, action);
                }
                1 => {
                    press(&mut app, KeyCode::Enter);
                    press(&mut app, KeyCode::Char('2'));
                }
                _ => {
                    app.input.replace("/retry".into());
                    run_command(&mut app);
                }
            }
            let payload = app.take_outbox();
            assert_eq!(payload[0]["files"], serde_json::json!(["/failed.wav"]));
            outcomes.push((payload, app.queue.selected, app.result_files));
        }
        assert_eq!(outcomes[0], outcomes[1]);
        assert_eq!(outcomes[1], outcomes[2]);
    }

    #[test]
    fn editor_arrows_and_delete_change_text_not_queue_or_focus() {
        let mut app = crate::test_support::ready_app();
        app.queue.add("/a.wav".into());
        app.input
            .open(crate::input::InputMode::Paths, "а🦄б".into());
        app.focus = Focus::Input;
        press(&mut app, KeyCode::Left);
        press(&mut app, KeyCode::Backspace);
        press(&mut app, KeyCode::Home);
        press(&mut app, KeyCode::Delete);
        assert_eq!(app.input.text(), "б");
        assert_eq!(app.queue.items.len(), 1);
        assert_eq!(app.focus, Focus::Input);
        handle_key(
            &mut app,
            KeyEvent::new(KeyCode::Char('u'), KeyModifiers::CONTROL),
        );
        assert_eq!(app.input.text(), "");
        assert_eq!(app.input.mode, crate::input::InputMode::Paths);
        press(&mut app, KeyCode::Esc);
        assert_eq!(app.input.mode, crate::input::InputMode::Hidden);
        press(&mut app, KeyCode::Right);
        assert_eq!(app.focus, Focus::Params);
    }

    #[test]
    fn cyrillic_and_function_shortcuts_match_latin_actions() {
        let _config = isolated_config_dir();
        for (latin, cyrillic, function) in [
            ('q', 'й', 10),
            ('s', 'ы', 5),
            ('d', 'в', 7),
            ('f', 'а', 8),
            ('r', 'к', 9),
            ('L', 'Д', 6),
        ] {
            let run = |code| {
                let mut app = crate::test_support::ready_app();
                app.queue.add("/tmp/запись.wav".into());
                app.result_files.push("/tmp/запись.txt".into());
                app.llm_results.push(("summary".into(), "Ответ".into()));
                let commands = press(&mut app, code);
                (
                    commands,
                    app.exit_requested,
                    app.running(),
                    app.diarization,
                    app.formats,
                    app.page,
                    app.input,
                    app.activity.is_llm(),
                )
            };
            let expected = run(KeyCode::Char(latin));
            assert_eq!(run(KeyCode::Char(cyrillic)), expected, "{cyrillic}");
            assert_eq!(run(KeyCode::F(function)), expected, "F{function}");
        }
    }

    #[test]
    fn shortcut_aliases_never_transliterate_typed_paths_or_commands() {
        let _config = isolated_config_dir();
        for prefix in ["/tmp/", "/prompt "] {
            let mut app = crate::test_support::ready_app();
            app.input.replace(prefix.into());
            for c in "йыівакдДQSDР — запись.mp3".chars() {
                press(&mut app, KeyCode::Char(c));
            }
            assert_eq!(
                app.input.text(),
                format!("{prefix}йыівакдДQSDР — запись.mp3")
            );
            assert!(!app.exit_requested);
        }
        for c in ['ы', 'і', 'к', 'д'] {
            let mut app = crate::test_support::ready_app();
            press(&mut app, KeyCode::Char(c));
            assert_eq!(
                app.input.text(),
                c.to_string(),
                "inactive shortcut must remain text"
            );
        }
    }

    #[test]
    fn cyrillic_control_shortcuts_and_layout_free_help_work() {
        let _config = isolated_config_dir();
        let mut app = crate::test_support::ready_app();
        app.log("test");
        handle_key(
            &mut app,
            KeyEvent::new(KeyCode::Char('д'), KeyModifiers::CONTROL),
        );
        assert!(app.logs.is_empty());
        app.log("test");
        press(&mut app, KeyCode::F(11));
        assert!(app.logs.is_empty());
        press(&mut app, KeyCode::F(12));
        assert!(app.help_open);
        press(&mut app, KeyCode::F(12));
        assert!(!app.help_open);
        press(&mut app, KeyCode::F(12));
        let cancel = KeyEvent::new(KeyCode::Char('с'), KeyModifiers::CONTROL);
        handle_key(&mut app, cancel);
        assert!(!app.exit_requested);
        handle_key(&mut app, cancel);
        assert!(app.exit_requested);
    }

    #[test]
    fn tabs_cycle_and_function_keys_jump() {
        let mut app = crate::test_support::ready_app();
        press(&mut app, KeyCode::Tab);
        assert_eq!(app.page, Page::Llm);
        press(&mut app, KeyCode::BackTab);
        assert_eq!(app.page, Page::Processing);
        press(&mut app, KeyCode::BackTab);
        assert_eq!(app.page, Page::Log, "Shift+Tab wraps around");
        press(&mut app, KeyCode::F(3));
        assert_eq!(app.page, Page::Settings);
        app.activity = crate::lifecycle::Activity::Running(crate::lifecycle::JobKind::Asr);
        press(&mut app, KeyCode::F(1));
        assert_eq!(app.page, Page::Processing, "tabs work during a run");
        // Tab with text in the input line still completes instead of switching tabs.
        app.activity = crate::lifecycle::Activity::Idle;
        app.input.replace("/back".into());
        press(&mut app, KeyCode::Tab);
        assert_eq!(app.input.text(), "/backend ");
        assert_eq!(app.page, Page::Processing);
    }

    #[test]
    fn arrows_move_focus_and_enter_opens_the_parameter_menu() {
        let _config = isolated_config_dir();
        let mut app = crate::test_support::ready_app();
        press(&mut app, KeyCode::Right);
        assert_eq!(app.focus, Focus::Params);
        press(&mut app, KeyCode::Down);
        assert_eq!(
            app.params_cursor, 1,
            "Down moves the panel cursor, not the queue"
        );
        press(&mut app, KeyCode::Up);
        press(&mut app, KeyCode::Up);
        assert_eq!(app.params_cursor, PARAM_ROWS.len() - 1, "Up wraps");
        app.params_cursor = 0;
        press(&mut app, KeyCode::Enter);
        assert_eq!(app.command_menu.as_deref(), Some("/backend"));
        press(&mut app, KeyCode::Esc);
        assert_eq!(app.command_menu, None, "the first Esc closes the menu");
        assert_eq!(app.focus, Focus::Params);
        press(&mut app, KeyCode::Esc);
        assert_eq!(
            app.focus,
            Focus::Queue,
            "the next Esc leaves the panel without opening an editor"
        );
        assert!(!app.exit_requested);
        press(&mut app, KeyCode::Left);
        assert_eq!(app.focus, Focus::Queue);
        press(&mut app, KeyCode::Char('x'));
        assert_eq!(app.focus, Focus::Input, "typing returns to the input line");
    }

    #[test]
    fn r_opens_saved_results_and_f2_opens_the_llm_answer() {
        let _config = isolated_config_dir();
        let mut app = crate::test_support::ready_app();
        press(&mut app, KeyCode::Char('r'));
        assert_eq!(
            app.input.text(),
            "r",
            "without a result `r` is ordinary text"
        );
        app.input.close();
        app.llm_results.push(("summary".into(), "…".into()));
        app.llm_saved_files.push("/summary.txt".into());
        press(&mut app, KeyCode::Char('r'));
        assert!(app.results_open);
        press(&mut app, KeyCode::Esc);
        assert!(!app.results_open && !app.exit_requested);
        press(&mut app, KeyCode::F(2));
        assert_eq!(app.page, Page::Llm);
        press(&mut app, KeyCode::PageDown);
        assert_eq!(
            app.scroll[&crate::ui::AreaId::LlmOutput],
            ANSWER_PAGE as u16
        );
        press(&mut app, KeyCode::PageUp);
        assert_eq!(app.scroll[&crate::ui::AreaId::LlmOutput], 0);

        // Delete on the LLM page acts on the transcript list, not the queue.
        app.queue.add("/tmp/a.wav".into());
        app.llm_extra_files = vec!["/tmp/b.txt".into()];
        press(&mut app, KeyCode::Delete);
        assert_eq!(app.queue.items.len(), 1);
        assert_eq!(app.queue.items[0].path, "/tmp/a.wav");
        assert!(app.llm_extra_files.is_empty());
    }

    #[test]
    fn llm_page_cursor_walks_transcripts_then_modes_and_space_toggles() {
        let _config = isolated_config_dir();
        let mut app = crate::test_support::ready_app();
        app.page = Page::Llm;
        app.result_files = vec!["/tmp/a.txt".into(), "/tmp/b.txt".into()];
        press(&mut app, KeyCode::Right);
        assert_eq!(
            app.focus,
            Focus::Input,
            "Left/Right belong to the Processing page"
        );
        press(&mut app, KeyCode::Down);
        assert_eq!((app.llm_input_cursor, app.llm_mode_cursor), (1, None));
        press(&mut app, KeyCode::Down);
        assert_eq!(
            app.llm_mode_cursor,
            Some(0),
            "past the last transcript come the modes"
        );
        press(&mut app, KeyCode::Down);
        assert_eq!(app.llm_mode_cursor, Some(1));
        press(&mut app, KeyCode::Char(' '));
        assert_eq!(app.llm_modes, vec!["summary", "tasks"]);
        assert!(app.input.is_empty(), "Space is not typed");
        press(&mut app, KeyCode::Delete);
        assert_eq!(
            app.result_files.len(),
            2,
            "Delete is for the transcripts only"
        );
        press(&mut app, KeyCode::Up);
        press(&mut app, KeyCode::Up);
        assert_eq!((app.llm_input_cursor, app.llm_mode_cursor), (1, None));
        press(&mut app, KeyCode::Enter);
        assert_eq!(
            app.command_menu, None,
            "Enter does not open a Processing menu"
        );
        handle_key(
            &mut app,
            KeyEvent::new(KeyCode::Char('l'), KeyModifiers::CONTROL),
        );
        assert!(app.logs.is_empty(), "Ctrl+L clears the log from any page");
    }

    #[test]
    fn settings_page_keys_move_the_cursor_and_enter_acts() {
        let _config = isolated_config_dir();
        let mut app = crate::test_support::ready_app();
        app.queue.add("/tmp/a.wav".into());
        press(&mut app, KeyCode::F(3));
        press(&mut app, KeyCode::Down);
        assert_eq!(app.settings_cursor, 1);
        assert_eq!(
            app.queue.selected_index(),
            Some(0),
            "the queue cursor is untouched"
        );
        press(&mut app, KeyCode::Enter);
        assert!(!app.mouse_enabled, "row 1 is the mouse toggle");
        press(&mut app, KeyCode::Delete);
        assert_eq!(
            app.queue.items.len(),
            1,
            "Delete belongs to the Processing page"
        );
        press(&mut app, KeyCode::Up);
        press(&mut app, KeyCode::Up);
        assert_eq!(app.settings_cursor, 0, "Up clamps at the first row");
        press(&mut app, KeyCode::Enter);
        assert_eq!(app.command_menu.as_deref(), Some("/lang"));
    }

    #[test]
    fn help_opens_during_a_run_and_l_is_plain_text_without_results() {
        let _config = isolated_config_dir();
        let mut app = crate::test_support::ready_app();
        app.activity = crate::lifecycle::Activity::Running(crate::lifecycle::JobKind::Asr);
        press(&mut app, KeyCode::Char('?'));
        assert!(
            app.help_open,
            "the help is read-only, so a run must not gate it"
        );
        press(&mut app, KeyCode::Esc);
        assert!(!app.help_open);
        app.activity = crate::lifecycle::Activity::Idle;
        press(&mut app, KeyCode::Char('l'));
        assert_eq!(
            app.input.text(),
            "l",
            "without results to summarise `l` is ordinary text"
        );
    }

    #[test]
    fn ctrl_c_quits_through_the_open_help() {
        let _config = isolated_config_dir();
        let mut app = crate::test_support::ready_app();
        press(&mut app, KeyCode::Char('?'));
        assert!(app.help_open);
        let ctrl_c = KeyEvent::new(KeyCode::Char('c'), KeyModifiers::CONTROL);
        handle_key(&mut app, ctrl_c);
        assert!(!app.exit_requested, "the first Ctrl+C only asks");
        assert!(app.status.contains("Ctrl+C"), "{}", app.status);
        handle_key(&mut app, ctrl_c);
        assert!(
            app.exit_requested,
            "the second Ctrl+C quits even with the help open"
        );
    }

    #[test]
    fn q_and_s_keep_their_meaning() {
        let _config = isolated_config_dir();
        let mut app = crate::test_support::ready_app();
        press(&mut app, KeyCode::Char('s'));
        assert_eq!(
            app.input.text(),
            "s",
            "with an empty queue `s` is ordinary text"
        );
        app.input.close();
        app.queue.add("/tmp/a.wav".into());
        let commands = press(&mut app, KeyCode::Char('s'));
        assert_eq!(commands[0]["type"], "start");
        press(&mut app, KeyCode::Char('q'));
        assert!(
            !app.exit_requested,
            "a starting batch is already protected from accidental quit"
        );
        app.handle_message(serde_json::json!({"type":"completed", "success":true}));
        press(&mut app, KeyCode::Char('q'));
        assert!(app.exit_requested);
    }
}
