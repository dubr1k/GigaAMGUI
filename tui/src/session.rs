//! Добавление входов и жизненный цикл неизменяемого снимка очереди.

use crate::{
    app::{App, Focus},
    batch::BatchRun,
    i18n::{t, tf},
    input::{input_candidates, InputMode},
    lifecycle::{Activity, ConnectionState, JobKind},
    queue::{FileState, RunSelection},
    requests::start_payload,
};
use serde::Deserialize;
use serde_json::{json, Value};
use std::time::{Duration, Instant};

/// How long a `resolve_inputs` request may stay unanswered. A worker that never
/// replies (an exception in its resolver thread, an older build) used to keep
/// "adding inputs" on screen and Start locked until the user found Esc.
pub(crate) const INPUTS_TIMEOUT: Duration = Duration::from_secs(120);

pub(crate) struct PendingInput {
    pub id: u64,
    pub original: String,
    pub sent_at: Instant,
}

#[derive(Deserialize)]
struct ResolvedInputs {
    files: Vec<String>,
    duplicates: Vec<String>,
    errors: Vec<InputError>,
    cancelled: bool,
}

#[derive(Deserialize)]
struct InputError {
    path: String,
    #[serde(default)]
    code: String,
    message: String,
}

impl App {
    pub(crate) fn submit_paths(&mut self, original: String) -> bool {
        if self.running() || self.worker_down() {
            self.status = t(
                self.lang,
                if self.worker_down() {
                    "status.worker_down"
                } else {
                    "status.running"
                },
            )
            .into();
            return false;
        }
        let paths = input_candidates(&original);
        if paths.is_empty() {
            return false;
        }
        self.next_input_id += 1;
        let id = self.next_input_id;
        self.pending_inputs.push_back(PendingInput {
            id,
            original,
            sent_at: Instant::now(),
        });
        self.outbox
            .push(json!({"type":"resolve_inputs", "request_id":id, "paths":paths}));
        self.status = t(self.lang, "status.adding_inputs").into();
        true
    }

    pub(crate) fn take_outbox(&mut self) -> Vec<Value> {
        std::mem::take(&mut self.outbox)
    }

    pub(crate) fn cancel_inputs(&mut self) {
        let pending = std::mem::take(&mut self.pending_inputs);
        self.outbox
            .retain(|value| value["type"] != "resolve_inputs");
        for request in pending {
            self.outbox
                .push(json!({"type":"cancel_inputs","request_id":request.id}));
        }
    }

    /// Cancels the requests the worker left unanswered for [`INPUTS_TIMEOUT`]; the
    /// input comes back to the editor (or the retry list) with a visible error.
    pub(crate) fn check_pending_inputs(&mut self, now: Instant) {
        let (expired, waiting): (Vec<_>, Vec<_>) = self
            .pending_inputs
            .drain(..)
            .partition(|request| now.saturating_duration_since(request.sent_at) >= INPUTS_TIMEOUT);
        self.pending_inputs = waiting.into();
        if expired.is_empty() {
            return;
        }
        for request in expired {
            self.outbox
                .push(json!({"type":"cancel_inputs","request_id":request.id}));
            self.recover_input(request.original);
        }
        self.status = tf(
            self.lang,
            "status.inputs_timeout",
            &[("seconds", &INPUTS_TIMEOUT.as_secs().to_string())],
        );
        self.log(self.status.clone());
    }

    fn recover_input(&mut self, original: String) {
        if self.input.is_empty() {
            self.input.open(InputMode::Paths, original);
            self.focus = Focus::Input;
        } else {
            self.failed_inputs.push(original);
        }
    }

