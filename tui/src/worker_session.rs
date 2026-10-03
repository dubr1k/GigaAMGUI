//! JSONL transport to the worker, for the interactive UI and headless mode alike:
//! its own process group/Job, bounded lines, reaping. No pipe I/O or process
//! waits on the caller's thread.
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

/// What happens to the worker's stderr.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Stderr {
    /// Read, sanitized and delivered as [`WorkerEventKind::Diagnostic`] (the UI).
    Diagnostics,
    /// Passed straight to our own stderr (headless human output).
    Inherit,
    /// Discarded (headless `--json` / `--quiet`).
    Discard,
}

/// Per-client transport policy.
#[derive(Clone, Copy, Debug)]
pub(crate) struct Transport {
    pub stderr: Stderr,
    /// Longest protocol line accepted; a longer one is a protocol failure.
    pub max_protocol_line: usize,
}

impl Transport {
    /// The UI negotiates compact ASR events (`hello`), so 8 MiB is plenty.
    pub(crate) const INTERACTIVE: Self = Self {
        stderr: Stderr::Diagnostics,
        max_protocol_line: MAX_PROTOCOL_LINE,
    };
}
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
        Self::spawn_with(generation, command, Transport::INTERACTIVE, events)
    }

    pub fn spawn_with(
        generation: u64,
        command: Command,
        transport: Transport,
        events: SyncSender<WorkerEvent>,
    ) -> Self {
        let (commands, incoming) = mpsc::sync_channel(64);
        let (control, requests) = mpsc::sync_channel(1);
        thread::spawn(move || {
            controller(command, transport, generation, incoming, requests, events)
        });
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
    transport: Transport,
    generation: u64,
    commands: Receiver<Value>,
    control: Receiver<()>,
    events: SyncSender<WorkerEvent>,
) {
    command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(match transport.stderr {
            Stderr::Diagnostics => Stdio::piped(),
            Stderr::Inherit => Stdio::inherit(),
            Stderr::Discard => Stdio::null(),
        });
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
    let mut readers = vec![
        reader(
            child.stdout.take().expect("piped stdout"),
            Some(transport.max_protocol_line),
            data_tx.clone(),
            fault_tx.clone(),
        ),
        writer(
            child.stdin.take().expect("piped stdin"),
            commands,
            fault_tx.clone(),
            shutdown.clone(),
        ),
    ];
    if let Some(stderr) = child.stderr.take() {
        readers.push(reader(stderr, None, data_tx, fault_tx));
    }
    let mut pending = None;
    // The event that followed a folded chunk; it goes out next, keeping the order.
    let mut held = None;
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
            pending = held.take().or_else(|| data_rx.recv_timeout(TICK).ok());
        }
        if let Some(kind) = pending.take() {
            match events.try_send(WorkerEvent { generation, kind }) {
                Ok(()) => {}
                Err(TrySendError::Disconnected(_)) => break None,
                Err(TrySendError::Full(event)) => {
                    let mut kind = event.kind;
                    if held.is_none() {
                        fold_chunks(&mut kind, &data_rx, &mut held);
                    } else {
                        thread::sleep(TICK);
                    }
                    pending = Some(kind);
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
        other => {
            // Already stopping: a stop request seen here asks for nothing new.
            publish(&events, &control, generation, other);
        }
    };
    for kind in [pending, held].into_iter().flatten() {
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
    result = join_finished(&mut readers, result);
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
    // An unconfirmed stop (a descendant escaped the group/Job, or a pipe is still
    // held open) used to end this thread: the next `stop()` from Ctrl+R reached
    // nobody and the TUI waited for a confirmation forever. The controller stays
    // reachable instead and retries on every further stop request, so reconnect
    // and the final shutdown can still confirm once the tree is really gone.
    loop {
        let retry_requested = publish(
            &events,
            &control,
            generation,
            WorkerEventKind::Stopped(result.clone()),
        );
        if result.is_ok() {
            return;
        }
        if !retry_requested && control.recv().is_err() {
            return; // The session was dropped: nobody waits for an answer.
        }
        result = tree
            .terminate(&mut child)
            .map_err(|error| error.to_string());
        let deadline = Instant::now() + Duration::from_secs(2);
        while readers.iter().any(|reader| !reader.is_finished()) && Instant::now() < deadline {
            thread::sleep(TICK);
        }
        result = join_finished(&mut readers, result);
    }
}

/// Text one folded `llm_chunk` may grow to before it waits like any other event.
const MAX_FOLDED_TEXT: usize = 64 * 1024;

/// The text of an `llm_chunk` message and its mode.
fn chunk(kind: &mut WorkerEventKind) -> Option<(&mut String, Value)> {
    let WorkerEventKind::Message(Value::Object(message)) = kind else {
        return None;
    };
    if message.get("type").and_then(Value::as_str) != Some("llm_chunk") {
        return None;
    }
    let mode = message.get("mode").cloned().unwrap_or(Value::Null);
    match message.get_mut("text") {
        Some(Value::String(text)) => Some((text, mode)),
        _ => None,
    }
}

/// While the consumer has no room, the token-stream chunks that arrive join the
/// one waiting for it (same mode, bounded size), for up to one tick. One queue
/// slot per token used to back the stream up into the pipe until the worker
/// blocked in `emit`, holding its write lock, which delayed every other
/// message. The first event that is not such a chunk is `held` and goes out
/// right after the folded one, so the order never changes.
fn fold_chunks(
    kind: &mut WorkerEventKind,
    data: &Receiver<WorkerEventKind>,
    held: &mut Option<WorkerEventKind>,
) {
    let deadline = Instant::now() + TICK;
    loop {
        let now = Instant::now();
        if now >= deadline {
            return;
        }
        let Some((text, mode)) = chunk(kind) else {
            thread::sleep(deadline - now);
            return;
        };
        if text.len() >= MAX_FOLDED_TEXT {
            thread::sleep(deadline - now);
            return;
        }
        match data.recv_timeout(deadline - now) {
            Ok(mut next) => match chunk(&mut next) {
                Some((more, next_mode)) if next_mode == mode => text.push_str(more),
                _ => {
                    *held = Some(next);
                    thread::sleep(deadline.saturating_duration_since(Instant::now()));
                    return;
                }
            },
            Err(_) => return,
        }
    }
}

/// Joins the transport threads that have ended and keeps the rest for a retry;
/// a thread still blocked on a pipe means the stop is not confirmed.
fn join_finished(
    readers: &mut Vec<JoinHandle<()>>,
    mut result: Result<(), String>,
) -> Result<(), String> {
    let mut pending = Vec::new();
    for reader in readers.drain(..) {
        if !reader.is_finished() {
            pending.push(reader);
        } else if reader.join().is_err() {
            result = Err("worker transport thread panicked".into());
        }
    }
    if !pending.is_empty() {
        result = Err("worker pipes did not close after termination".into());
    }
    *readers = pending;
    result
}

/// Delivers one event, waiting while the queue is full. Returns whether a stop
/// request arrived meanwhile, so that the caller does not lose it.
fn publish(
    events: &SyncSender<WorkerEvent>,
    control: &Receiver<()>,
    generation: u64,
    kind: WorkerEventKind,
) -> bool {
    let mut event = WorkerEvent { generation, kind };
    let mut stop_requested = false;
    loop {
        match events.try_send(event) {
            Ok(()) | Err(TrySendError::Disconnected(_)) => return stop_requested,
            Err(TrySendError::Full(returned)) => event = returned,
        }
        match control.try_recv() {
            Ok(()) => stop_requested = true,
            Err(TryRecvError::Disconnected) => return stop_requested,
            Err(TryRecvError::Empty) => {}
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

/// `protocol_line`: `Some(limit)` for stdout (the protocol), `None` for stderr.
fn reader(
    stream: impl Read + Send + 'static,
    protocol_line: Option<usize>,
    data: SyncSender<WorkerEventKind>,
    faults: Sender<String>,
) -> JoinHandle<()> {
    let stdout = protocol_line.is_some();
    thread::spawn(move || {
        let mut reader = BufReader::new(stream);
        loop {
            match bounded_line(&mut reader, protocol_line.unwrap_or(MAX_LINE)) {
                Ok(Some((line, truncated))) => {
                    if stdout && truncated {
                        let _ = faults.send(format!(
                            "worker protocol exceeds the {} MiB message limit; reduce the batch/answer size or update TUI and worker together",
                            protocol_line.unwrap_or_default() / (1024 * 1024)
                        ));
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
        StringEsc,
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
                '\x1b' => Escape::StringEsc,
                _ => Escape::String,
            },
            Escape::StringEsc => {
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
