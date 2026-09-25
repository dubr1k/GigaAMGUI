import io
import json
import threading

from src.services.llm_worker_service import LLMWorkerService


class _InlineThread:
    """Run the target synchronously so tests need no joins."""

    def __init__(self, *, target, args=(), daemon=True):
        self._target, self._args = target, args
        self._alive = False

    def start(self):
        self._alive = True
        try:
            self._target(*self._args)
        finally:
            self._alive = False

    def is_alive(self):
        return self._alive


def _service(run_provider):
    output = io.StringIO()

    def emit(message_type, **payload):
        output.write(json.dumps({"type": message_type, **payload}, ensure_ascii=False) + "\n")

    return LLMWorkerService(emit, run_provider=run_provider, thread_factory=_InlineThread), output


def _messages(output):
    return [json.loads(line) for line in output.getvalue().splitlines()]


SETTINGS = {"provider": "API", "api_url": "http://x", "api_key": "k", "model": "m", "temperature": 0.2}


def test_llm_start_accepts_inline_text_and_streams_chunks(tmp_path):
    def run_provider(settings, transcript, prompt, *, provider, strict_empty_cli, on_stream_chunk=None, cancel_check=None):
        assert "hello world" in transcript
        on_stream_chunk("Sum")
        on_stream_chunk("mary")
        return "Summary"

    service, output = _service(run_provider)
    service.start({"type": "llm_start", "text": "hello world", "modes": ["summary"], "settings": SETTINGS, "output_dir": str(tmp_path)})

    messages = _messages(output)
    assert messages[0] == {"type": "llm_started", "mode": "summary", "index": 1, "total": 1}
    assert [m["text"] for m in messages if m["type"] == "llm_chunk"] == ["Sum", "mary"]
    done = messages[-1]
    assert done["type"] == "llm_completed" and done["success"] is True
    assert done["results"] == [{"mode": "summary", "text": "Summary"}]
    assert (tmp_path / "session_llm_summary.txt").read_text(encoding="utf-8") == "Summary"


def test_llm_start_requires_text_or_existing_files(tmp_path):
    service, output = _service(lambda *a, **k: "x")
    service.start({"type": "llm_start", "files": [str(tmp_path / "missing.txt")], "modes": ["summary"], "settings": SETTINGS})
    assert _messages(output) == [{"type": "error", "message": "LLM transcript file does not exist"}]

    service, output = _service(lambda *a, **k: "x")
    service.start({"type": "llm_start", "modes": ["summary"], "settings": SETTINGS})
    assert _messages(output)[0]["type"] == "error"


def test_llm_custom_mode_requires_prompt():
    service, output = _service(lambda *a, **k: "x")
    service.start({"type": "llm_start", "text": "t", "modes": ["custom"], "settings": SETTINGS})
    assert _messages(output) == [{"type": "error", "message": "Custom LLM prompt is required"}]


def test_llm_cancel_reports_cancelled_completion(tmp_path):
    holder = {}
    seen = threading.Event()

    def run_provider(settings, transcript, prompt, *, provider, strict_empty_cli, on_stream_chunk=None, cancel_check=None):
        assert not cancel_check()
        holder["service"].cancel()  # arrives while the request is in flight
        seen.set()
        assert cancel_check()
        raise RuntimeError("cancelled by test")

    service, output = _service(run_provider)
    holder["service"] = service
    service.cancel()  # nothing running yet → error
    service.start({"type": "llm_start", "text": "t", "modes": ["summary"], "settings": SETTINGS, "output_dir": str(tmp_path)})

    messages = _messages(output)
    assert messages[0] == {"type": "error", "message": "No LLM request is running"}
    assert seen.is_set()
    done = messages[-1]
    assert done["type"] == "llm_completed" and done["success"] is False and done["cancelled"] is True
    assert not (tmp_path / "session_llm_summary.txt").exists()


def test_llm_provider_error_becomes_failed_completion(tmp_path):
    def run_provider(*args, **kwargs):
        raise RuntimeError("boom")

    service, output = _service(run_provider)
    service.start({"type": "llm_start", "text": "t", "modes": ["summary"], "settings": SETTINGS, "output_dir": str(tmp_path)})
    done = _messages(output)[-1]
    assert done == {"type": "llm_completed", "success": False, "message": "boom"}


def test_failed_process_termination_is_not_reported_as_successful_cancel(tmp_path):
    from src.services import llm_service
    holder = {}

    def run_provider(*args, **kwargs):
        holder["service"].cancel()
        raise llm_service.LLMTerminationError("owned CLI is still alive")

    service, output = _service(run_provider)
    holder["service"] = service
    service.start({"type": "llm_start", "text": "t", "modes": ["summary"], "settings": SETTINGS, "output_dir": str(tmp_path)})
    done = _messages(output)[-1]
    assert done.get("cancelled") is not True
    assert done["termination_failed"] is True
    assert done["message"] == "owned CLI is still alive"