    pub(crate) fn inputs_resolved(&mut self, value: Value) {
        let Some(id) = value["request_id"].as_u64() else {
            return;
        };
        let Some(index) = self
            .pending_inputs
            .iter()
            .position(|request| request.id == id)
        else {
            return;
        };
        let request = self.pending_inputs.remove(index).unwrap();
        let response = match serde_json::from_value::<ResolvedInputs>(value) {
            Ok(response) if response.files.iter().all(|p| !p.is_empty()) => response,
            _ => {
                self.recover_input(request.original);
                self.status = t(self.lang, "status.invalid_input_response").into();
                self.log(self.status.clone());
                return;
            }
        };
        if response.cancelled {
            return;
        }
        let mut added = 0;
        let mut duplicates = response.duplicates.len();
        for path in response.files {
            if self.queue.add(path) {
                added += 1;
            } else {
                duplicates += 1;
            }
        }
        let errors = response.errors.len();
        for error in response.errors {
            let key = match error.code.as_str() {
                "missing" => "input.error_missing",
                "unreadable" => "input.error_unreadable",
                "unsupported" => "input.error_unsupported",
                "empty_directory" => "input.error_empty",
                "invalid_request" => "input.error_invalid",
                "undecodable" => "input.error_undecodable",
                "too_many_files" => "input.error_too_many",
                "more_errors" => "input.error_more",
                "internal" => "input.error_internal",
                _ => "input.error_unknown",
            };
            let line = format!("{} — {}", t(self.lang, key), error.message);
            // Request-wide errors (too many files, …) carry no path.
            self.log(if error.path.is_empty() {
                line
            } else {
                format!("{}: {line}", error.path)
            });
        }
        self.status = tf(
            self.lang,
            "status.queued_report",
            &[
                ("added", &added.to_string()),
                ("duplicates", &duplicates.to_string()),
                ("errors", &errors.to_string()),
            ],
        );
        self.log(self.status.clone());
        if added == 0 && duplicates == 0 {
            self.recover_input(request.original);
        } else if self.input.mode == InputMode::Hidden {
            self.focus = Focus::Queue;
        }
    }

    pub(crate) fn can_start(&self, selection: RunSelection) -> bool {
        !self.running()
            && !self.worker_down()
            && self.pending_inputs.is_empty()
            && !self.queue.paths(selection).is_empty()
    }

    pub(crate) fn begin_batch(&mut self, selection: RunSelection, confirmed: bool) -> Vec<Value> {
        if !self.can_start(selection) {
            return Vec::new();
        }
        let files = self.queue.paths(selection);
        if selection == RunSelection::Selected && !confirmed {
            if let Some(item) = self
                .queue
                .items
                .iter()
                .find(|item| Some(item.id) == self.queue.selected)
            {
                if item.state == FileState::Done || !item.results.is_empty() {
                    self.rerun_confirmation = Some(item.id);
                    self.status = t(self.lang, "status.confirm_rerun").into();
                    return Vec::new();
                }
            }
        }
        let payload = start_payload(self, &files);
        self.progress.total_files = files.len();
        self.batch = Some(BatchRun::new(files));
        self.batch_summary = None;
        self.activity.start(JobKind::Asr);
        self.cancelled = false;
        self.progress.current_file = None;
        self.progress.file_index = 0;
        self.progress.fraction = 0.0;
        self.progress.stage = "preparing".into();
        self.progress.processed_seconds = None;
        self.progress.total_seconds = None;
        self.input.close();
        self.status = t(self.lang, "status.batch_starting").into();
        vec![payload]
    }

    pub(crate) fn worker_failed(&mut self, message: String) {
        self.connection.state = ConnectionState::Unavailable;
        self.finish_batch(true, Some(message.clone()), None);
        self.rerun_confirmation = None;
        self.activity = Activity::Idle;
        self.cancelled = true;
        for item in &mut self.queue.items {
            if item.state == FileState::Processing {
                item.state = FileState::Cancelled;
            }
        }
        self.failed_inputs.extend(
            self.pending_inputs
                .drain(..)
                .map(|request| request.original),
        );
        self.outbox.clear();
        self.status = message;
        self.log(self.status.clone());
    }

    pub(crate) fn resolver_unavailable(&mut self) {
        let originals: Vec<_> = self.pending_inputs.drain(..).map(|r| r.original).collect();
        self.outbox
            .retain(|value| value["type"] != "resolve_inputs");
        for original in originals {
            self.recover_input(original);
        }
        self.status = t(self.lang, "status.resolver_unavailable").into();
        self.log(self.status.clone());
    }
}

#[cfg(test)]
mod tests {
    use crate::queue::{FileState, RunSelection};
    use serde_json::json;

