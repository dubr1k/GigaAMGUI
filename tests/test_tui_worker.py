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
