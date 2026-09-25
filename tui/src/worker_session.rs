//! Interactive JSONL transport. No pipe I/O or process waits on the UI thread.
use std::{
    collections::VecDeque,
    io::{self, BufRead, BufReader, Read},
    process::{Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc::{self, Receiver, Sender, SyncSender, TryRecvError, TrySendError},
        Arc,
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

use serde_json::Value;

mod process;
use process::ProcessTree;

const TICK: Duration = Duration::from_millis(20);
const MAX_LINE: usize = 64 * 1024;
const MAX_PROTOCOL_LINE: usize = 8 * 1024 * 1024;
const DIAGNOSTIC_TAIL: usize = 128;
// Larger legitimate JSON events must not multiply into hundreds of queued copies.
pub(crate) const EVENT_CAPACITY: usize = 8;

#[derive(Debug)]
pub(crate) struct WorkerEvent {
    pub generation: u64,
    pub kind: WorkerEventKind,
}

#[derive(Debug)]
pub(crate) enum WorkerEventKind {
    Message(Value),
    Diagnostic(String),
    Failed(String),
    Stopped(Result<(), String>),
}

pub(crate) struct WorkerSession {
    commands: SyncSender<Value>,
    control: SyncSender<()>,
}

impl WorkerSession {
    pub fn spawn(generation: u64, events: SyncSender<WorkerEvent>) -> Self {
        Self::spawn_with_command(generation, crate::worker::worker_command(), events)
    }

    pub fn spawn_with_command(
        generation: u64,
        command: Command,
        events: SyncSender<WorkerEvent>,
    ) -> Self {
        let (commands, incoming) = mpsc::sync_channel(64);
        let (control, requests) = mpsc::sync_channel(1);
        thread::spawn(move || controller(command, generation, incoming, requests, events));
        Self { commands, control }
    }

    pub fn try_send(&self, message: Value) -> io::Result<()> {
        self.commands
            .try_send(message)
            .map_err(|error| match error {
                TrySendError::Full(_) => {
                    io::Error::new(io::ErrorKind::WouldBlock, "worker command queue is full")
                }
                TrySendError::Disconnected(_) => io::Error::new(
                    io::ErrorKind::BrokenPipe,
                    "worker command channel is closed",
                ),
            })
    }

    pub fn stop(&self) {
        // Full means a stop is already pending. It never waits on the stdin writer.
        let _ = self.control.try_send(());
    }
}

// Dropping the session disconnects control; the controller still owns cleanup.
fn controller(
    mut command: Command,
    generation: u64,
    commands: Receiver<Value>,
    control: Receiver<()>,
    events: SyncSender<WorkerEvent>,
) {
    command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let (mut child, tree) = match ProcessTree::spawn(&mut command) {
        Ok(pair) => pair,
        Err(error) => {
            drop(commands);
            publish(
                &events,
                &control,
                generation,
                WorkerEventKind::Failed(format!("worker spawn: {error}")),
            );
            publish(
                &events,
                &control,
                generation,
                WorkerEventKind::Stopped(Ok(())),
            );
            return;
        }
    };
    let shutdown = Arc::new(AtomicBool::new(false));
    let (data_tx, data_rx) = mpsc::sync_channel(EVENT_CAPACITY);
    // Each producer can report at most one fault; diagnostics never use this path.
    let (fault_tx, faults) = mpsc::channel();
    let readers = vec![
        reader(
            child.stdout.take().expect("piped stdout"),
            true,
            data_tx.clone(),
            fault_tx.clone(),
        ),
        reader(
            child.stderr.take().expect("piped stderr"),
            false,
            data_tx,
            fault_tx.clone(),
        ),
        writer(
            child.stdin.take().expect("piped stdin"),
            commands,
            fault_tx,
            shutdown.clone(),
        ),
    ];
    let mut pending = None;
    let failure = loop {
        match control.try_recv() {
            Ok(()) | Err(TryRecvError::Disconnected) => break None,
            Err(TryRecvError::Empty) => {}
        }
        if let Ok(error) = faults.try_recv() {
            break Some(error);
        }
        match child.try_wait() {
            Ok(Some(status)) => break Some(format!("worker exited: {status}")),
            Err(error) => break Some(format!("worker wait: {error}")),
            Ok(None) => {}
        }
        if pending.is_none() {
            pending = data_rx.recv_timeout(TICK).ok();
        }
        if let Some(kind) = pending.take() {
            match events.try_send(WorkerEvent { generation, kind }) {
                Ok(()) => {}
                Err(TrySendError::Disconnected(_)) => break None,
                Err(TrySendError::Full(event)) => {
                    pending = Some(event.kind);
                    thread::sleep(TICK);
                }
            }
        }
    };
    shutdown.store(true, Ordering::Release);
    let mut result = tree
        .terminate(&mut child)
        .map_err(|error| error.to_string());
    // Drain pipes before reporting failure: startup tracebacks can arrive after EOF
    // on the other stream. Keep a bounded diagnostic tail during this final drain.
    let mut diagnostics = VecDeque::new();
    let mut omitted = 0;
    let mut collect = |kind| match kind {
        WorkerEventKind::Diagnostic(message) => {
            if diagnostics.len() == DIAGNOSTIC_TAIL {
                diagnostics.pop_front();
                omitted += 1;
            }
            diagnostics.push_back(message);
        }
        other => publish(&events, &control, generation, other),
    };
    if let Some(kind) = pending {
        collect(kind);
    }
    let deadline = Instant::now() + Duration::from_secs(2);
    while readers.iter().any(|thread| !thread.is_finished()) && Instant::now() < deadline {
        match data_rx.recv_timeout(TICK) {
            Ok(kind) => collect(kind),
            Err(mpsc::RecvTimeoutError::Disconnected) => thread::sleep(TICK),
            Err(mpsc::RecvTimeoutError::Timeout) => {}
        }
    }
    while let Ok(kind) = data_rx.try_recv() {
        collect(kind);
    }
    // On a failed cleanup, unblock any reader waiting to deliver data as well.
    drop(data_rx);
    for reader in readers {
        if reader.is_finished() {
            if reader.join().is_err() {
                result = Err("worker transport thread panicked".into());
            }
        } else {
            result = Err("worker pipes did not close after termination".into());
        }
    }
    if omitted > 0 {
        publish(
            &events,
            &control,
            generation,
            WorkerEventKind::Diagnostic(format!(
                "worker: {omitted} earlier diagnostic lines omitted during shutdown"
            )),
        );
    }
    for message in diagnostics {
        publish(
            &events,
            &control,
            generation,
            WorkerEventKind::Diagnostic(message),
        );
    }
    if let Some(error) = failure {
        publish(
            &events,
            &control,
            generation,
            WorkerEventKind::Failed(error),
        );
    }
    publish(
        &events,
        &control,
        generation,
        WorkerEventKind::Stopped(result),
    );
}

fn publish(
    events: &SyncSender<WorkerEvent>,
    control: &Receiver<()>,
    generation: u64,
    kind: WorkerEventKind,
) {
    let mut event = WorkerEvent { generation, kind };
    loop {
        match events.try_send(event) {
            Ok(()) | Err(TrySendError::Disconnected(_)) => return,
            Err(TrySendError::Full(returned)) => event = returned,
        }
        if matches!(control.try_recv(), Err(TryRecvError::Disconnected)) {
            return;
        }
        thread::sleep(TICK);
    }
}

fn writer(
    mut stdin: std::process::ChildStdin,
    commands: Receiver<Value>,
    faults: Sender<String>,
    shutdown: Arc<AtomicBool>,
) -> JoinHandle<()> {
    thread::spawn(move || {
        while !shutdown.load(Ordering::Acquire) {
            match commands.recv_timeout(TICK) {
                Ok(message) => {
                    if let Err(error) = crate::worker::send(&mut stdin, message) {
                        let _ = faults.send(format!("worker stdin: {error}"));
                        break;
                    }
                }
                Err(mpsc::RecvTimeoutError::Disconnected) => break,
                Err(mpsc::RecvTimeoutError::Timeout) => {}
            }
        }
    })
}

fn reader(
    stream: impl Read + Send + 'static,
    stdout: bool,
    data: SyncSender<WorkerEventKind>,
    faults: Sender<String>,
) -> JoinHandle<()> {
    thread::spawn(move || {
        let mut reader = BufReader::new(stream);
        loop {
            match bounded_line(
                &mut reader,
                if stdout { MAX_PROTOCOL_LINE } else { MAX_LINE },
            ) {
                Ok(Some((line, truncated))) => {
                    if stdout && truncated {
                        let _ = faults.send("worker protocol exceeds the 8 MiB message limit; reduce the batch/answer size or update TUI and worker together".into());
                        break;
                    }
                    let value = if stdout && !truncated {
                        serde_json::from_slice::<Value>(&line)
                            .ok()
                            .filter(|v| v.is_object() && v["type"].is_string())
                    } else {
                        None
                    };
                    let event = if let Some(value) = value {
                        WorkerEventKind::Message(value)
                    } else {
                        let prefix = if stdout { "stdout: " } else { "stderr: " };
                        let text = sanitize(&String::from_utf8_lossy(&line));
                        let suffix = if truncated || line.len() > MAX_LINE {
                            " [truncated]"
                        } else {
                            ""
                        };
                        WorkerEventKind::Diagnostic(format!("{prefix}{text}{suffix}"))
                    };
                    if data.send(event).is_err() {
                        break;
                    }
                }
                Ok(None) => {
                    if stdout {
                        let _ = faults.send("worker stdout closed".into());
                    }
                    break;
                }
                Err(error) => {
                    let _ = faults.send(format!(
                        "worker {}: {error}",
                        if stdout { "stdout" } else { "stderr" }
                    ));
                    break;
                }
            }
        }
    })
}

fn bounded_line(reader: &mut impl BufRead, limit: usize) -> io::Result<Option<(Vec<u8>, bool)>> {
    let mut line = Vec::new();
    let mut truncated = false;
    loop {
        let buffer = reader.fill_buf()?;
        if buffer.is_empty() {
            return Ok((!line.is_empty() || truncated).then_some((line, truncated)));
        }
        let newline = buffer.iter().position(|b| *b == b'\n');
        let length = newline.unwrap_or(buffer.len());
        let keep = length.min(limit - line.len());
        line.extend_from_slice(&buffer[..keep]);
        truncated |= keep < length;
        reader.consume(length + usize::from(newline.is_some()));
        if newline.is_some() {
            return Ok(Some((line, truncated)));
        }
    }
}

/// Strip CSI, OSC/DCS strings and C0/C1 controls; preserve readable Unicode.
fn sanitize(text: &str) -> String {
    enum Escape {
        Text,
        Start,
        Csi,
        String,
        StringEscape,
    }
    let mut state = Escape::Text;
    let mut output = String::new();
    for ch in text.chars() {
        state = match state {
            Escape::Text => match ch {
                '\x1b' => Escape::Start,
                '\u{9b}' => Escape::Csi,
                '\u{90}' | '\u{9d}' | '\u{9e}' | '\u{9f}' => Escape::String,
                _ => {
                    if !ch.is_control() {
                        output.push(ch);
                    }
                    Escape::Text
                }
            },
            Escape::Start => match ch {
                '[' => Escape::Csi,
                ']' | 'P' | '^' | '_' => Escape::String,
                _ => Escape::Text,
            },
            Escape::Csi => {
                if ('@'..='~').contains(&ch) {
                    Escape::Text
                } else {
                    Escape::Csi
                }
            }
            Escape::String => match ch {
                '\x07' | '\u{9c}' => Escape::Text,
                '\x1b' => Escape::StringEscape,
                _ => Escape::String,
            },
            Escape::StringEscape => {
                if ch == '\\' {
                    Escape::Text
                } else {
                    Escape::String
                }
            }
        };
    }
    // Lossy UTF-8 can expand one byte into three. Keep the displayed entry bounded too.
    let mut end = output.len().min(MAX_LINE);
    while !output.is_char_boundary(end) {
        end -= 1;
    }
    output.truncate(end);
    output
}

#[cfg(test)]
mod tests;
