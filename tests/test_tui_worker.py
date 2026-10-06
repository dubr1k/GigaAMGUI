import io
import json
import os
import subprocess
import sys

import pytest

from src.tui_worker import TuiWorker

# fcntl/O_NONBLOCK and select() on pipes are POSIX-only; build.yml runs this file
# on windows-latest too, where these two pipe-level regressions cannot be reproduced.
posix_pipes_only = pytest.mark.skipif(sys.platform == "win32", reason="needs fcntl and select() on pipes")


def test_frozen_native_worker_entrypoint_replies_to_ping():
    result = subprocess.run(
        [sys.executable, "app.py", "--native-worker"],
        input='{"type":"ping"}\n',
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {"type": "pong"}


def _messages(output):
    return [json.loads(line) for line in output.getvalue().splitlines()]


def test_tui_worker_hello_is_lightweight():
    result = subprocess.run(
        [sys.executable, "-c", """
import io, json, sys
from src.tui_worker import TuiWorker
out = io.StringIO()
worker = TuiWorker(out)
worker.handle({'type': 'hello'})
worker.handle({'type': 'ping'})
worker.close()
values = [json.loads(line) for line in out.getvalue().splitlines()]
assert values == [
    {'type': 'ready', 'protocol_version': 1,
     'capabilities': ['resolve_inputs', 'asr', 'llm', 'compact_completed']},
    {'type': 'pong'},
], values
assert not {'torch', 'gigaam', 'pyannote.audio', 'mlx'}.intersection(sys.modules)
"""],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("interactive", [False, True])
def test_only_interactive_tui_negotiates_compact_asr_completion(interactive):
    output = io.StringIO()
    worker = TuiWorker(output)
    try:
        if interactive:
            worker.handle({"type": "hello", "client": "tui"})
        result = {"file_path": "/long.wav", "success": True, "saved_files": ["/long.txt"],
                  "utterances": [{"text": "длинная запись " * 10000}]}
        worker.emit("file_completed", file="/long.wav", result=result)
        worker.emit("completed", success=True, results=[result])
        messages = _messages(output)[-2:]
        assert messages[0]["result"]["saved_files"] == ["/long.txt"]
        assert messages[1]["results"][0]["success"]
        for summary in (messages[0]["result"], messages[1]["results"][0]):
            assert ("utterances" in summary) is not interactive
        assert "utterances" in result, "emit must not mutate the processor result"
    finally:
        worker.close()


@pytest.mark.parametrize(("hello", "compact_file", "compact_completed"), [
    (None, False, False),  # headless and old clients: the full contract
    ({"type": "hello", "client": "tui"}, True, True),
    # Liquid takes full results from file_completed; only `completed` repeated
    # every result with its word timings and outgrew the 8 MiB line limit.
    ({"type": "hello", "client": "liquid", "features": ["compact_completed"]}, False, True),
    ({"type": "hello", "client": "liquid"}, False, False),
    ({"type": "hello", "client": "liquid", "features": "compact_completed"}, False, False),
    ({"type": "hello", "client": "liquid", "features": [None, 7, "compact_completed"]}, False, True),
])
def test_hello_negotiates_which_asr_events_are_compact(hello, compact_file, compact_completed):
    output = io.StringIO()
    worker = TuiWorker(output)
    try:
        if hello is not None:
            worker.handle(hello)
            assert _messages(output)[-1] == {
                "type": "ready", "protocol_version": 1, "capabilities": ["resolve_inputs", "asr", "llm", "compact_completed"],
            }
        result = {"file_path": "/long.wav", "success": True, "saved_files": ["/long.txt"],
                  "utterances": [{"text": "длинная запись"}]}
        worker.emit("file_completed", file="/long.wav", result=result)
        worker.emit("completed", success=True, results=[result])
        file_completed, completed = _messages(output)[-2:]
        assert ("utterances" in file_completed["result"]) is not compact_file
        assert ("utterances" in completed["results"][0]) is not compact_completed
        assert completed["results"][0]["saved_files"] == ["/long.txt"]
        assert "utterances" in result, "emit must not mutate the processor result"
    finally:
        worker.close()


@posix_pipes_only
def test_worker_survives_a_child_that_makes_stdin_non_blocking(tmp_path):
    """`pi --version` (probed by llm_tools) sets O_NONBLOCK on its inherited stdin —
    the flag lives on the shared open-file description, so the worker's own pipe
    turned non-blocking, `for line in sys.stdin` got EAGAIN, Python reported EOF and
    the worker exited 0 while the TUI still held the pipe («Worker exited»)."""
    import os

    # Drive the worker's own reader with a pipe whose read end is flipped to
    # non-blocking mid-stream, exactly what a probed CLI does to the shared fd.
    reader = tmp_path / "reader.py"
    reader.write_text(
        "import fcntl, json, os, sys, threading, time\n"
        "from src.tui_worker import read_commands\n"
        "r, w = os.pipe()\n"
        "def writer():\n"
        "    os.write(w, b'{\"type\":\"ping\"}\\n')\n"
        "    time.sleep(0.2)\n"
        "    fl = fcntl.fcntl(r, fcntl.F_GETFL); fcntl.fcntl(r, fcntl.F_SETFL, fl | os.O_NONBLOCK)\n"
        "    time.sleep(0.4)\n"
        "    os.write(w, b'{\"type\":\"ping\"}\\n')\n"
        "    time.sleep(0.2)\n"
        "    os.close(w)\n"
        "threading.Thread(target=writer, daemon=True).start()\n"
        "got = [c['type'] for c in read_commands(os.fdopen(r, 'rb', buffering=0))]\n"
        "print(json.dumps(got))\n"
    )
    result = subprocess.run([sys.executable, str(reader)], capture_output=True, text=True, timeout=30, env={**os.environ, "PYTHONPATH": "."})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip()) == ["ping", "ping"], result.stdout


@posix_pipes_only
def test_worker_answers_while_stdin_stays_open():
    """The TUI keeps the pipe open for the whole session; the worker must answer
    each line as it arrives (a BufferedReader.read(n) would wait for n bytes/EOF)."""
    import os

    worker = subprocess.Popen(
        [sys.executable, "-m", "src.tui_worker"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        env={**os.environ, "PYTHONPATH": "."},
    )
    try:
        import select

        worker.stdin.write('{"type":"ping"}\n')
        worker.stdin.flush()
        ready, _, _ = select.select([worker.stdout], [], [], 20)
        assert ready, "worker did not answer within 20 s while stdin stayed open"
        assert json.loads(worker.stdout.readline()) == {"type": "pong"}
        worker.stdin.write('{"type":"ping"}\n')
        worker.stdin.flush()
        ready, _, _ = select.select([worker.stdout], [], [], 20)
        assert ready
        assert json.loads(worker.stdout.readline()) == {"type": "pong"}
        assert worker.poll() is None, "worker exited although stdin is still open"
    finally:
        worker.kill()
        worker.wait(timeout=10)


def test_cli_probe_does_not_share_the_worker_stdin(monkeypatch):
    """Children of the worker must get /dev/null as stdin: an inherited pipe lets a
    CLI change the worker's own stdin flags (see the non-blocking test above)."""
    from src.services import cli_tools

    captured = {}

    def fake_run(command, **kwargs):
        captured["stdin"] = kwargs.get("stdin")
        return subprocess.CompletedProcess(command, 0, "1.2.3", "")

    monkeypatch.setattr(cli_tools.subprocess, "run", fake_run)
    monkeypatch.setattr(cli_tools, "locate_tool", lambda spec, override=None: "/x/claude")
    status = cli_tools.resolve_tool(cli_tools.provider_by_name("Claude Code"))
    assert status.status == "found"
    assert captured["stdin"] == subprocess.DEVNULL


def test_run_command_uses_devnull_without_input(monkeypatch):
    """llm_service._run_command must never hand the worker's own stdin to a CLI:
    /dev/null when there is no prompt, a private pipe when there is one."""
    from src.services import llm_service

    captured = []

    def fake_run(command, **kwargs):
        captured.append(("run", kwargs.get("stdin"), kwargs.get("input")))
        return subprocess.CompletedProcess(command, 0, "", "")

    class FakeStdin:
        def write(self, text):
            captured.append(("stdin", None, text))

        def close(self):
            pass

    class FakePopen:
        returncode = 0
        pid = 424242

        def __init__(self, command, **kwargs):
            captured.append(("popen", kwargs.get("stdin"), None))
            self.stdin = FakeStdin() if kwargs.get("stdin") == subprocess.PIPE else None

        def communicate(self, input=None, timeout=None):
            return "", ""

    class FakeProcess:
        def __init__(self, pid):
            pass

        def children(self, recursive=False):
            return []

    monkeypatch.setattr(llm_service.subprocess, "run", fake_run)
    monkeypatch.setattr(llm_service.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(llm_service.psutil, "Process", FakeProcess)

    llm_service._run_command(["tool"])
    llm_service._run_command(["tool"], input_text="prompt")
    llm_service._run_command(["tool"], cancel_check=lambda: False)
    llm_service._run_command(["tool"], input_text="prompt", cancel_check=lambda: False)

    assert captured == [
        ("run", subprocess.DEVNULL, None),
        ("run", None, "prompt"),
        ("popen", subprocess.DEVNULL, None),
        ("popen", subprocess.PIPE, None),
        ("stdin", None, "prompt"),
    ]


def test_tui_worker_replies_to_ping():
    output = io.StringIO()
    worker = TuiWorker(output=output)

    worker.handle({"type": "ping"})

    assert _messages(output) == [{"type": "pong"}]


def test_tui_worker_rejects_empty_batch():
    output = io.StringIO()
    worker = TuiWorker(output=output)

    worker.handle({"type": "start", "files": []})

    assert _messages(output)[0]["type"] == "error"
    assert _messages(output)[0]["message"] == "No input files supplied"


def test_tui_worker_rejects_colliding_output_stems(tmp_path):
    first = tmp_path / "a" / "same.wav"
    second = tmp_path / "b" / "SAME.mp3"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes(b"wav")
    second.write_bytes(b"mp3")
    output = io.StringIO()

    TuiWorker(output=output).handle({
        "type": "start",
        "files": [str(first), str(second)],
        "output_dir": str(tmp_path / "out"),
    })

    message = _messages(output)[0]
    assert message["type"] == "error"
    assert "overwrite" in message["message"]


def test_tui_worker_routes_llm_cancel_without_job():
    output = io.StringIO()
    worker = TuiWorker(output=output)

    worker.handle({"type": "llm_cancel"})

    assert _messages(output) == [
        {"type": "error", "message": "No LLM request is running", "command": "llm_cancel"},
    ]


@pytest.mark.parametrize(("command", "answered"), [
    ({"type": "cancel"}, "cancel"),  # the late reply that used to reject a new start
    ({"type": "start", "files": []}, "start"),
    ({"type": "llm_cancel"}, "llm_cancel"),
    ({"type": "llm_tool_check", "provider": "Nope"}, "llm_tool_check"),
    ({"type": "bogus"}, "bogus"),
])
def test_command_errors_name_the_command_they_answer(command, answered):
    """A late "Nothing is being processed" (the reply to `cancel`) arrived while
    the TUI was starting the next batch and was taken as that start's rejection:
    the TUI went idle and ignored the `started` that followed."""
    output = io.StringIO()
    worker = TuiWorker(output=output)
    try:
        worker.handle(command)
        error = _messages(output)[-1]
        assert error["type"] == "error"
        assert error["command"] == answered
        worker.emit("error", message="from a background thread")
        assert "command" not in _messages(output)[-1], "only replies to a command are tagged"
    finally:
        worker.close()


def test_batch_failure_before_started_is_tagged_as_the_start(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def failing_import(name, *args, **kwargs):
        if name == "src.core.model_loader":
            raise ModuleNotFoundError("No module named 'torch'", name="torch")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", failing_import)
    output = io.StringIO()
    worker = TuiWorker(output=output)
    worker._run_batch(["/a.wav"], "", ["txt"], False, "pyannote", None, "auto",
                      "v3_e2e_rnnt", "auto", "auto", True, 2, 64)
    error, completed = _messages(output)[-2:]
    assert error["type"] == "error" and error["command"] == "start"
    assert "torch" in error["message"] and "traceback" in error
    assert completed["type"] == "completed"


def test_tui_worker_routes_live_commands_to_service():
    output = io.StringIO()
    worker = TuiWorker(output=output)

    worker.handle({"type": "live_stop"})
    worker.handle({"type": "live_audio", "source": "mic", "seq": 0, "sample_offset": 0, "timestamp_ns": 0, "pcm": ""})
    worker.handle({"type": "live_ask", "question": "?", "settings": {}})

    assert [m["message"] for m in _messages(output)] == [
        "No live session is running",
        "No live session is running",
        "No live session is running",
    ]


def test_tui_worker_batch_start_is_rejected_while_live_session_runs(tmp_path):
    class FakeLive:
        def is_running(self):
            return True

    output = io.StringIO()
    worker = TuiWorker(output=output)
    worker._live = FakeLive()
    sample = tmp_path / "a.wav"
    sample.write_bytes(b"x")

    worker.handle({"type": "start", "files": [str(sample)]})
    worker.handle({"type": "llm_start", "text": "t", "modes": ["summary"], "settings": {}})

    assert _messages(output) == [
        {"type": "error", "message": "Processing is already running", "command": "start"},
        {"type": "error", "message": "Processing is already running", "command": "llm_start"},
    ]


def test_tui_worker_rejects_unknown_command():
    output = io.StringIO()
    worker = TuiWorker(output=output)

    worker.handle({"type": "unknown"})

    assert _messages(output)[0]["message"] == "Unknown command: 'unknown'"


def test_tui_worker_forwards_onnx_provider(tmp_path, monkeypatch):
    sample = tmp_path / "sample.wav"
    sample.write_bytes(b"wav")
    captured = {}

    class FakeThread:
        def __init__(self, *, target, args, daemon):
            captured["target"] = target
            captured["args"] = args

        def start(self):
            return None

        def is_alive(self):
            return False

    monkeypatch.setattr("src.tui_worker.threading.Thread", FakeThread)
    worker = TuiWorker(output=io.StringIO())
    worker.handle({
        "type": "start",
        "files": [str(sample)],
        "backend": "onnx",
        "onnx_provider": "cuda",
        "audio_preprocessing_mode": "denoise",
        "subtitle_sentence_split": False,
        "subtitle_max_lines": 3,
        "subtitle_max_width": 72,
    })

    assert captured["args"][-7:-4] == ("onnx", "v3_e2e_rnnt", "cuda")
    assert captured["args"][-4:] == ("denoise", False, 3, 72)


def test_tui_worker_defaults_preprocessing_to_config(tmp_path, monkeypatch):
    sample = tmp_path / "sample.wav"
    sample.write_bytes(b"wav")
    captured = {}

    class FakeThread:
        def __init__(self, *, target, args, daemon):
            captured["args"] = args

        def start(self):
            return None

        def is_alive(self):
            return False

    monkeypatch.setattr("src.tui_worker.threading.Thread", FakeThread)
    monkeypatch.setattr("src.tui_worker.AUDIO_PREPROCESSING_MODE", "light")

    TuiWorker(output=io.StringIO()).handle({"type": "start", "files": [str(sample)]})

    assert captured["args"][-4] == "light"


def test_tui_worker_rejects_explicit_invalid_preprocessing_mode(tmp_path):
    sample = tmp_path / "sample.wav"
    sample.write_bytes(b"wav")
    output = io.StringIO()

    TuiWorker(output=output).handle({
        "type": "start",
        "files": [str(sample)],
        "audio_preprocessing_mode": "invalid",
    })

    message = _messages(output)[0]
    assert message["type"] == "error"
    assert message["message"] == "Unknown audio preprocessing mode: 'invalid'"


def test_tui_worker_rejects_invalid_subtitle_limits(tmp_path):
    sample = tmp_path / "sample.wav"
    sample.write_bytes(b"wav")
    output = io.StringIO()

    TuiWorker(output=output).handle({
        "type": "start",
        "files": [str(sample)],
        "subtitle_max_lines": 0,
    })

    assert _messages(output)[0] == {
        "type": "error",
        "message": "max_line_count должен быть от 1 до 4",
        "command": "start",
    }


def test_tui_worker_rejects_non_integer_subtitle_limits(tmp_path):
    sample = tmp_path / "sample.wav"
    sample.write_bytes(b"wav")
    output = io.StringIO()

    TuiWorker(output=output).handle({
        "type": "start",
        "files": [str(sample)],
        "subtitle_max_lines": True,
    })

    message = _messages(output)[0]
    assert message["type"] == "error"
    assert "max_line_count" in message["message"]


def test_tui_worker_lists_llm_tools(monkeypatch):
    from src.services import cli_tools

    fake = [cli_tools.ToolStatus("omp", "oh-my-pi", "found", "/opt/homebrew/bin/omp", "18.2.5", None, "brew …")]
    captured = {}

    def fake_scan(overrides=None, *, fresh=False):
        captured["overrides"] = overrides
        captured["fresh"] = fresh
        return fake

    monkeypatch.setattr(cli_tools, "scan", fake_scan)
    output = io.StringIO()
    worker = TuiWorker(output=output)

    worker.handle({"type": "llm_tools", "overrides": {"omp": "/x/omp"}, "fresh": True})

    message = _messages(output)[0]
    assert message["type"] == "llm_tools"
    assert message["providers"] == ["API", "Claude Code", "Codex", "OpenCode", "Pi", "oh-my-pi", "Other"]
    assert message["tools"] == [fake[0].to_dict()]
    assert captured == {"overrides": {"omp": "/x/omp"}, "fresh": True}


def test_tui_worker_checks_one_llm_tool(monkeypatch):
    from src.services import cli_tools

    captured = {}

    def fake_resolve(spec, override=None):
        captured["spec"] = spec.id
        captured["override"] = override
        return cli_tools.ToolStatus(spec.id, spec.name, "broken", override, None, "exit 1", spec.install_hint)

    monkeypatch.setattr(cli_tools, "resolve_tool", fake_resolve)
    output = io.StringIO()
    worker = TuiWorker(output=output)

    worker.handle({"type": "llm_tool_check", "provider": "Claude Code", "path": "/x/claude"})

    message = _messages(output)[0]
    assert message["type"] == "llm_tool_check"
    assert message["tool"]["status"] == "broken"
    assert captured == {"spec": "claude", "override": "/x/claude"}


def test_tui_worker_llm_tool_check_unknown_provider():
    output = io.StringIO()
    worker = TuiWorker(output=output)

    worker.handle({"type": "llm_tool_check", "provider": "Nope"})

    assert _messages(output)[0]["type"] == "error"


@pytest.mark.parametrize("locale_encoding", ["cp1252", "cp1251"])
def test_worker_protocol_is_utf8_whatever_the_locale_encoding(locale_encoding):
    """Windows gives a piped stdout the ANSI code page. cp1251 wrote Cyrillic as
    bytes the clients could not parse as UTF-8 (the message became a diagnostic,
    the TUI waited forever); cp1252 raised UnicodeEncodeError and no reply left
    at all. Simulated here by forcing the locale encoding of the worker process."""
    environment = {**os.environ, "PYTHONIOENCODING": locale_encoding, "PYTHONUTF8": "0"}
    result = subprocess.run(
        [sys.executable, "-m", "src.tui_worker"],
        input='{"type": "запись"}\n{"type": "ping"}\n'.encode(),
        capture_output=True, env=environment, timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    messages = [json.loads(line) for line in result.stdout.decode("utf-8").splitlines()]
    assert messages[0]["type"] == "error"
    assert "запись" in messages[0]["message"]
    assert messages[-1] == {"type": "pong"}


def test_emit_never_writes_invalid_json_or_unencodable_text():
    """`json.dumps` writes NaN/Infinity by default, which no strict parser accepts:
    the client dropped the whole line — a `completed` with a NaN duration left the
    batch running forever. A non-UTF-8 file name (surrogateescape) made the write
    itself raise, so the reply never left."""
    from pathlib import Path

    raw = io.BytesIO()
    output = io.TextIOWrapper(raw, encoding="utf-8")
    worker = TuiWorker(output)
    try:
        worker.emit("progress", file_progress=float("nan"), total_seconds=float("inf"),
                    file="/tmp/\udcff.wav", stage=Path("/x"), nested={"values": [float("-inf"), 1.5]})
        worker.emit("pong")
    finally:
        worker.close()
    output.flush()

    def reject(constant):
        raise AssertionError(f"non-JSON constant {constant}")

    lines = raw.getvalue().decode("utf-8").splitlines()
    progress = json.loads(lines[0], parse_constant=reject)
    assert progress["file_progress"] is None and progress["total_seconds"] is None
    assert progress["file"] == "/tmp/�.wav"
    assert progress["stage"] == str(Path("/x"))  # «\x» на Windows
    assert progress["nested"] == {"values": [None, 1.5]}
    assert json.loads(lines[1]) == {"type": "pong"}


# ---------- пакет на подделках: _run_batch без модели и процессора ----------


class _FakeBatchLoader:
    def __init__(self, **_kwargs):
        pass

    def load_model(self, logger=None):
        return True


@pytest.fixture
def fake_batch(monkeypatch):
    """`_run_batch` с поддельными загрузчиком, процессором и статистикой.

    Поведение файла задаёт тест: ``fake_batch.process(kwargs, progress_callback)``.
    """
    import types

    from src.core import model_loader
    from src.services import transcription_service
    from src.utils import processing_stats

    state = types.SimpleNamespace(calls=[], process=None, callback=None)

    class Processor:
        def process_file(self, **kwargs):
            state.calls.append(kwargs)
            return state.process(kwargs, state.callback)

    def build_processor(_loader, _stats, *, logger=None, progress_callback=None, **_kwargs):
        state.callback = progress_callback
        return Processor()

    monkeypatch.setattr(model_loader, "ModelLoader", _FakeBatchLoader)
    monkeypatch.setattr(transcription_service, "build_processor", build_processor)
    monkeypatch.setattr(processing_stats, "ProcessingStats",
                        lambda *_a, **_k: types.SimpleNamespace(add_processing_record=lambda **_kw: None))
    return state


def _ok(kwargs):
    return {"file_path": kwargs["filepath"], "success": True, "error": None, "saved_files": [], "media_duration": 0}


def _run_fake_batch(worker, files):
    worker._run_batch(files, "", ["txt"], False, "pyannote", None, "auto", "v3_e2e_rnnt", "auto", "auto", True, 2, 64)


def test_batch_progress_line_keeps_its_wire_format(fake_batch):
    """`progress` разбирают TUI (serde) и Liquid: те же поля в том же порядке."""
    from src.core.progress import ProgressEvent

    def process(kwargs, callback):
        callback(ProgressEvent("transcription", 0.5, 0.42, 10.0, 20.0))
        return _ok(kwargs)

    fake_batch.process = process
    output = io.StringIO()
    _run_fake_batch(TuiWorker(output=output), ["/a.wav"])

    line = next(text for text in output.getvalue().splitlines() if text.startswith('{"type": "progress"'))
    assert line == (
        '{"type": "progress", "file": "/a.wav", "file_index": 0, "total_files": 1, "stage": "transcription", '
        '"stage_progress": 0.5, "file_progress": 0.42, "processed_seconds": 10.0, "total_seconds": 20.0, '
        '"message": null}'
    )


def test_cancel_interrupts_the_current_file_instead_of_failing_it(fake_batch):
    """Процессор умеет прерываться посреди файла (cancel_check). Прерванный файл —
    остановка, а не сбой: без `file_completed` с «ошибкой» и без строки в results;
    TUI и Liquid считают такой файл прерванным (started без file_completed)."""
    output = io.StringIO()
    worker = TuiWorker(output=output)

    def process(kwargs, _callback):
        worker._cancel_requested.set()  # пользователь нажал «Отмена» посреди файла
        cancel_check = kwargs.get("cancel_check")
        if cancel_check is None or not cancel_check():
            return _ok(kwargs)
        return {"file_path": kwargs["filepath"], "success": False, "cancelled": True,
                "error": "Обработка отменена пользователем", "saved_files": []}

    fake_batch.process = process
    _run_fake_batch(worker, ["/a.wav", "/b.wav"])

    messages = _messages(output)
    assert [call["filepath"] for call in fake_batch.calls] == ["/a.wav"]
    assert not [m for m in messages if m["type"] == "file_completed"]
    assert [m["file"] for m in messages if m["type"] == "file_started"] == ["/a.wav"]
    completed = messages[-1]
    assert completed["type"] == "completed"
    assert completed["cancelled"] is True and completed["success"] is False
    assert completed["results"] == []


def test_cancel_reply_says_the_current_file_is_interrupted():
    import types

    output = io.StringIO()
    worker = TuiWorker(output=output)
    worker._task = types.SimpleNamespace(is_alive=lambda: True)

    worker.handle({"type": "cancel"})

    reply = _messages(output)[-1]
    assert reply["type"] == "cancelling"
    assert "прерываем текущий файл" in reply["message"]
    assert worker._cancel_requested.is_set()
