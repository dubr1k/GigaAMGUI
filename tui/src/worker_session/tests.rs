use super::*;
use serde_json::json;
use std::{
    process::Command,
    sync::mpsc,
    time::{Duration, Instant},
};

fn python(script: &str) -> Command {
    let mut command = Command::new(std::env::var("GIGAAM_PYTHON").unwrap_or_else(|_| {
        if cfg!(windows) {
            "python".into()
        } else {
            "python3".into()
        }
    }));
    command.args(["-u", "-c", script]);
    command
}

fn receive(rx: &mpsc::Receiver<WorkerEvent>) -> WorkerEventKind {
    let event = rx
        .recv_timeout(Duration::from_secs(5))
        .expect("worker event");
    assert_eq!(event.generation, 7);
    event.kind
}

fn stopped(rx: &mpsc::Receiver<WorkerEvent>) -> Vec<WorkerEventKind> {
    let mut events = Vec::new();
    loop {
        let event = receive(rx);
        if let WorkerEventKind::Stopped(result) = &event {
            assert!(result.is_ok(), "{result:?}");
            return events;
        }
        events.push(event);
    }
}

#[test]
fn commands_and_responses_keep_the_session_generation() {
    let (tx, rx) = mpsc::sync_channel(128);
    let session = WorkerSession::spawn_with_command(
        7,
        python("import sys\nfor line in sys.stdin:\n print(line, end='', flush=True)"),
        tx,
    );
    session.try_send(json!({"type":"ping"})).unwrap();
    assert!(matches!(receive(&rx), WorkerEventKind::Message(v) if v == json!({"type":"ping"})));
    session.stop();
    stopped(&rx);
}

#[test]
fn blocked_stdin_has_bounded_admission_and_cannot_block_stop() {
    let (tx, rx) = mpsc::sync_channel(128);
    let session = WorkerSession::spawn_with_command(
        7,
        python("import time\nprint('{\"type\":\"waiting\"}', flush=True)\ntime.sleep(30)"),
        tx,
    );
    assert!(matches!(receive(&rx), WorkerEventKind::Message(_)));
    let now = Instant::now();
    let payload = json!({"type":"test", "data":"x".repeat(128 * 1024)});
    let mut rejected = false;
    for _ in 0..100 {
        if let Err(error) = session.try_send(payload.clone()) {
            assert_eq!(error.kind(), std::io::ErrorKind::WouldBlock);
            rejected = true;
            break;
        }
    }
    assert!(
        rejected,
        "an unread pipe must not admit an unbounded command queue"
    );
    assert!(
        now.elapsed() < Duration::from_secs(1),
        "enqueue blocked the caller"
    );
    session.stop();
    stopped(&rx);
}

#[test]
fn long_protocol_results_are_not_mistaken_for_diagnostic_noise() {
    let (tx, rx) = mpsc::sync_channel(128);
    let session = WorkerSession::spawn_with_command(
        7,
        python(
            r#"
import json, time
print(json.dumps({'type':'file_completed','file':'/long.wav','result':{'success':True,'saved_files':['/long.txt'],'utterances':[{'text':'а'*70000}]}}), flush=True)
print(json.dumps({'type':'llm_completed','success':True,'saved_files':['/summary.txt'],'results':[{'mode':'summary','text':'б'*70000}]}), flush=True)
print(json.dumps({'type':'inputs_resolved','request_id':1,'files':['/recordings/'+str(i)+'-'+'x'*80+'.wav' for i in range(2000)],'duplicates':[],'errors':[],'cancelled':False}), flush=True)
time.sleep(30)
"#,
        ),
        tx,
    );
    let first = receive(&rx);
    let second = receive(&rx);
    let third = receive(&rx);
    session.stop();
    stopped(&rx);
    assert!(
        matches!(first, WorkerEventKind::Message(ref v) if v["type"] == "file_completed" && v["result"]["saved_files"][0] == "/long.txt"),
        "valid long file result must remain protocol"
    );
    assert!(
        matches!(second, WorkerEventKind::Message(ref v) if v["type"] == "llm_completed" && v["results"][0]["text"].as_str().unwrap().chars().count() == 70000),
        "valid long LLM result must remain protocol"
    );
    assert!(
        matches!(third, WorkerEventKind::Message(ref v) if v["type"] == "inputs_resolved" && v["files"].as_array().unwrap().len() == 2000)
    );
}

