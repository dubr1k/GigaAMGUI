//! Keyboard handling: every key press becomes state changes on [`App`] and, for the
//! keys that start or stop work, the commands the caller must send to the worker.
//! The function never touches the terminal or the worker process, so the tests
//! drive it with synthetic [`KeyEvent`]s.
//!
//! The one key that is not here is `Esc` during a run: its second press kills and
//! respawns the worker, which needs the child handle that only `main` owns.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use serde_json::Value;

use crate::{
    app::{dispatch, esc_should_soft_cancel, llm_can_run, on_off, App, Focus, Page},
    commands::{
        apply_command_menu, command_menu_options, command_suggestions, complete_path, is_command,
        open_command_menu, queue_paths, remove_selected_file, run_command, COMMANDS,
    },
    i18n::{t, tf},
    settings::save_app_settings,
    ui::{llm::MODES, processing::PARAM_ROWS, Action, AreaId, ButtonId},
};

/// Lines `PgUp` / `PgDn` move the LLM answer, the log and the help by.
const ANSWER_PAGE: i32 = 10;

pub(crate) fn handle_key(app: &mut App, key: KeyEvent) -> Vec<Value> {
    let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
    // The help overlay is modal: it scrolls, closes, and swallows everything else
    // except Ctrl+C, which must always reach the quit path.
    if app.help_open && !(ctrl && key.code == KeyCode::Char('c')) {
        return match key.code {
            KeyCode::Esc | KeyCode::Char('?') => dispatch(app, Action::Help),
            KeyCode::Up => dispatch(app, Action::Scroll(AreaId::Help, -1)),
            KeyCode::Down => dispatch(app, Action::Scroll(AreaId::Help, 1)),
            KeyCode::PageUp => dispatch(app, Action::Scroll(AreaId::Help, -ANSWER_PAGE)),
            KeyCode::PageDown => dispatch(app, Action::Scroll(AreaId::Help, ANSWER_PAGE)),
            _ => Vec::new(),
        };
    }
    let idle = !app.running;
    let no_input = app.input.is_empty();
    let menu_open = app.command_menu.is_some();
    let on_page = |page: Page| app.page == page && no_input && !menu_open;
    match key.code {
        KeyCode::Char('l') if ctrl => return dispatch(app, Action::Button(ButtonId::ClearLog)),
        KeyCode::Char('c') if idle && key.modifiers.contains(KeyModifiers::CONTROL) => {
            app.request_exit("ctrl-c", "Ctrl+C");
        }
        KeyCode::Char('q') if idle && no_input => app.exit_requested = true,
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
        KeyCode::Char('L') if idle && no_input => {
            return dispatch(app, Action::Button(ButtonId::RunLlm));
        }
        KeyCode::Char('l') if idle && no_input && llm_can_run(app) => {
            return dispatch(app, Action::Button(ButtonId::RunLlm));
        }
        KeyCode::Char('r') if idle && no_input && !app.llm_results.is_empty() => {
            return dispatch(app, Action::Tab(Page::Llm));
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
        KeyCode::Char('d') if idle && no_input => {
            app.diarization = !app.diarization;
            app.log(tf(
                app.lang,
                "status.diarization",
                &[("value", on_off(app.lang, app.diarization))],
            ));
            save_app_settings(app);
        }
        KeyCode::Char('f') if idle && no_input => {
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
        KeyCode::Char('s') if idle && no_input && !app.files.is_empty() => {
            return dispatch(app, Action::Button(ButtonId::Start));
        }
        // Reachable during a run too: the overlay only reads, it never touches the worker.
        KeyCode::Char('?') if no_input => return dispatch(app, Action::Help),
        KeyCode::Esc if esc_should_soft_cancel(app) => {
            return dispatch(app, Action::Button(ButtonId::CancelLlm));
        }
        KeyCode::Esc if menu_open => {
            app.command_menu = None;
            app.input.clear();
            app.status = t(app.lang, "status.menu_closed").into();
        }
        KeyCode::Esc if idle && app.focus != Focus::Input => {
            return dispatch(app, Action::FocusInput);
        }
        KeyCode::Esc if no_input => app.request_exit("esc", "Esc"),
        KeyCode::Esc => {
            app.input.clear();
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
        KeyCode::Enter if idle => {
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
            if !suggestions.is_empty() {
                let index = app.selected_command.min(suggestions.len() - 1);
                app.input = format!("{} ", suggestions[index].0);
                app.selected_command = 0;
            } else if let Some(path) = complete_path(&app.input) {
                app.input = path;
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
        KeyCode::Up if idle && on_page(Page::Processing) => {
            if ctrl {
                if let Some(index) = app.selected_file.filter(|index| *index > 0) {
                    app.files.swap(index, index - 1);
                    app.selected_file = Some(index - 1);
                }
            } else if !app.files.is_empty() {
                let index = app.selected_file.unwrap_or(0).saturating_sub(1);
                return dispatch(app, Action::SelectFile(index));
            }
        }
        KeyCode::Down if idle && on_page(Page::Processing) => {
            if ctrl {
                if let Some(index) = app
                    .selected_file
                    .filter(|index| *index + 1 < app.files.len())
                {
                    app.files.swap(index, index + 1);
                    app.selected_file = Some(index + 1);
                }
            } else if !app.files.is_empty() {
                let index = (app.selected_file.unwrap_or(0) + 1).min(app.files.len() - 1);
                return dispatch(app, Action::SelectFile(index));
            }
        }
        KeyCode::Delete | KeyCode::Backspace
            if idle && on_page(Page::Processing) && app.focus != Focus::Params =>
        {
            match app.selected_file {
                Some(index) => return dispatch(app, Action::RemoveFile(index)),
                None => remove_selected_file(app),
            }
        }
        KeyCode::Backspace if idle => {
            app.input.pop();
        }
        KeyCode::Char(c) if idle => {
            app.command_menu = None;
            app.focus = Focus::Input;
            app.input.push(c);
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
    fn tabs_cycle_and_function_keys_jump() {
        let mut app = App::default();
        press(&mut app, KeyCode::Tab);
        assert_eq!(app.page, Page::Llm);
        press(&mut app, KeyCode::BackTab);
        assert_eq!(app.page, Page::Processing);
        press(&mut app, KeyCode::BackTab);
        assert_eq!(app.page, Page::Log, "Shift+Tab wraps around");
        press(&mut app, KeyCode::F(3));
        assert_eq!(app.page, Page::Settings);
        app.running = true;
        press(&mut app, KeyCode::F(1));
        assert_eq!(app.page, Page::Processing, "tabs work during a run");
        // Tab with text in the input line still completes instead of switching tabs.
        app.running = false;
        app.input = "/back".into();
        press(&mut app, KeyCode::Tab);
        assert_eq!(app.input, "/backend ");
        assert_eq!(app.page, Page::Processing);
    }

    #[test]
    fn arrows_move_focus_and_enter_opens_the_parameter_menu() {
        let _config = isolated_config_dir();
        let mut app = App::default();
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
        assert_eq!(app.focus, Focus::Input, "the next Esc leaves the panel");
        assert!(!app.exit_requested);
        press(&mut app, KeyCode::Left);
        assert_eq!(app.focus, Focus::Queue);
        press(&mut app, KeyCode::Char('x'));
        assert_eq!(app.focus, Focus::Input, "typing returns to the input line");
    }

    #[test]
    fn r_opens_the_llm_page_and_page_keys_scroll_the_answer() {
        let _config = isolated_config_dir();
        let mut app = App::default();
        press(&mut app, KeyCode::Char('r'));
        assert_eq!(app.input, "r", "without a result `r` is ordinary text");
        app.input.clear();
        app.llm_results.push(("summary".into(), "…".into()));
        press(&mut app, KeyCode::Char('r'));
        assert_eq!(app.page, Page::Llm);
        press(&mut app, KeyCode::PageDown);
        assert_eq!(
            app.scroll[&crate::ui::AreaId::LlmOutput],
            ANSWER_PAGE as u16
        );
        press(&mut app, KeyCode::PageUp);
        assert_eq!(app.scroll[&crate::ui::AreaId::LlmOutput], 0);

        // Delete on the LLM page acts on the transcript list, not the queue.
        app.files = vec!["/tmp/a.wav".into()];
        app.selected_file = Some(0);
        app.llm_extra_files = vec!["/tmp/b.txt".into()];
        press(&mut app, KeyCode::Delete);
        assert_eq!(app.files, vec!["/tmp/a.wav"]);
        assert!(app.llm_extra_files.is_empty());
    }

    #[test]
    fn llm_page_cursor_walks_transcripts_then_modes_and_space_toggles() {
        let _config = isolated_config_dir();
        let mut app = App::default();
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
        let mut app = App::default();
        app.files = vec!["/tmp/a.wav".into()];
        app.selected_file = Some(0);
        press(&mut app, KeyCode::F(3));
        press(&mut app, KeyCode::Down);
        assert_eq!(app.settings_cursor, 1);
        assert_eq!(app.selected_file, Some(0), "the queue cursor is untouched");
        press(&mut app, KeyCode::Enter);
        assert!(!app.mouse_enabled, "row 1 is the mouse toggle");
        press(&mut app, KeyCode::Delete);
        assert_eq!(app.files.len(), 1, "Delete belongs to the Processing page");
        press(&mut app, KeyCode::Up);
        press(&mut app, KeyCode::Up);
        assert_eq!(app.settings_cursor, 0, "Up clamps at the first row");
        press(&mut app, KeyCode::Enter);
        assert_eq!(app.command_menu.as_deref(), Some("/lang"));
    }

    #[test]
    fn help_opens_during_a_run_and_l_is_plain_text_without_results() {
        let _config = isolated_config_dir();
        let mut app = App::default();
        app.running = true;
        press(&mut app, KeyCode::Char('?'));
        assert!(
            app.help_open,
            "the help is read-only, so a run must not gate it"
        );
        press(&mut app, KeyCode::Esc);
        assert!(!app.help_open);
        app.running = false;
        press(&mut app, KeyCode::Char('l'));
        assert_eq!(
            app.input, "l",
            "without results to summarise `l` is ordinary text"
        );
    }

    #[test]
    fn ctrl_c_quits_through_the_open_help() {
        let _config = isolated_config_dir();
        let mut app = App::default();
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
        let mut app = App::default();
        press(&mut app, KeyCode::Char('s'));
        assert_eq!(app.input, "s", "with an empty queue `s` is ordinary text");
        app.input.clear();
        app.files.push("/tmp/a.wav".into());
        let commands = press(&mut app, KeyCode::Char('s'));
        assert_eq!(commands[0]["type"], "start");
        press(&mut app, KeyCode::Char('q'));
        assert!(app.exit_requested);
    }
}