    #[test]
    fn batch_progress_counts_attempts_once_and_resets_each_file() {
        let mut app = crate::test_support::ready_app();
        for file in ["/a.wav", "/b.wav"] {
            app.queue.add(file.into());
        }
        app.begin_batch(RunSelection::Pending, false);
        app.handle_message(json!({"type":"started", "total_files":2}));
        app.handle_message(json!({"type":"file_started", "file":"/a.wav", "file_index":0}));
        app.handle_message(json!({"type":"progress", "file":"/a.wav", "stage":"transcription", "file_progress":0.5, "processed_seconds":10, "total_seconds":20}));
        assert_eq!(app.overall_progress(), 0.25);
        let complete = json!({"type":"file_completed", "file":"/a.wav", "result":{"success":true,"saved_files":["/a.txt"]}});
        app.handle_message(complete.clone());
        app.handle_message(complete);
        assert_eq!(app.overall_progress(), 0.5);
        app.handle_message(json!({"type":"file_started", "file":"/b.wav", "file_index":1}));
        assert_eq!(app.progress.processed_seconds, None);
        assert_eq!(app.progress.total_seconds, None);
        assert_eq!(app.progress.stage, "preparing");
        app.handle_message(json!({"type":"progress", "file":"/b.wav", "stage":"transcription", "file_progress":0.5}));
        assert_eq!(app.overall_progress(), 0.75);
        app.handle_message(json!({"type":"file_completed", "file":"/b.wav", "result":{"success":false,"error":"decoder failed"}}));
        app.handle_message(json!({"type":"completed", "success":false, "elapsed_seconds":2.5}));
        let summary = app.batch_summary.as_ref().unwrap();
        assert_eq!(
            (summary.succeeded, summary.failed, summary.unstarted),
            (1, 1, 0)
        );
        assert_eq!(summary.elapsed.as_secs_f64(), 2.5);
        assert!(app.logs.iter().any(|line| line.contains("decoder failed")));
        assert_eq!(app.overall_progress(), 1.0);
    }

    #[test]
    fn cancellation_and_startup_failure_keep_truthful_summary_and_error() {
        let mut app = crate::test_support::ready_app();
        for file in ["/a.wav", "/b.wav"] {
            app.queue.add(file.into());
        }
        app.begin_batch(RunSelection::Pending, false);
        app.handle_message(json!({"type":"started", "total_files":2}));
        app.handle_message(json!({"type":"file_started", "file":"/a.wav"}));
        app.handle_message(json!({"type":"completed", "success":false,"cancelled":true}));
        let summary = app.batch_summary.as_ref().unwrap();
        assert_eq!(
            (
                summary.succeeded,
                summary.failed,
                summary.unstarted,
                summary.interrupted
            ),
            (0, 0, 1, 1)
        );
        assert!(summary.cancelled);
        assert!(app.overall_progress() < 1.0);
        app.begin_batch(RunSelection::Pending, false);
        app.handle_message(json!({"type":"started", "total_files":1}));
        app.handle_message(json!({"type":"error", "message":"model download denied"}));
        app.handle_message(json!({"type":"completed", "success":false}));
        assert_eq!(
            app.batch_summary.as_ref().unwrap().error.as_deref(),
            Some("model download denied")
        );
        assert!(app.status.contains("model download denied"));
        assert_eq!(app.overall_progress(), 0.0);
        assert_eq!(app.batch_summary.as_ref().unwrap().unstarted, 1);
    }

    #[test]
    fn cancelled_input_response_cannot_restore_queue() {
        let mut app = crate::test_support::ready_app();
        app.submit_paths("/a.wav".into());
        let id = app.pending_inputs.front().unwrap().id;
        app.cancel_inputs();
        app.handle_message(json!({
            "type": "inputs_resolved", "request_id": id,
            "files": ["/a.wav"], "duplicates": [], "errors": [], "cancelled": false
        }));
        assert!(app.queue.items.is_empty());
    }

    #[test]
    fn start_is_locked_before_worker_acknowledges() {
        let mut app = crate::test_support::ready_app();
        app.queue.add("/a.wav".into());
        app.queue.find_mut("/a.wav").unwrap().state = FileState::Done;
        app.queue.add("/b.wav".into());
        let messages = app.begin_batch(RunSelection::Pending, false);
        assert_eq!(messages[0]["files"], json!(["/b.wav"]));
        assert!(app.running());
        assert!(app.begin_batch(RunSelection::Pending, false).is_empty());
        app.handle_message(json!({"type":"started","total_files":1}));
        assert_eq!(app.queue.find_mut("/a.wav").unwrap().state, FileState::Done);
        app.worker_failed("broken pipe".into());
        assert!(!app.running());
        assert!(app.batch.is_none());
    }

