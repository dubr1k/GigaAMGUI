import base64
import io
import json

import numpy as np
import pytest

from src.live.types import CaptureSource, CaptureState, TranscriptEvent
from src.services.live_worker_service import LiveWorkerService


class FakeScheduler:
    instances = []

    def __init__(self, source, on_final, on_partial, on_error):
        self.source, self._on_final, self._on_partial = source, on_final, on_partial
        self.submitted, self.flushed, self.closed = [], False, False
        FakeScheduler.instances.append(self)

    def submit(self, chunk):
        self.submitted.append(chunk)

    def flush(self):
        self.flushed = True

    def close(self):
        self.closed = True

    def partial(self, text):
        c = self.submitted[-1]
        self._on_partial(TranscriptEvent(
            f"{self.source.value}-{c.sample_offset}", 0, self.source,
            c.sample_offset, c.sample_offset + len(c.frames), 1, text, "partial",
        ))

    def final(self, text):
        c = self.submitted[-1]
        self._on_final(TranscriptEvent(
            f"{self.source.value}-{c.sample_offset}", 1, self.source,
            c.sample_offset, c.sample_offset + len(c.frames), 1, text, "final",
        ))


class _InlineThread:
    def __init__(self, *, target, args=(), daemon=True):
        self._target, self._args = target, args

    def start(self):
        self._target(*self._args)

    def is_alive(self):
        return False


@pytest.fixture
def service(tmp_path):
    FakeScheduler.instances.clear()
    output = io.StringIO()

    def emit(message_type, **payload):
        output.write(json.dumps({"type": message_type, **payload}, ensure_ascii=False) + "\n")

    svc = LiveWorkerService(
        emit,
        scheduler_factory=lambda source, on_final, on_partial, on_error: FakeScheduler(source, on_final, on_partial, on_error),
    )
    return svc, output, tmp_path


def _messages(output):
    return [json.loads(line) for line in output.getvalue().splitlines()]


def _pcm(n=1600, value=0.25):
    return base64.b64encode((np.full(n, value * 32767, dtype=np.int16)).tobytes()).decode("ascii")


def _start(svc, tmp_path, **overrides):
    command = {"type": "live_start", "session_root": str(tmp_path), "sources": ["mic"], "exports": {"txt": True, "srt": True}}
    command.update(overrides)
    svc.start(command)


def test_live_start_creates_session_and_reports_recording(service):
    svc, output, tmp_path = service
    _start(svc, tmp_path)

    status = [m for m in _messages(output) if m["type"] == "live_status"][-1]
    assert status["state"] == "recording"
    assert status["active_sources"] == ["mic"]
    assert svc.is_running()
    assert svc.session.status().state is CaptureState.RECORDING


def test_live_start_validates_root_sources_and_exclusivity(service, tmp_path):
    svc, output, _ = service
    svc.start({"type": "live_start", "session_root": str(tmp_path / "nope"), "sources": ["mic"]})
    svc.start({"type": "live_start", "session_root": str(tmp_path), "sources": []})
    svc.start({"type": "live_start", "session_root": str(tmp_path), "sources": ["radio"]})
    errors = [m["message"] for m in _messages(output) if m["type"] == "error"]
    assert errors == [
        "session_root must be an existing directory",
        "sources must contain mic and/or system",
        "Unknown live source: 'radio'",
    ]

    _start(svc, tmp_path)
    _start(svc, tmp_path)
    assert _messages(output)[-1] == {"type": "error", "message": "Processing is already running"}


def test_live_audio_feeds_scheduler_and_partials_finals_are_emitted(service):
    svc, output, tmp_path = service
    _start(svc, tmp_path)

    svc.audio({"type": "live_audio", "source": "mic", "seq": 0, "sample_offset": 0, "timestamp_ns": 5, "pcm": _pcm()})
    scheduler = FakeScheduler.instances[0]
    assert len(scheduler.submitted) == 1
    assert scheduler.submitted[0].sample_rate == 16_000
    assert np.allclose(scheduler.submitted[0].frames[:, 0], 0.25, atol=1e-3)

    scheduler.partial("прив")
    scheduler.final("привет")
    kinds = [m["type"] for m in _messages(output)]
    assert "live_partial" in kinds and "live_final" in kinds
    final = [m for m in _messages(output) if m["type"] == "live_final"][-1]
    assert final["text"] == "привет" and final["source"] == "mic" and final["sample_start"] == 0


