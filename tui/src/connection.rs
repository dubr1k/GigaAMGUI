use crate::{
    app::{reset_after_worker_restart, App},
    i18n::{t, tf},
    lifecycle::{Connection, ConnectionState},
    worker_session::{WorkerEvent, WorkerEventKind},
};
use serde_json::json;
use std::time::Instant;

impl App {
    pub(crate) fn begin_connection(&mut self, now: Instant) {
        reset_after_worker_restart(self);
        self.connection = Connection::new(self.connection.generation + 1, now);
        self.worker_stopped = false;
        self.worker_stop_requested = false;
        self.reconnect_requested = false;
        self.status = t(self.lang, "status.worker_connecting").into();
    }

    pub(crate) fn check_connection(&mut self, now: Instant) {
        if self.connection.timed_out(now) {
            self.worker_failed(t(self.lang, "status.worker_timeout").into());
            self.worker_stop_requested = true;
        }
    }

    pub(crate) fn can_reconnect(&self) -> bool {
        self.reconnect_requested && self.worker_stopped
    }

    pub(crate) fn request_reconnect(&mut self) {
        if self.running() {
            self.stop_confirmation = true;
        } else {
            self.reconnect_requested = true;
            self.worker_stop_requested = !self.worker_stopped;
            self.connection.state = ConnectionState::Unavailable;
            self.status = t(self.lang, "status.reconnecting").into();
        }
    }