#[test]
fn oversized_protocol_fails_explicitly_instead_of_leaving_activity_hanging() {
    let (tx, rx) = mpsc::sync_channel(128);
    let session = WorkerSession::spawn_with_command(
        7,
        python(
            r#"
import sys, time
sys.stdout.write('{"type":"llm_completed","text":"' + 'x' * (9 * 1024 * 1024) + '"}\n')
sys.stdout.flush()
time.sleep(30)
"#,
        ),
        tx,
    );
    let event = receive(&rx);
    session.stop();
    let remaining = stopped(&rx);
    assert!(std::iter::once(&event).chain(remaining.iter()).any(|e| matches!(e, WorkerEventKind::Failed(reason) if reason.contains("protocol") && reason.contains("limit"))));
}

#[test]
fn diagnostics_are_bounded_safe_and_do_not_hide_later_messages() {
    let (tx, rx) = mpsc::sync_channel(128);
    let session = WorkerSession::spawn_with_command(
        7,
        python(
            r#"
import os, time
os.write(2, b'\x1b[31mwarning\x1b[0m\x1b]0;bad title\x07\r\x00\n')
os.write(1, b'not json\n')
os.write(2, b'\xffbroken\n')
os.write(2, b'x' * 100000 + b'\n')
os.write(1, b'{"type":"after"}\n')
time.sleep(30)
"#,
        ),
        tx,
    );
    let mut messages = Vec::new();
    let mut after = false;
    while messages.len() < 4 || !after {
        match receive(&rx) {
            WorkerEventKind::Diagnostic(s) => messages.push(s),
            WorkerEventKind::Message(v) => after |= v["type"] == "after",
            other => panic!("unexpected {other:?}"),
        }
    }
    assert!(messages
        .iter()
        .any(|s| s.contains("warning") && !s.contains("bad title")));
    assert!(messages.iter().any(|s| s.contains("not json")));
    assert!(messages
        .iter()
        .any(|s| s.contains('\u{fffd}') && s.contains("broken")));
    assert!(messages.iter().any(|s| s.contains("truncated")));
    assert!(messages.iter().all(|s| s.len() <= 65536 + 128));
    assert!(messages.iter().all(|s| !s.chars().any(char::is_control)));
    session.stop();
    stopped(&rx);
}

#[test]
fn exit_and_stdout_eof_report_one_failure_then_reap() {
    for script in [
        "raise SystemExit(3)",
        "import os,time\nos.close(1)\ntime.sleep(30)",
    ] {
        let (tx, rx) = mpsc::sync_channel(128);
        let _session = WorkerSession::spawn_with_command(7, python(script), tx);
        let events = stopped(&rx);
        assert_eq!(
            events
                .iter()
                .filter(|e| matches!(e, WorkerEventKind::Failed(_)))
                .count(),
            1
        );
    }
}

#[test]
fn broken_pipe_is_reported_without_logging_the_command() {
    let (tx, rx) = mpsc::sync_channel(128);
    let session = WorkerSession::spawn_with_command(7, python(
        "import os,time\nos.close(0)\nprint('{\"type\":\"waiting\"}', flush=True)\ntime.sleep(30)"), tx);
    assert!(matches!(receive(&rx), WorkerEventKind::Message(_)));
    session
        .try_send(json!({"type":"test", "secret":"private-api-key"}))
        .unwrap();
    let events = stopped(&rx);
    assert!(events
        .iter()
        .any(|e| matches!(e, WorkerEventKind::Failed(s) if s.contains("stdin"))));
    assert!(events
        .iter()
        .all(|e| !format!("{e:?}").contains("private-api-key")));
}

#[test]
fn a_full_event_queue_does_not_prevent_stop() {
    let (tx, rx) = mpsc::sync_channel(1);
    let session = WorkerSession::spawn_with_command(
        7,
        python("import os\nwhile True: os.write(2, b'noise\\n')"),
        tx,
    );
    assert!(matches!(receive(&rx), WorkerEventKind::Diagnostic(_)));
    // The stop path must be independent of a reader waiting on backpressure.
    std::thread::sleep(Duration::from_millis(100));
    session.stop();
    stopped(&rx);
}

