import io
import json
import subprocess
import sys

from src.tui_worker import TuiWorker


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

    assert _messages(output) == [{"type": "error", "message": "No LLM request is running"}]


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
        {"type": "error", "message": "Processing is already running"},
        {"type": "error", "message": "Processing is already running"},
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