    pub(crate) fn handle_worker_event(&mut self, event: WorkerEvent) {
        if event.generation != self.connection.generation {
            return;
        }
        match event.kind {
            WorkerEventKind::Diagnostic(message) => self.log(message),
            WorkerEventKind::Failed(error) => {
                self.worker_failed(tf(
                    self.lang,
                    "status.worker_unavailable",
                    &[("error", &error)],
                ));
            }
            WorkerEventKind::Stopped(result) => match result {
                Ok(()) => {
                    self.worker_stopped = true;
                    if self.connection.state != ConnectionState::Unavailable {
                        self.worker_failed(t(self.lang, "status.worker_exited").into());
                    }
                }
                Err(error) => {
                    self.worker_stopped = false;
                    self.worker_failed(tf(self.lang, "status.stop_failed", &[("error", &error)]));
                }
            },
            WorkerEventKind::Message(value) => {
                if self.connection.state == ConnectionState::Connecting {
                    if value["type"] == "ready" {
                        match self.connection.ready(&value) {
                            Ok(()) => {
                                self.status = t(self.lang, "status.ready").into();
                                self.outbox.push(
                                    json!({"type":"llm_tools", "overrides":self.llm_tool_paths}),
                                );
                            }
                            Err(error) => {
                                self.worker_failed(tf(
                                    self.lang,
                                    "status.worker_incompatible",
                                    &[("error", &error)],
                                ));
                                self.worker_stop_requested = true;
                            }
                        }
                    } else if value["type"] == "error" {
                        self.worker_failed(tf(
                            self.lang,
                            "status.worker_incompatible",
                            &[("error", value["message"].as_str().unwrap_or("handshake"))],
                        ));
                        self.worker_stop_requested = true;
                    }
                } else if self.connection.state == ConnectionState::Ready {
                    self.handle_message(value);
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use crate::{
        app::{dispatch, App},
        lifecycle::{Activity, ConnectionState, JobKind},
        queue::RunSelection,
        ui::{Action, ButtonId},
        worker_session::{WorkerEvent, WorkerEventKind},
    };
    use serde_json::json;
    use std::time::{Duration, Instant};

    fn event(generation: u64, kind: WorkerEventKind) -> WorkerEvent {
        WorkerEvent { generation, kind }
    }
    fn ready() -> WorkerEventKind {
        WorkerEventKind::Message(json!({"type":"ready", "protocol_version":1,
            "capabilities":["resolve_inputs","asr","llm"]}))
    }

    #[test]
    fn spawn_is_not_readiness_and_old_generation_cannot_change_state() {
        let mut app = App::default();
        app.queue.add("/a.wav".into());
        app.llm_extra_files.push("/a.txt".into());
        app.begin_connection(Instant::now());
        assert!(app.begin_batch(RunSelection::Pending, false).is_empty());
        assert!(dispatch(&mut app, Action::Button(ButtonId::RunLlm)).is_empty());
        app.handle_worker_event(event(0, ready()));
        assert_eq!(app.connection.state, ConnectionState::Connecting);
        app.handle_worker_event(event(1, ready()));
        assert_eq!(app.connection.state, ConnectionState::Ready);
        assert!(!app.begin_batch(RunSelection::Pending, false).is_empty());
        app.handle_worker_event(event(0, WorkerEventKind::Failed("old".into())));
        assert_eq!(app.activity, Activity::Starting(JobKind::Asr));
    }

    #[test]
    fn incompatible_and_silent_workers_become_unavailable() {
        let mut app = App::default();
        let now = Instant::now();
        app.begin_connection(now);
        app.check_connection(now + Duration::from_secs(15));
        assert_eq!(app.connection.state, ConnectionState::Unavailable);
        assert!(app.worker_stop_requested);
        app.begin_connection(now);
        app.handle_worker_event(event(
            2,
            WorkerEventKind::Message(json!({"type":"ready", "protocol_version":99})),
        ));
        assert!(app.worker_down());
        assert!(
            app.status.contains("99")
                || app.status.contains("protocol")
                || app.status.contains("протокол")
        );
    }

    #[test]
    fn reconnect_needs_successful_stop_and_keeps_results_and_queue() {
        let mut app = crate::test_support::ready_app();
        app.queue.add("/a.wav".into());
        app.result_files.push("/done.txt".into());
        dispatch(&mut app, Action::Reconnect);
        assert!(app.worker_stop_requested && app.reconnect_requested);
        assert!(!app.can_reconnect());
        app.handle_worker_event(event(
            0,
            WorkerEventKind::Stopped(Err("still alive".into())),
        ));
        assert!(!app.can_reconnect());
        app.handle_worker_event(event(0, WorkerEventKind::Stopped(Ok(()))));
        assert!(app.can_reconnect());
        app.begin_connection(Instant::now());
        assert_eq!(app.queue.items.len(), 1);
        assert_eq!(app.result_files, vec!["/done.txt"]);
        assert_eq!(app.activity, Activity::Idle);
    }

    #[test]
    fn llm_start_error_unlocks_but_wrong_kind_completion_does_not() {
        let mut app = crate::test_support::ready_app();
        app.llm_extra_files.push("/a.txt".into());
        dispatch(&mut app, Action::Button(ButtonId::RunLlm));
        app.handle_message(json!({"type":"completed", "success":true}));
        assert_eq!(app.activity, Activity::Starting(JobKind::Llm));
        app.handle_message(json!({"type":"error", "message":"fixture start failed"}));
        assert_eq!(app.activity, Activity::Idle);
        assert_eq!(app.status, "fixture start failed");
    }

    #[test]
    fn multiple_llm_mode_starts_belong_to_one_active_job() {
        let mut app = crate::test_support::ready_app();
        app.llm_extra_files.push("/a.txt".into());
        dispatch(&mut app, Action::Button(ButtonId::RunLlm));
        app.handle_message(json!({"type":"llm_started", "mode":"summary", "index":1, "total":2}));
        app.handle_message(json!({"type":"llm_chunk", "text":"old mode"}));
        app.handle_message(json!({"type":"llm_started", "mode":"tasks", "index":2, "total":2}));
        assert_eq!(app.llm_stream_mode, "tasks");
        assert!(app.llm_stream.is_empty());
        assert_eq!(app.activity, Activity::Running(JobKind::Llm));
    }

    #[test]
    fn stop_confirmation_preserves_soft_cancel_on_escape() {
        use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
        let mut app = crate::test_support::ready_app();
        app.llm_extra_files.push("/a.txt".into());
        dispatch(&mut app, Action::Button(ButtonId::RunLlm));
        let esc = KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE);
        assert_eq!(
            crate::keys::handle_key(&mut app, esc)[0]["type"],
            "llm_cancel"
        );
        assert!(crate::keys::handle_key(&mut app, esc).is_empty());
        assert!(app.stop_confirmation);
        assert!(!app.worker_stop_requested);
        assert!(crate::keys::handle_key(&mut app, esc).is_empty());
        assert!(!app.stop_confirmation);
        assert_eq!(app.activity, Activity::CancellingStart(JobKind::Llm));
        dispatch(&mut app, Action::ForceStop);
        crate::commands::paste_input(&mut app, "/unexpected.wav");
        assert!(app.input.is_empty());
        dispatch(&mut app, Action::ConfirmStop(true));
        assert!(app.worker_stop_requested && app.reconnect_requested);
        assert!(app.worker_down());
    }

    #[test]
    fn unconfirmed_cli_stop_keeps_new_jobs_locked() {
        let mut app = crate::test_support::ready_app();
        app.llm_extra_files.push("/a.txt".into());
        app.queue.add("/a.wav".into());
        dispatch(&mut app, Action::Button(ButtonId::RunLlm));
        dispatch(&mut app, Action::Button(ButtonId::CancelLlm));
        app.handle_message(json!({"type":"llm_completed", "success":false,
            "termination_failed":true, "message":"owned CLI still alive"}));
        assert_eq!(app.activity, Activity::Stopping(JobKind::Llm));
        app.handle_message(json!({"type":"error", "message":"owned CLI still alive"}));
        assert_eq!(app.activity, Activity::Stopping(JobKind::Llm));
        assert!(dispatch(&mut app, Action::Button(ButtonId::RunLlm)).is_empty());
        assert!(app.begin_batch(RunSelection::Pending, false).is_empty());
        assert!(app.status.contains("owned CLI still alive"));
        dispatch(&mut app, Action::Reconnect);
        assert!(app.stop_confirmation);
    }

    #[test]
    fn rejected_start_after_early_cancel_does_not_leave_a_phantom_job() {
        use crate::ui::{Action, ButtonId};
        use crate::{app::dispatch, queue::RunSelection};
        for button in [ButtonId::Stop, ButtonId::CancelLlm] {
            let mut app = crate::test_support::ready_app();
            app.queue.add("/removed.wav".into());
            app.llm_extra_files.push("/removed.txt".into());
            if button == ButtonId::Stop {
                app.begin_batch(RunSelection::Pending, false);
            } else {
                dispatch(&mut app, Action::Button(ButtonId::RunLlm));
            }
            dispatch(&mut app, Action::Button(button));
            app.handle_message(
                serde_json::json!({"type":"error","message":"Input file does not exist"}),
            );
            assert!(
                !app.running(),
                "rejected startup has no running process to cancel"
            );
            assert!(app.status.contains("does not exist"));
            assert!(app.begin_batch(RunSelection::Pending, false).len() == 1);
        }
    }
}
