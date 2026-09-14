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
