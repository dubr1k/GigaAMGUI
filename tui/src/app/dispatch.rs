//! Actions from keys and clicks, and the decisions shared with them.

use std::{collections::HashSet, path::Path};

use serde_json::{json, Value};

use crate::{
    action::{Action, AreaId, ButtonId},
    commands::{
        accept_command_suggestion, apply_command_menu, clear_queue, command_menu_options,
        command_suggestions, is_command, open_command_menu, remove_selected_file, short_name,
        toggle_pets,
    },
    i18n::{t, tf, tn, Lang},
    lifecycle::{Activity, ConnectionState, JobKind},
    options::LLM_MODES,
    queue::RunSelection,
    requests::llm_start_payload,
    setting::Setting,
    settings_page::rows as setting_rows,
};

use super::{App, FileState, Focus, Page};

/// «вкл» / «выкл» (or `on` / `off`) for the status messages of the toggles.
pub(crate) fn on_off(lang: Lang, value: bool) -> &'static str {
    t(lang, if value { "value.on" } else { "value.off" })
}

pub(crate) fn llm_input_files(app: &App) -> Vec<String> {
    let mut seen = HashSet::new();
    app.result_files
        .iter()
        .chain(app.llm_extra_files.iter())
        .filter(|path| {
            matches!(
                Path::new(path).extension().and_then(|value| value.to_str()),
                Some("txt" | "md" | "srt" | "vtt")
            )
        })
        .filter(|path| seen.insert((*path).clone()))
        .cloned()
        .collect()
}

pub(crate) fn llm_can_run(app: &App) -> bool {
    !app.worker_down()
        && !app.running()
        && !llm_input_files(app).is_empty()
        && !app.llm_modes.is_empty()
        && (!app.llm_modes.iter().any(|mode| mode == "custom") || !app.llm_prompt.is_empty())
}

pub(crate) fn request_llm(app: &mut App) -> Vec<Value> {
    if app.worker_down() || app.running() {
        return Vec::new();
    }
    if llm_input_files(app).is_empty() {
        app.status = t(app.lang, "llm.inputs_empty").into();
    } else if app.llm_modes.is_empty() {
        app.status = t(app.lang, "status.llm_no_mode").into();
    } else if app.llm_modes.iter().any(|mode| mode == "custom") && app.llm_prompt.is_empty() {
        app.status = t(app.lang, "status.llm_no_prompt").into();
    } else {
        app.activity.start(JobKind::Llm);
        app.cancelled = false;
        app.status = tf(
            app.lang,
            "status.llm_starting",
            &[(
                "transcripts",
                &tn(app.lang, llm_input_files(app).len(), "plural.transcripts"),
            )],
        );
        return vec![llm_start_payload(app)];
    }
    Vec::new()
}

/// First Esc requests cooperative cancellation; later presses open confirmation.
pub(crate) fn esc_should_soft_cancel(app: &App) -> bool {
    app.llm_running() && !app.activity.is_stopping()
}

/// Help/path overlays consume Esc before cancellation or force-stop confirmation.
pub(crate) fn esc_is_cancel(app: &App) -> bool {
    app.running()
        && !app.help_open
        && !app.show_path
        && !app.stop_confirmation
        && !esc_should_soft_cancel(app)
}

/// The one-line hint under the main area, as an i18n key. The first matching
/// situation wins: a dead worker outranks everything, then the two kinds of run,
/// then the furthest stage the session has reached.
pub(crate) fn next_step(app: &App) -> &'static str {
    if app.connection.state == ConnectionState::Connecting {
        "hint.worker_connecting"
    } else if app.worker_down() {
        "hint.worker_down"
    } else if app.llm_running() {
        "hint.cancel_llm"
    } else if !app.pending_inputs.is_empty() {
        "hint.adding_inputs"
    } else if app.activity == Activity::Starting(JobKind::Asr) {
        "hint.batch_starting"
    } else if app.running() {
        "hint.cancel_batch"
    } else if app.can_start(RunSelection::Pending) {
        "hint.start"
    } else if !app.llm_results.is_empty() {
        "hint.view_result"
    } else if !app.result_files.is_empty() {
        "hint.run_llm"
    } else if !app.queue.items.is_empty() {
        "hint.queue_actions"
    } else {
        "hint.add_files"
    }
}

/// After the worker is killed and respawned nothing it was doing survives, so every
/// "in flight" flag and buffer of both the transcription and the LLM run must go back to
/// idle; the fresh worker will never send the `completed`/`llm_completed` that would.
pub(crate) fn reset_after_worker_restart(app: &mut App) {
    app.batch = None;
    app.rerun_confirmation = None;
    app.failed_inputs
        .extend(app.pending_inputs.drain(..).map(|r| r.original));
    app.outbox.clear();
    for item in &mut app.queue.items {
        if item.state == FileState::Processing {
            item.state = FileState::Cancelled;
        }
    }
    app.activity = Activity::Idle;
    app.cancelled = true;
    app.llm_stream.clear();
}