#[test]
fn worker_exit_and_explicit_stop_terminate_grandchildren_holding_pipes() {
    for leave_first in [false, true] {
        let (tx, rx) = mpsc::sync_channel(128);
        let session = WorkerSession::spawn_with_command(
            7,
            python(
                r#"
import json, os, subprocess, sys, time
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
print(json.dumps({'type':'tree', 'parent':os.getpid(), 'child':child.pid}), flush=True)
sys.stdin.readline()
"#,
            ),
            tx,
        );
        let WorkerEventKind::Message(tree) = receive(&rx) else {
            panic!("missing tree")
        };
        if leave_first {
            session.try_send(json!({"type":"exit"})).unwrap();
        } else {
            session.stop();
        }
        stopped(&rx);
        for key in ["parent", "child"] {
            assert!(
                !running(tree[key].as_u64().unwrap() as u32),
                "{key} still running"
            );
        }
    }
}

#[cfg(unix)]
fn running(pid: u32) -> bool {
    let output = Command::new("ps")
        .args(["-o", "stat=", "-p", &pid.to_string()])
        .output()
        .unwrap();
    let state = String::from_utf8_lossy(&output.stdout);
    !state.trim().is_empty() && !state.trim().starts_with('Z')
}

#[cfg(windows)]
fn running(pid: u32) -> bool {
    use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
    use windows_sys::Win32::System::Threading::{
        GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
    };
    let handle = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) };
    if handle.is_null() {
        return false;
    }
    let handle = unsafe { OwnedHandle::from_raw_handle(handle) };
    let mut code = 0;
    assert_ne!(
        unsafe { GetExitCodeProcess(handle.as_raw_handle(), &mut code) },
        0
    );
    code == 259 // STILL_ACTIVE
}

#[test]
fn dropping_session_cleans_up_without_a_stop_command() {
    let (tx, rx) = mpsc::sync_channel(1);
    let session = WorkerSession::spawn_with_command(7, python(
        "import json,os,time\nprint(json.dumps({'type':'pid','pid':os.getpid()}), flush=True)\ntime.sleep(30)"), tx);
    let WorkerEventKind::Message(value) = receive(&rx) else {
        panic!("missing pid")
    };
    let pid = value["pid"].as_u64().unwrap() as u32;
    assert!(running(pid));
    drop(session);
    let deadline = Instant::now() + Duration::from_secs(5);
    while running(pid) {
        assert!(
            Instant::now() < deadline,
            "dropped session left its worker alive"
        );
        std::thread::sleep(Duration::from_millis(20));
    }
}

#[test]
fn spawn_failure_is_terminal_and_sending_is_rejected() {
    let (tx, rx) = mpsc::sync_channel(128);
    let session =
        WorkerSession::spawn_with_command(7, Command::new("/definitely/missing/worker"), tx);
    let events = stopped(&rx);
    assert!(matches!(events.as_slice(), [WorkerEventKind::Failed(_)]));
    assert!(session.try_send(json!({"type":"ping"})).is_err());
}

#[test]
fn startup_traceback_survives_an_immediate_exit() {
    let (tx, rx) = mpsc::sync_channel(1);
    let session = WorkerSession::spawn_with_command(7, python(
        "import os,sys\nprint('{\"type\":\"waiting\"}', flush=True)\nsys.stdin.readline()\nos.write(2, b'loading\\n' * 300 + b'ModuleNotFoundError: missing_backend\\n')\nos._exit(2)"), tx);
    assert!(matches!(receive(&rx), WorkerEventKind::Message(_)));
    session.try_send(json!({"type":"exit"})).unwrap();
    // Deliberately keep the event queue full as the worker exits.
    std::thread::sleep(Duration::from_millis(100));
    let events = stopped(&rx);
    assert!(events.iter().any(|event| matches!(event,
        WorkerEventKind::Diagnostic(message) if message.contains("ModuleNotFoundError: missing_backend"))));
}

#[test]
fn line_reader_preserves_split_unicode_and_the_final_unterminated_line() {
    let mut reader = std::io::BufReader::with_capacity(1, "Файл\nещё".as_bytes());
    let (first, truncated) = bounded_line(&mut reader, MAX_LINE).unwrap().unwrap();
    assert_eq!(String::from_utf8(first).unwrap(), "Файл");
    assert!(!truncated);
    let (last, truncated) = bounded_line(&mut reader, MAX_LINE).unwrap().unwrap();
    assert_eq!(String::from_utf8(last).unwrap(), "ещё");
    assert!(!truncated);
    assert!(bounded_line(&mut reader, MAX_LINE).unwrap().is_none());
}