    #[test]
    fn queued_inputs_lock_start_and_duplicate_responses_are_ignored() {
        let mut app = crate::test_support::ready_app();
        app.queue.add("/a.wav".into());
        app.submit_paths("/b.wav".into());
        let id = app.pending_inputs.front().unwrap().id;
        assert!(app.begin_batch(RunSelection::Pending, false).is_empty());
        let response = json!({"type":"inputs_resolved","request_id":id,"files":["/b.wav"],"duplicates":[],"errors":[],"cancelled":false});
        app.handle_message(response.clone());
        app.queue.find_mut("/b.wav").unwrap().state = FileState::Done;
        app.handle_message(response);
        assert_eq!(app.queue.items.len(), 2);
        assert_eq!(app.queue.find_mut("/b.wav").unwrap().state, FileState::Done);
    }

    #[test]
    fn startup_error_unlocks_but_file_error_does_not_end_batch() {
        let mut app = crate::test_support::ready_app();
        app.queue.add("/a.wav".into());
        app.begin_batch(RunSelection::Pending, false);
        app.handle_message(json!({"type":"error","message":"initialization failed"}));
        assert!(!app.running() && app.batch.is_none());
        app.begin_batch(RunSelection::Pending, false);
        app.handle_message(json!({"type":"started","total_files":1}));
        app.handle_message(json!({"type":"error","message":"file failed"}));
        assert!(app.running() && app.batch.is_some());
    }

    #[test]
    fn old_resolver_and_malformed_response_preserve_input() {
        let mut app = crate::test_support::ready_app();
        app.submit_paths("/first.wav".into());
        let id = app.pending_inputs.front().unwrap().id;
        app.input
            .open(crate::input::InputMode::Paths, "/new".into());
        app.handle_message(json!({"type":"inputs_resolved","request_id":id,"files":42}));
        assert_eq!(app.failed_inputs, vec!["/first.wav"]);
        assert_eq!(app.input.text(), "/new");
        app.submit_paths("/second.wav".into());
        app.handle_message(json!({"type":"error","message":"Unknown command: 'resolve_inputs'"}));
        assert!(app.pending_inputs.is_empty());
        assert_eq!(app.failed_inputs.len(), 2);
        assert_eq!(app.input.text(), "/new");
    }

    #[test]
    fn worker_failure_keeps_unstarted_entries_and_unsent_originals() {
        let mut app = crate::test_support::ready_app();
        app.queue.add("/first.wav".into());
        app.queue.add("/second.wav".into());
        app.begin_batch(RunSelection::Pending, false);
        app.handle_message(json!({"type":"started","total_files":2}));
        app.handle_message(json!({"type":"file_started","file":"/first.wav","file_index":0}));
        app.worker_failed("disconnected".into());
        assert_eq!(app.queue.items[0].state, FileState::Cancelled);
        assert_eq!(app.queue.items[1].state, FileState::Pending);
        assert!(app.begin_batch(RunSelection::Pending, false).is_empty());
        app.connection.state = crate::lifecycle::ConnectionState::Ready;
        app.submit_paths("/third.wav".into());
        app.worker_failed("send failed".into());
        assert_eq!(app.failed_inputs, vec!["/third.wav"]);
        assert!(app.take_outbox().is_empty());
    }

    #[test]
    fn clear_invalidates_only_old_inputs_not_later_pastes() {
        let mut app = crate::test_support::ready_app();
        app.submit_paths("/first.wav".into());
        let old = app.pending_inputs.front().unwrap().id;
        crate::commands::clear_queue(&mut app);
        app.submit_paths("/second.wav".into());
        let new = app.pending_inputs.back().unwrap().id;
        assert!(new > old);
        for (id, path) in [(old, "/first.wav"), (new, "/second.wav")] {
            app.handle_message(json!({"type":"inputs_resolved","request_id":id,"files":[path],"duplicates":[],"errors":[],"cancelled":false}));
        }
        assert_eq!(app.queue.items.len(), 1);
        assert_eq!(app.queue.items[0].path, "/second.wav");
    }