def test_live_audio_rejects_bad_payloads_without_stopping(service):
    svc, output, tmp_path = service
    _start(svc, tmp_path)
    svc.audio({"type": "live_audio", "source": "system", "seq": 0, "sample_offset": 0, "timestamp_ns": 0, "pcm": _pcm()})
    svc.audio({"type": "live_audio", "source": "mic", "seq": 0, "sample_offset": 0, "timestamp_ns": 0, "pcm": "***"})
    errors = [m["message"] for m in _messages(output) if m["type"] == "error"]
    assert errors == ["Unknown live source: 'system'", "live_audio pcm is not valid base64 int16"]
    assert svc.is_running()


def test_live_seq_gap_emits_capture_event(service):
    svc, output, tmp_path = service
    _start(svc, tmp_path)
    svc.audio({"type": "live_audio", "source": "mic", "seq": 0, "sample_offset": 0, "timestamp_ns": 0, "pcm": _pcm()})
    svc.audio({"type": "live_audio", "source": "mic", "seq": 2, "sample_offset": 3200, "timestamp_ns": 0, "pcm": _pcm()})
    events = [m for m in _messages(output) if m["type"] == "live_capture_event"]
    assert events[-1]["kind"] == "discontinuity" and events[-1]["source"] == "mic"


def test_live_capture_event_from_client_marks_source_failed(service):
    svc, output, tmp_path = service
    _start(svc, tmp_path)
    svc.capture_event({"type": "live_capture_event", "source": "mic", "kind": "permission_denied", "detail": "denied"})
    status = svc.session.status()
    assert status.state is CaptureState.FAILED and status.failed_sources == {CaptureSource.MIC}
    kinds = {m["kind"] for m in _messages(output) if m["type"] == "live_capture_event"}
    assert kinds == {"permission_denied", "status"}  # the failure itself plus the session's status note


def test_live_pause_resume_stop_export(service):
    svc, output, tmp_path = service
    _start(svc, tmp_path)
    svc.audio({"type": "live_audio", "source": "mic", "seq": 0, "sample_offset": 0, "timestamp_ns": 0, "pcm": _pcm()})
    FakeScheduler.instances[0].final("готово")

    svc.pause()
    assert [m for m in _messages(output) if m["type"] == "live_status"][-1]["state"] == "paused"
    svc.resume()
    assert [m for m in _messages(output) if m["type"] == "live_status"][-1]["state"] == "recording"
    svc.stop()

    stopped = _messages(output)[-1]
    assert stopped["type"] == "live_stopped"
    saved = [name.rsplit("/", 1)[-1] for name in stopped["saved_files"]]
    assert "transcript.txt" in saved and "transcript.srt" in saved
    assert FakeScheduler.instances[0].flushed and FakeScheduler.instances[0].closed
    assert not svc.is_running()


def test_live_stop_without_session_is_an_error(service):
    svc, output, _ = service
    svc.stop()
    svc.pause()
    assert [m["message"] for m in _messages(output)] == ["No live session is running", "No live session is running"]


def test_live_ask_streams_answer_and_records_turn(service):
    svc, output, tmp_path = service
    _start(svc, tmp_path)
    svc.audio({"type": "live_audio", "source": "mic", "seq": 0, "sample_offset": 0, "timestamp_ns": 0, "pcm": _pcm()})
    FakeScheduler.instances[0].final("встреча в пять")

    def run_provider(settings, transcript, prompt, *, provider, strict_empty_cli, on_stream_chunk=None, cancel_check=None):
        assert "встреча в пять" in transcript and prompt == "когда встреча?"
        on_stream_chunk("в ")
        on_stream_chunk("пять")
        return "в пять"

    svc._run_provider = run_provider
    svc._thread_factory = _InlineThread
    svc.ask({"type": "live_ask", "question": "когда встреча?", "settings": {"provider": "API"}})

    messages = _messages(output)
    chunks = [m["text"] for m in messages if m["type"] == "live_answer_chunk"]
    assert chunks == ["в ", "пять"]
    answer = [m for m in messages if m["type"] == "live_answer"][-1]
    assert answer["status"] == "complete" and answer["text"] == "в пять"
    assert svc.session.conversation()[0].answer == "в пять"


def test_live_ask_without_session_or_transcript_errors(service):
    svc, output, tmp_path = service
    svc.ask({"type": "live_ask", "question": "?", "settings": {}})
    _start(svc, tmp_path)
    svc.ask({"type": "live_ask", "question": "?", "settings": {}})
    errors = [m["message"] for m in _messages(output) if m["type"] == "error"]
    assert errors == ["No live session is running", "No final transcript events are available yet"]