/// The one place where an action changes state, whether it came from a key or a click.
/// Returns the commands the caller must send to the worker; `dispatch` itself never
/// writes to the process, so the tests need no worker and `main` keeps the only
/// handle that can respawn it.
pub(crate) fn dispatch(app: &mut App, action: Action) -> Vec<Value> {
    if app.stop_confirmation && !matches!(action, Action::ConfirmStop(_)) {
        return Vec::new();
    }
    if app.rerun_confirmation.is_some() && !matches!(action, Action::ConfirmRerun(_)) {
        return Vec::new();
    }
    if app.results_open
        && !matches!(
            action,
            Action::ShowResults(_)
                | Action::SelectResult(_)
                | Action::OpenResult(_)
                | Action::Scroll(AreaId::Results | AreaId::ResultPath, _)
        )
    {
        return Vec::new();
    }
    // During a run the queue is read-only; selection and inspection stay available.
    let edits_idle_state = matches!(
        action,
        Action::RemoveFile(_)
            | Action::UndoRemove
            | Action::AddFiles
            | Action::RetryInputs
            | Action::OpenMenu(_)
            | Action::MenuItem(_)
            | Action::Suggestion(_)
            | Action::ToggleMode(_)
            | Action::RemoveLlmInput(_)
            | Action::EditCommand(_)
            | Action::SettingsRow(_)
            | Action::ToggleSetting(_)
    );
    if edits_idle_state && app.running() {
        return Vec::new();
    }
    match action {
        Action::ShowResults(show) => {
            app.results_open = show;
            app.result_cursor = app
                .result_cursor
                .min(app.saved_results().len().saturating_sub(1));
            if show {
                app.result_notice.clear();
            }
        }
        Action::SelectResult(index) => {
            app.result_cursor = index.min(app.saved_results().len().saturating_sub(1));
            app.scroll.insert(AreaId::ResultPath, 0);
        }
        Action::OpenResult(folder) => app.open_selected_result(folder),
        Action::Scroll(AreaId::Results, delta) => {
            let index = app.result_cursor.saturating_add_signed(delta as isize);
            return dispatch(app, Action::SelectResult(index));
        }
        Action::Reconnect => app.request_reconnect(),
        Action::ForceStop => {
            app.stop_confirmation = true;
        }
        Action::ConfirmStop(confirmed) => {
            app.stop_confirmation = false;
            if confirmed {
                app.worker_failed(t(app.lang, "status.force_stopping").into());
                app.worker_stop_requested = true;
                app.reconnect_requested = true;
            }
        }
        Action::QueueActions => {
            if app.running() {
                return dispatch(app, Action::ShowPath(true));
            }
            open_command_menu(app, "/queue-actions");
        }
        Action::ShowPath(show) => {
            app.show_path = show && app.queue.selected_index().is_some();
            app.scroll.insert(AreaId::Path, 0);
        }
        Action::Run(selection) => return app.begin_batch(selection, false),
        Action::ConfirmRerun(confirmed) => {
            let id = app.rerun_confirmation.take();
            if confirmed && id.is_some() && id == app.queue.selected {
                return app.begin_batch(RunSelection::Selected, true);
            }
        }
        Action::UndoRemove => {
            if app.queue.undo_remove() {
                app.status = t(app.lang, "queue.restored").into();
            }
        }
        Action::AddFiles => {
            app.page = Page::Processing;
            app.command_menu = None;
            app.input
                .open(crate::input::InputMode::Paths, String::new());
            app.focus = Focus::Input;
        }
        Action::RetryInputs => {
            if let Some(original) = app.failed_inputs.last().cloned() {
                if app.submit_paths(original) {
                    app.failed_inputs.pop();
                }
            }
        }
        Action::Tab(page) => app.page = page,
        Action::SelectFile(index) => {
            if index < app.queue.items.len() {
                app.queue.select(index);
                app.focus = Focus::Queue;
            }
        }
        Action::RemoveFile(index) => {
            if index < app.queue.items.len() {
                app.queue.select(index);
                remove_selected_file(app);
            }
        }
        Action::OpenMenu(command) => {
            // A parameter without a choice menu (`/output <dir>`) is typed instead:
            // the command line is pre-filled the way the `/settings` menu does it.
            if !open_command_menu(app, command) && is_command(command) {
                app.input.open(
                    crate::input::InputMode::Argument(command),
                    format!("{command} "),
                );
                app.selected_command = 0;
                app.focus = Focus::Input;
            }
        }
        Action::MenuItem(index) => {
            if index < command_menu_options(app).len() {
                app.command_menu_index = index;
                apply_command_menu(app);
            }
        }
        Action::Suggestion(index) => {
            if let Some((command, _)) = command_suggestions(&app.input).get(index) {
                accept_command_suggestion(app, command);
            }
        }
        Action::ToggleMode(mode) => {
            app.llm_mode_cursor = LLM_MODES.iter().position(|(id, _)| *id == mode);
            if let Some(position) = app.llm_modes.iter().position(|item| item == mode) {
                app.llm_modes.remove(position);
            } else {
                app.llm_modes.push(mode.to_owned());
            }
            app.status = tf(
                app.lang,
                "status.llm_modes",
                &[("modes", &app.llm_modes.join(", "))],
            );
        }
        Action::Button(ButtonId::Start) => {
            return dispatch(app, Action::Run(RunSelection::Pending));
        }
        Action::Button(ButtonId::Stop) => {
            // One graceful cancel per batch: the worker answers `cancelling`, which
            // also sets `cancelled`, so a repeated Esc within the double-press
            // window does not queue more cancels behind the first.
            if app.running() && !app.llm_running() && app.activity.stop() {
                app.cancelled = true;
                return vec![json!({"type": "cancel"})];
            }
        }
        Action::Button(ButtonId::RunLlm) => {
            return request_llm(app);
        }
        Action::Button(ButtonId::CancelLlm) => {
            if esc_should_soft_cancel(app) {
                app.activity.stop();
                app.status = t(app.lang, "status.llm_cancelling").into();
                return vec![json!({"type": "llm_cancel"})];
            }
        }
        Action::Button(ButtonId::ClearQueue) => {
            if !app.running() {
                clear_queue(app);
                app.log(app.status.clone());
            }
        }
        Action::FocusInput => {
            app.focus = Focus::Input;
            if app.input.mode == crate::input::InputMode::Hidden {
                app.input
                    .open(crate::input::InputMode::Paths, String::new());
            }
        }
        Action::Button(ButtonId::ClearLog) => app.logs.clear(),
        Action::ToggleLang => Setting::Language(app.lang.toggle()).apply(app),
        Action::Help => app.help_open = !app.help_open,
        // The Settings list is a cursor list: the wheel moves the cursor and the list
        // slides to keep it visible, so wheel and arrows can never fight each other.
        // One tick (any magnitude: the wheel sends ±3 lines) moves exactly one row.
        Action::Scroll(AreaId::Settings, delta) => {
            let last = setting_rows(app).len().saturating_sub(1);
            app.settings_cursor = (app.settings_cursor as i64 + i64::from(delta.signum()))
                .clamp(0, last as i64) as usize;
        }
        Action::Scroll(area, delta) => {
            if area == AreaId::Log && delta < 0 {
                app.log_follow = false;
            }
            let offset = app.scroll.entry(area).or_default();
            *offset = (i64::from(*offset) + i64::from(delta)).clamp(0, i64::from(u16::MAX)) as u16;
        }
        Action::LlmInput(index) => {
            if index < llm_input_files(app).len() {
                app.llm_input_cursor = index;
                app.llm_mode_cursor = None;
            }
        }
        Action::RemoveLlmInput(index) => {
            // Only files added with `/llm-file` can go; session results are the
            // transcription's own output and leave with `/clear`.
            let files = llm_input_files(app);
            let Some(path) = files.get(index) else {
                return Vec::new();
            };
            if app.result_files.contains(path) {
                app.status = t(app.lang, "llm.cannot_remove_session").into();
                return Vec::new();
            }
            app.llm_extra_files.retain(|item| item != path);
            app.llm_input_cursor = index.min(llm_input_files(app).len().saturating_sub(1));
            app.status = tf(app.lang, "llm.removed", &[("name", &short_name(path))]);
        }
        Action::EditCommand(command) => {
            app.command_menu = None;
            app.input.open(
                crate::input::InputMode::Argument(command),
                format!("{command} "),
            );
            app.selected_command = 0;
            app.focus = Focus::Input;
        }
        Action::SettingsRow(index) => {
            let rows = setting_rows(app);
            if let Some(row) = rows.get(index) {
                let action = row.action.clone();
                app.settings_cursor = index;
                return dispatch(app, action);
            }
        }
        Action::ToggleSetting(name) => {
            // Each flip is exactly what the matching command does, status included.
            let setting = match name {
                "mouse" => Setting::Mouse(!app.mouse_enabled),
                "pets" => {
                    toggle_pets(app);
                    return Vec::new();
                }
                "subtitle_split" => Setting::SubtitleSplit(!app.subtitle_sentence_split),
                "llm_tools" => Setting::LlmTools(!app.llm_allow_tools),
                _ => return Vec::new(),
            };
            setting.apply(app);
        }
    }
    Vec::new()
}