    #[test]
    fn selected_rerun_requires_confirmation_and_uses_one_file() {
        use crate::{
            action::{Action, ButtonId},
            app::dispatch,
        };
        let mut app = crate::test_support::ready_app();
        app.queue.add("/a.wav".into());
        app.queue.add("/b.wav".into());
        app.queue.items[1].state = FileState::Done;
        assert!(dispatch(&mut app, Action::Run(RunSelection::Selected)).is_empty());
        assert!(app.rerun_confirmation.is_some());
        assert!(dispatch(&mut app, Action::SelectFile(0)).is_empty());
        assert!(dispatch(&mut app, Action::Button(ButtonId::Start)).is_empty());
        assert_eq!(app.queue.selected_index(), Some(1));
        let messages = dispatch(&mut app, Action::ConfirmRerun(true));
        assert_eq!(messages[0]["files"], json!(["/b.wav"]));
        assert!(app.rerun_confirmation.is_none());
        dispatch(&mut app, Action::RemoveFile(0));
        assert_eq!(app.queue.items.len(), 2);
    }

    #[test]
    fn queued_duplicates_and_input_errors_do_not_reset_existing_results() {
        let mut app = crate::test_support::ready_app();
        app.queue.add("/a.wav".into());
        app.queue.items[0].state = FileState::Done;
        app.queue.items[0].results = vec!["/a.txt".into()];
        app.submit_paths("/a.wav /missing".into());
        let id = app.pending_inputs.front().unwrap().id;
        app.handle_message(json!({"type":"inputs_resolved","request_id":id,
            "files":["/a.wav"],"duplicates":["/a.wav"],"errors":[{"path":"/missing","message":"not found"}],"cancelled":false}));
        assert_eq!(app.queue.items[0].results, vec!["/a.txt"]);
        assert_eq!(app.queue.items[0].state, FileState::Done);
        assert!(app.status.contains("2"));
        assert!(app.logs.iter().any(|line| line.contains("/missing")));
        assert_eq!(app.input.mode, crate::input::InputMode::Hidden);
    }

    #[test]
    fn down_worker_preserves_editor_and_failed_input_retry_is_lossless() {
        use crate::{action::Action, app::dispatch};
        let mut app = crate::test_support::ready_app();
        app.connection.state = crate::lifecycle::ConnectionState::Unavailable;
        app.input
            .open(crate::input::InputMode::Paths, "/a.wav".into());
        crate::commands::queue_paths(&mut app, "/a.wav");
        assert_eq!(app.input.text(), "/a.wav");
        assert!(app.pending_inputs.is_empty());
        app.failed_inputs.push("/b.wav".into());
        dispatch(&mut app, Action::RetryInputs);
        assert_eq!(app.failed_inputs.len(), 1);
        app.connection.state = crate::lifecycle::ConnectionState::Ready;
        dispatch(&mut app, Action::RetryInputs);
        assert!(app.failed_inputs.is_empty());
        assert_eq!(app.pending_inputs.front().unwrap().original, "/b.wav");
        assert_eq!(app.input.text(), "/a.wav");
    }

    #[test]
    fn a_late_reply_to_another_command_does_not_reject_the_next_start() {
        use crate::lifecycle::{Activity, JobKind};
        let mut app = crate::test_support::ready_app();
        app.queue.add("/a.wav".into());
        app.begin_batch(RunSelection::Pending, false);
        // The reply to a cancel sent for the previous batch arrives late.
        app.handle_message(
            json!({"type":"error","message":"Nothing is being processed",
            "command":"cancel"}),
        );
        assert_eq!(app.activity, Activity::Starting(JobKind::Asr));
        assert!(app.batch.as_ref().unwrap().error.is_none());
        app.handle_message(json!({"type":"started","total_files":1}));
        assert_eq!(app.activity, Activity::Running(JobKind::Asr));

        // The worker's own rejection of this start still ends it.
        let mut app = crate::test_support::ready_app();
        app.queue.add("/a.wav".into());
        app.begin_batch(RunSelection::Pending, false);
        app.handle_message(json!({"type":"error","message":"Input file does not exist",
            "command":"start", "traceback":"Traceback (most recent call last):\n  File \"w.py\"\nValueError: deep cause"}));
        assert_eq!(app.activity, Activity::Idle);
        assert!(app
            .logs
            .iter()
            .any(|line| line.contains("ValueError: deep cause")));

        // An LLM start is rejected only by an error for `llm_start`.
        let mut app = crate::test_support::ready_app();
        app.llm_extra_files.push("/a.txt".into());
        crate::app::request_llm(&mut app);
        app.handle_message(json!({"type":"error","message":"x","command":"start"}));
        assert_eq!(app.activity, Activity::Starting(JobKind::Llm));
        app.handle_message(json!({"type":"error","message":"x","command":"llm_start"}));
        assert_eq!(app.activity, Activity::Idle);
    }

