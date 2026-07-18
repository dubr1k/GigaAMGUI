import io
import json

from src.tui_worker import TuiWorker


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
    })

    assert captured["args"][-3:] == ("onnx", "v3_e2e_rnnt", "cuda")
