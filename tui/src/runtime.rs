//! Bridge between App and its asynchronous transport; no process I/O in frame work.
use crate::{
    app::App,
    i18n::tf,
    worker_session::{WorkerEvent, WorkerEventKind, WorkerSession, EVENT_CAPACITY},
};
use serde_json::{json, Value};
use std::{
    io,
    sync::mpsc::{self, Receiver, SyncSender},
    time::{Duration, Instant},
};

/// Per tick: at most this many worker events …
const DRAIN_LIMIT: usize = 256;
/// … and no longer than this, so that a flood never stalls drawing or keys.
const DRAIN_BUDGET: Duration = Duration::from_millis(10);

pub(crate) struct WorkerRuntime {
    session: Option<WorkerSession>,
    events: Receiver<WorkerEvent>,
    sender: SyncSender<WorkerEvent>,
    generation: u64,
    stopped: bool,
}

impl WorkerRuntime {
    pub fn new(app: &mut App) -> Self {
        let (sender, events) = mpsc::sync_channel(EVENT_CAPACITY);
        let mut runtime = Self {
            session: None,
            events,
            sender,
            generation: 0,
            stopped: true,
        };
        runtime.connect(app);
        runtime
    }

    fn connect(&mut self, app: &mut App) {
        app.begin_connection(Instant::now());
        self.generation = app.connection.generation;
        self.stopped = false;
        self.session = Some(WorkerSession::spawn(self.generation, self.sender.clone()));
        self.deliver(app, vec![json!({"type":"hello", "client":"tui"})]);
    }

    pub fn deliver(&self, app: &mut App, commands: Vec<Value>) {
        for command in commands {
            let result = self.session.as_ref().map_or_else(
                || {
                    Err(io::Error::new(
                        io::ErrorKind::BrokenPipe,
                        "worker is not running",
                    ))
                },
                |session| session.try_send(command),
            );
            if let Err(error) = result {
                app.worker_failed(tf(
                    app.lang,
                    "status.worker_unavailable",
                    &[("error", &error.to_string())],
                ));
                app.worker_stop_requested = true;
                break;
            }
        }
    }

    pub fn tick(&mut self, app: &mut App) {
        app.poll_result_opening();
        // Everything that is queued, within a frame-friendly budget. At most
        // EVENT_CAPACITY per tick, with an 80 ms idle poll, capped the transport at
        // ~100 messages a second: a token stream backed up into the pipe and the
        // worker blocked in `emit`, delaying `llm_completed` and everything else.
        let started = Instant::now();
        for _ in 0..DRAIN_LIMIT {
            if started.elapsed() >= DRAIN_BUDGET {
                break;
            }
            let Ok(event) = self.events.try_recv() else {
                break;
            };
            if event.generation == self.generation {
                if let WorkerEventKind::Stopped(result) = &event.kind {
                    self.stopped = result.is_ok();
                }
            }
            app.handle_worker_event(event);
        }
        let now = Instant::now();
        app.check_connection(now);
        app.check_pending_inputs(now);
        if app.worker_stop_requested {
            if let Some(session) = &self.session {
                session.stop();
            }
            app.worker_stop_requested = false;
        }
        if app.can_reconnect() {
            self.connect(app);
        }
        let commands = app.take_outbox();
        self.deliver(app, commands);
    }

    fn await_stop(&mut self, deadline: Instant) -> Result<(), String> {
        while Instant::now() < deadline {
            match self.events.recv_timeout(Duration::from_millis(20)) {
                Ok(WorkerEvent {
                    generation,
                    kind: WorkerEventKind::Stopped(result),
                }) if generation == self.generation => {
                    self.stopped = result.is_ok();
                    return result;
                }
                Err(mpsc::RecvTimeoutError::Disconnected) => break,
                _ => {}
            }
        }
        Err("worker termination was not confirmed before the shutdown deadline".into())
    }
}

impl Drop for WorkerRuntime {
    fn drop(&mut self) {
        // Only final shutdown/unwind waits here. Reconnect replaces an already
        // stopped session; frame work never waits on process termination.
        if self.stopped {
            return;
        }
        if let Some(session) = &self.session {
            session.stop();
        }
        if let Err(error) = self.await_stop(Instant::now() + Duration::from_secs(6)) {
            // TerminalGuard has already restored the terminal. Never panic in Drop.
            use std::io::Write;
            let _ = writeln!(io::stderr(), "GigaAM worker shutdown: {error}");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn one_tick_drains_a_token_stream_not_just_eight_events() {
        let mut app = crate::test_support::ready_app();
        app.activity.start(crate::lifecycle::JobKind::Llm);
        app.handle_message(json!({"type":"llm_started","mode":"summary","index":1,"total":1}));
        let (sender, events) = mpsc::sync_channel(64);
        let mut runtime = WorkerRuntime {
            session: None,
            sender,
            events,
            generation: app.connection.generation,
            stopped: true,
        };
        for index in 0..40 {
            runtime
                .sender
                .send(WorkerEvent {
                    generation: app.connection.generation,
                    kind: WorkerEventKind::Message(
                        json!({"type":"llm_chunk","mode":"summary","text":format!("{index},")}),
                    ),
                })
                .unwrap();
        }
        runtime.tick(&mut app);
        let expected: String = (0..40).map(|index| format!("{index},")).collect();
        assert_eq!(app.llm_stream, expected);
    }

    #[test]
    fn shutdown_requires_confirmation_and_reports_the_original_failure() {
        for outcome in [Ok(()), Err("owned process still alive".into())] {
            let (sender, events) = mpsc::sync_channel(EVENT_CAPACITY);
            let mut runtime = WorkerRuntime {
                session: None,
                sender,
                events,
                generation: 2,
                stopped: false,
            };
            runtime
                .sender
                .send(WorkerEvent {
                    generation: 1,
                    kind: WorkerEventKind::Stopped(Ok(())),
                })
                .unwrap();
            runtime
                .sender
                .send(WorkerEvent {
                    generation: 2,
                    kind: WorkerEventKind::Stopped(outcome.clone()),
                })
                .unwrap();
            let result = runtime.await_stop(Instant::now() + Duration::from_secs(1));
            assert_eq!(result, outcome);
            assert_eq!(runtime.stopped, result.is_ok());
            runtime.stopped = true; // Fixture has no OS process to stop during Drop.
        }
    }
}