    #[test]
    fn resolver_failure_is_recognized_by_request_not_by_english_text() {
        let mut app = crate::test_support::ready_app();
        app.submit_paths("/a.wav".into());
        app.handle_message(json!({"type":"error","command":"llm_tool_check",
            "message":"resolve_inputs is mentioned but unrelated"}));
        assert_eq!(app.pending_inputs.len(), 1);
        app.handle_message(json!({"type":"error","command":"resolve_inputs",
            "message":"resolve_inputs failed: boom"}));
        assert!(app.pending_inputs.is_empty());
        assert_eq!(app.input.text(), "/a.wav");
    }

    #[test]
    fn an_unanswered_input_request_times_out_and_unlocks_start() {
        use std::time::{Duration, Instant};
        let mut app = crate::test_support::ready_app();
        app.queue.add("/queued.wav".into());
        app.submit_paths("/dropped folder".into());
        let id = app.pending_inputs.front().unwrap().id;
        app.take_outbox();
        let now = Instant::now();
        app.check_pending_inputs(now + Duration::from_secs(5));
        assert_eq!(
            app.pending_inputs.len(),
            1,
            "a slow scan is not an error yet"
        );
        assert!(!app.can_start(RunSelection::Pending));

        app.check_pending_inputs(now + super::INPUTS_TIMEOUT + Duration::from_secs(1));
        assert!(app.pending_inputs.is_empty());
        assert!(
            app.can_start(RunSelection::Pending),
            "Start is no longer locked"
        );
        assert_eq!(
            app.take_outbox(),
            vec![json!({"type":"cancel_inputs", "request_id":id})]
        );
        assert_eq!(
            app.input.text(),
            "/dropped folder",
            "the input is recoverable"
        );
        assert!(app
            .status
            .contains(&super::INPUTS_TIMEOUT.as_secs().to_string()));
        assert!(app.logs.contains(&app.status));
    }

    #[test]
    fn new_resolver_error_codes_are_localized_and_pathless_errors_read_cleanly() {
        let mut app = crate::test_support::ready_app();
        app.lang = crate::i18n::Lang::En;
        app.submit_paths("/folder".into());
        let id = app.pending_inputs.front().unwrap().id;
        app.handle_message(json!({"type":"inputs_resolved","request_id":id,"files":[],"duplicates":[],
            "errors":[
                {"path":"/folder/bad\u{fffd}.wav","code":"undecodable","message":"File name is not valid UTF-8"},
                {"path":"","code":"too_many_files","message":"Too many files to add at once (90000)"}
            ],"cancelled":false}));
        assert!(
            app.logs
                .iter()
                .any(|line| line.starts_with("/folder/bad\u{fffd}.wav: ")
                    && !line.contains("Unknown"))
        );
        let summary = app
            .logs
            .iter()
            .find(|line| line.contains("90000"))
            .expect("too_many_files is logged");
        assert!(!summary.starts_with(':'), "{summary}");
    }

    #[test]
    fn resolver_error_kind_is_localized_with_technical_detail_preserved() {
        let mut app = crate::test_support::ready_app();
        app.submit_paths("/missing".into());
        let id = app.pending_inputs.front().unwrap().id;
        app.handle_message(json!({"type":"inputs_resolved","request_id":id,"files":[],"duplicates":[],
            "errors":[{"path":"/missing","code":"missing","message":"ENOENT detail"}],"cancelled":false}));
        assert!(app
            .logs
            .iter()
            .any(|line| line.contains("Не найден") && line.contains("ENOENT detail")));
    }
}
