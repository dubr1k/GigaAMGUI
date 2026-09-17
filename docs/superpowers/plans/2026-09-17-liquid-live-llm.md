# GigaAMLiquid Live + LLM Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the Live and LLM pages of the native macOS client `GigaAMLiquid` to the existing Python stack through the JSONL worker, with audio capture in Swift, and ship it as release 2.1.0.

**Architecture:** Swift owns hardware and permissions (AVAudioEngine microphone, ScreenCaptureKit system audio) and pushes 16 kHz mono int16 PCM as base64 JSON lines into the frozen companion's `--native-worker` stdin. Python owns everything else: a new `PushCaptureAdapter` feeds the unchanged `LiveSession`, a `LiveWorkerService` and `LLMWorkerService` translate JSONL commands into `LiveSession`/`llm_service` calls, and `TuiWorker` stays a thin router. Two new Swift job classes mirror `NativeTranscriptionJob`.

**Tech Stack:** Python 3.12 (numpy, existing `src/live`, `src/services/llm_service.py`), Swift 5.9 / AppKit / AVFoundation / ScreenCaptureKit (macOS 13+), pytest, SwiftPM.

**Spec:** `docs/superpowers/specs/2026-09-17-liquid-live-llm-design.md`

## Global Constraints

- `web`, `api.py`, `cli.py` and every `src/services`/`src/live` module must never import `src.gui` (AGENTS.md). The new services import only `src.live`, `src.core`, `src.services`, `src.utils`.
- Worker protocol is JSON Lines: one JSON object per line on stdin, one per line on stdout, stderr is human diagnostics only.
- PCM transport: `sample_rate` 16000, mono, int16 little-endian, base64 in the `pcm` field, chunks of 100 ms (1600 frames).
- One live session per worker process; a live session and a batch `start` are mutually exclusive (`"Processing is already running"`).
- Sortformer takes no manual speaker count anywhere (release 2.0.5 rule); live diarization backend choices are `pyannote`, `onnx`, `sortformer`.
- No hard-coded UI strings in Swift without an `L10n` entry; the `english` dictionary must have no duplicate keys (test `test_swift_english_dictionary_has_no_duplicate_keys`).
- Secrets (LLM API key) live in Keychain via `SecureStore`, never in `UserDefaults`; job classes redact them in logs via `safeText`.
- Tests: `.venv/bin/python -m pytest tests/ -q -p no:warnings` must stay green; `ruff check src/ tests/ packaging/ web/ scripts/ *.py` must stay clean. Delete `*sync-conflict*.py` copies before running pytest.
- Commit only when a task says so; never push or tag except in the final release task.

---

## File map

| Path | Responsibility |
|---|---|
| `src/live/capture/push.py` (new) | `PushCaptureAdapter`: `CaptureAdapter` fed from outside the process |
| `src/live/asr_backend.py` (new) | `LazyModelBackend` (moved from `src/gui/live_mixin.py._LiveModelBackend`) |
| `src/services/live_worker_service.py` (new) | `LiveWorkerService`: JSONL command handling for live sessions |
| `src/services/llm_worker_service.py` (new) | `LLMWorkerService`: `llm_start`/`llm_cancel` with text input and streaming |
| `src/tui_worker.py` (modify) | route `live_*`, `llm_cancel`; delegate LLM to service |
| `src/gui/live_mixin.py` (modify) | import `LazyModelBackend` from `src/live/asr_backend.py` |
| `scripts/native_worker_smoke.py` (modify) | `--live` mode |
| `.github/workflows/build.yml` (modify) | live smoke gate |
| `macos/GigaAMLiquid/Sources/GigaAMLiquid/LiveCapture.swift` (new) | mic + system audio capture, conversion, permissions |
| `macos/GigaAMLiquid/Sources/GigaAMLiquid/LiveSessionJob.swift` (new) | worker process for a live session |
| `macos/GigaAMLiquid/Sources/GigaAMLiquid/LLMJob.swift` (new) | worker process for an LLM request |
| `macos/GigaAMLiquid/Sources/GigaAMLiquid/WorkerProcess.swift` (new) | shared `LineReader`, `Failure`, `safeText`, process lifecycle extracted from `Transcription.swift` |
| `macos/GigaAMLiquid/Sources/GigaAMLiquid/Transcription.swift` (modify) | use `WorkerProcess` helpers |
| `macos/GigaAMLiquid/Sources/GigaAMLiquid/main.swift` (modify) | Live page, LLM page, LLM settings, About text |
| `macos/GigaAMLiquid/Sources/GigaAMLiquid/Localization.swift` (modify) | new strings |
| `tests/test_live_capture_push.py`, `tests/test_live_worker_service.py`, `tests/test_llm_worker_service.py` (new), `tests/test_tui_worker.py`, `tests/test_macos_swift_packaging.py` (modify) | tests |

---

### Task 1: `PushCaptureAdapter`

**Files:**
- Create: `src/live/capture/push.py`
- Test: `tests/test_live_capture_push.py`

**Interfaces:**
- Consumes: `CaptureAdapter` protocol from `src/live/capture/base.py`; `PcmChunk`, `CaptureEvent`, `CaptureEventKind`, `CaptureDevice`, `CaptureSource` from `src/live/types.py`.
- Produces: `class PushCaptureAdapter(source: CaptureSource, sample_rate: int = 16_000, channels: int = 1)` with methods `start(on_chunk, on_event)`, `pause()`, `resume()`, `stop()`, `devices() -> list[CaptureDevice]`, `push(seq: int, sample_offset: int, timestamp_ns: int, frames: np.ndarray) -> None`, `event(kind: CaptureEventKind, detail: str) -> None`. `frames` is float32 shape `(n, channels)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_live_capture_push.py
import numpy as np

from src.live.capture.base import CaptureAdapter
from src.live.capture.push import PushCaptureAdapter
from src.live.types import CaptureEventKind, CaptureSource


def _frames(n=1600):
    return np.zeros((n, 1), dtype=np.float32)


def _started(source=CaptureSource.MIC):
    adapter = PushCaptureAdapter(source)
    chunks, events = [], []
    adapter.start(chunks.append, events.append)
    return adapter, chunks, events


def test_push_adapter_satisfies_capture_protocol():
    assert isinstance(PushCaptureAdapter(CaptureSource.MIC), CaptureAdapter)


def test_push_delivers_pcm_chunk_with_source_offset_and_timestamp():
    adapter, chunks, _ = _started()

    adapter.push(seq=0, sample_offset=0, timestamp_ns=10, frames=_frames())
    adapter.push(seq=1, sample_offset=1600, timestamp_ns=110_000_000, frames=_frames())

    assert [c.sample_offset for c in chunks] == [0, 1600]
    assert chunks[0].source is CaptureSource.MIC
    assert chunks[0].sample_rate == 16_000 and chunks[0].channels == 1
    assert chunks[1].timestamp_ns == 110_000_000
    assert chunks[0].frames.shape == (1600, 1)


def test_push_reports_discontinuity_on_seq_gap_and_keeps_chunk():
    adapter, chunks, events = _started()

    adapter.push(seq=0, sample_offset=0, timestamp_ns=0, frames=_frames())
    adapter.push(seq=3, sample_offset=4800, timestamp_ns=300_000_000, frames=_frames())

    assert len(chunks) == 2
    assert [e.kind for e in events] == [CaptureEventKind.DISCONTINUITY]
    assert events[0].source is CaptureSource.MIC
    assert events[0].sample_offset == 4800
    assert "seq" in events[0].detail


def test_push_ignores_chunks_while_paused_and_after_stop():
    adapter, chunks, _ = _started()

    adapter.pause()
    adapter.push(seq=0, sample_offset=0, timestamp_ns=0, frames=_frames())
    adapter.resume()
    adapter.push(seq=1, sample_offset=1600, timestamp_ns=0, frames=_frames())
    adapter.stop()
    adapter.push(seq=2, sample_offset=3200, timestamp_ns=0, frames=_frames())

    assert [c.sample_offset for c in chunks] == [1600]


def test_push_before_start_is_ignored():
    adapter = PushCaptureAdapter(CaptureSource.SYSTEM)
    adapter.push(seq=0, sample_offset=0, timestamp_ns=0, frames=_frames())  # no callbacks, no error


def test_push_event_forwards_capture_event():
    adapter, _, events = _started(CaptureSource.SYSTEM)

    adapter.event(CaptureEventKind.PERMISSION_DENIED, "screen recording denied")

    assert events[0].kind is CaptureEventKind.PERMISSION_DENIED
    assert events[0].source is CaptureSource.SYSTEM
    assert events[0].detail == "screen recording denied"


def test_push_devices_describes_one_virtual_device():
    devices = PushCaptureAdapter(CaptureSource.MIC).devices()
    assert len(devices) == 1
    assert devices[0].source is CaptureSource.MIC
    assert devices[0].sample_rate == 16_000
    assert devices[0].is_default
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_live_capture_push.py -q -p no:warnings`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.live.capture.push'`

- [ ] **Step 3: Write the adapter**

```python
# src/live/capture/push.py
"""Capture adapter fed by an external process (the Swift client pushes PCM)."""

from __future__ import annotations

from collections.abc import Callable
from threading import Lock

import numpy as np

from ..types import CaptureDevice, CaptureEvent, CaptureEventKind, CaptureSource, PcmChunk


class PushCaptureAdapter:
    """`CaptureAdapter` whose audio arrives via `push()` instead of a device callback.

    The pushing side owns the hardware and its permissions; this adapter only
    validates ordering and converts frames into `PcmChunk`.
    """

    def __init__(self, source: CaptureSource, sample_rate: int = 16_000, channels: int = 1) -> None:
        self.source = source
        self.sample_rate = sample_rate
        self.channels = channels
        self._on_chunk: Callable[[PcmChunk], None] | None = None
        self._on_event: Callable[[CaptureEvent], None] | None = None
        self._started = False
        self._paused = False
        self._next_seq = 0
        self._lock = Lock()

    def start(
        self,
        on_chunk: Callable[[PcmChunk], None],
        on_event: Callable[[CaptureEvent], None],
    ) -> None:
        with self._lock:
            self._on_chunk = on_chunk
            self._on_event = on_event
            self._started = True
            self._paused = False
            self._next_seq = 0

    def pause(self) -> None:
        with self._lock:
            self._paused = True

    def resume(self) -> None:
        with self._lock:
            if self._started:
                self._paused = False

    def stop(self) -> None:
        with self._lock:
            self._started = False
            self._paused = False
            self._on_chunk = None
            self._on_event = None

    def devices(self) -> list[CaptureDevice]:
        name = "Microphone (native client)" if self.source is CaptureSource.MIC else "System audio (native client)"
        return [CaptureDevice(f"{self.source.value}-push", name, self.source, self.sample_rate, self.channels, True)]

    def push(self, seq: int, sample_offset: int, timestamp_ns: int, frames: np.ndarray) -> None:
        with self._lock:
            if not self._started or self._paused or self._on_chunk is None:
                return
            on_chunk, on_event = self._on_chunk, self._on_event
            expected = self._next_seq
            self._next_seq = seq + 1
        if seq != expected and on_event is not None:
            on_event(CaptureEvent(
                CaptureEventKind.DISCONTINUITY, self.source, sample_offset, timestamp_ns,
                f"seq gap: expected {expected}, got {seq}",
            ))
        frames = np.ascontiguousarray(frames, dtype=np.float32).reshape(-1, self.channels).copy()
        on_chunk(PcmChunk(self.source, self.sample_rate, self.channels, sample_offset, frames, timestamp_ns))

    def event(self, kind: CaptureEventKind, detail: str) -> None:
        with self._lock:
            on_event = self._on_event
        if on_event is not None:
            on_event(CaptureEvent(kind, self.source, 0, 0, detail))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_live_capture_push.py -q -p no:warnings`
Expected: 7 passed

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/python -m ruff check src/live/capture/push.py tests/test_live_capture_push.py
git add src/live/capture/push.py tests/test_live_capture_push.py
git commit -m "Add PushCaptureAdapter for externally captured live audio

The native macOS client owns the microphone and ScreenCaptureKit and
pushes 16 kHz PCM into the worker; LiveSession only needs a
CaptureAdapter, so this adapter validates seq ordering (gap →
discontinuity event, never a crash) and wraps frames in PcmChunk.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Move the lazy ASR backend out of the GUI

**Files:**
- Create: `src/live/asr_backend.py`
- Modify: `src/gui/live_mixin.py:31-42` (delete `_LiveModelBackend`), `src/gui/live_mixin.py:250-258` (use `LazyModelBackend`)
- Test: `tests/test_live_asr_backend.py`

**Interfaces:**
- Produces: `class LazyModelBackend(model_loader, load_error: str)` with `transcribe_window(audio, sample_rate: int, offset_samples: int)`. Identical behaviour to the removed `_LiveModelBackend`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_live_asr_backend.py
import pytest

from src.live.asr_backend import LazyModelBackend


class FakeLoader:
    def __init__(self, loads: bool):
        self._loads = loads
        self._loaded = False
        self.calls = []

    def is_loaded(self):
        return self._loaded

    def load_model(self, logger=None):
        self._loaded = self._loads
        return self._loads

    def diagnostics(self):
        return {"error": "no weights"}

    def transcribe_window(self, audio, sample_rate, offset_samples):
        self.calls.append((sample_rate, offset_samples))
        return ["segment"]


def test_lazy_backend_loads_model_on_first_window():
    loader = FakeLoader(loads=True)
    backend = LazyModelBackend(loader, "load failed")

    assert backend.transcribe_window(b"", 16_000, 0) == ["segment"]
    assert loader.is_loaded()
    assert loader.calls == [(16_000, 0)]


def test_lazy_backend_raises_with_diagnostics_when_load_fails():
    backend = LazyModelBackend(FakeLoader(loads=False), "load failed")

    with pytest.raises(RuntimeError, match="load failed: no weights"):
        backend.transcribe_window(b"", 16_000, 0)


def test_gui_no_longer_defines_private_backend():
    import src.gui.live_mixin as live_mixin

    assert not hasattr(live_mixin, "_LiveModelBackend")
    assert live_mixin.LazyModelBackend is LazyModelBackend
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_live_asr_backend.py -q -p no:warnings`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.live.asr_backend'`

- [ ] **Step 3: Create the module and switch the GUI to it**

```python
# src/live/asr_backend.py
"""Window backend that loads the ASR model lazily on the scheduler thread."""

from __future__ import annotations


class LazyModelBackend:
    """Load the selected model on first use, off the UI thread."""

    def __init__(self, model_loader, load_error: str) -> None:
        self._model_loader = model_loader
        self._load_error = load_error

    def transcribe_window(self, audio, sample_rate: int, offset_samples: int):
        if not self._model_loader.is_loaded() and not self._model_loader.load_model():
            detail = self._model_loader.diagnostics().get("error")
            raise RuntimeError(f"{self._load_error}: {detail or 'unknown error'}")
        return self._model_loader.transcribe_window(audio, sample_rate, offset_samples)
```

In `src/gui/live_mixin.py`: delete the `_LiveModelBackend` class (lines 31-42), add `from ..live.asr_backend import LazyModelBackend` next to the other `..live` imports, and replace `_LiveModelBackend(` with `LazyModelBackend(` in `_start_live_session`.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_live_asr_backend.py tests/test_gui_live_tab.py tests/test_gui_live_issue_42.py -q -p no:warnings`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
.venv/bin/python -m ruff check src/live/asr_backend.py src/gui/live_mixin.py tests/test_live_asr_backend.py
git add src/live/asr_backend.py src/gui/live_mixin.py tests/test_live_asr_backend.py
git commit -m "Move lazy live ASR backend out of the Qt mixin

The worker-side live service needs the same lazy loader and must not
import src.gui.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `LLMWorkerService` with text input, streaming and cancel

**Files:**
- Create: `src/services/llm_worker_service.py`
- Modify: `src/tui_worker.py:47-56` (route), `src/tui_worker.py:114-172` (delete `_start_llm`/`_run_llm`)
- Test: `tests/test_llm_worker_service.py`, `tests/test_tui_worker.py`

**Interfaces:**
- Consumes: `src.services.llm_service.run_provider(settings, transcript, prompt, provider=..., strict_empty_cli=..., on_stream_chunk=..., cancel_check=...)`.
- Produces: `class LLMWorkerService(emit: Callable[..., None], run_provider=llm_service.run_provider)` with `start(command: dict) -> None`, `cancel() -> None`, `is_running() -> bool`. Events: `llm_started {mode, index, total}`, `llm_chunk {mode, text}`, `llm_completed {success, saved_files, results: [{mode, text}], message?, cancelled?}`.
- Command `llm_start` fields: `files` (list) or `text` (str), `modes` (list[str]), `prompt` (custom), `settings` (dict), `output_dir` (str, optional). `llm_cancel` has no fields.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_llm_worker_service.py
import io
import json
import threading

from src.services.llm_worker_service import LLMWorkerService


def _service(run_provider):
    output = io.StringIO()

    def emit(message_type, **payload):
        output.write(json.dumps({"type": message_type, **payload}, ensure_ascii=False) + "\n")

    return LLMWorkerService(emit, run_provider=run_provider, thread_factory=_InlineThread), output


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
    cancelled = threading.Event()

    def run_provider(settings, transcript, prompt, *, provider, strict_empty_cli, on_stream_chunk=None, cancel_check=None):
        cancelled.set()
        assert cancel_check()
        raise RuntimeError("cancelled by test")

    service, output = _service(run_provider)
    service.cancel()  # before start → error
    service._cancel_requested.set()  # simulate cancel racing the request
    service.start({"type": "llm_start", "text": "t", "modes": ["summary"], "settings": SETTINGS, "output_dir": str(tmp_path)})

    done = _messages(output)[-1]
    assert done["type"] == "llm_completed" and done["success"] is False and done["cancelled"] is True


def test_llm_provider_error_becomes_failed_completion(tmp_path):
    def run_provider(*args, **kwargs):
        raise RuntimeError("boom")

    service, output = _service(run_provider)
    service.start({"type": "llm_start", "text": "t", "modes": ["summary"], "settings": SETTINGS, "output_dir": str(tmp_path)})
    done = _messages(output)[-1]
    assert done == {"type": "llm_completed", "success": False, "message": "boom"}
```

Add to `tests/test_tui_worker.py`:

```python
def test_tui_worker_routes_llm_cancel_without_job():
    output = io.StringIO()
    worker = TuiWorker(output=output)

    worker.handle({"type": "llm_cancel"})

    assert _messages(output) == [{"type": "error", "message": "No LLM request is running"}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_llm_worker_service.py tests/test_tui_worker.py -q -p no:warnings`
Expected: FAIL with `ModuleNotFoundError` for the service and `Unknown command: 'llm_cancel'` for the worker test.

- [ ] **Step 3: Write the service**

```python
# src/services/llm_worker_service.py
"""JSONL-facing LLM runner shared by the TUI worker and the native macOS client."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.services import llm_service

PROMPTS = {
    "summary": "Сделай плотную выжимку: ключевые факты, решения, риски и открытые вопросы.",
    "tasks": "Выдели конкретные задачи, ответственных, сроки и открытые вопросы.",
    "terms": "Выдели основные термины, имена, организации, сокращения и их контекст.",
}


class LLMWorkerService:
    """Run one `llm_start` request at a time and report progress as JSONL events."""

    def __init__(
        self,
        emit: Callable[..., None],
        *,
        run_provider: Callable[..., str] = llm_service.run_provider,
        thread_factory: Callable[..., Any] = threading.Thread,
    ) -> None:
        self._emit = emit
        self._run_provider = run_provider
        self._thread_factory = thread_factory
        self._task: Any = None
        self._cancel_requested = threading.Event()

    def is_running(self) -> bool:
        return bool(self._task and self._task.is_alive())

    def start(self, command: dict[str, Any]) -> None:
        if self.is_running():
            self._emit("error", message="Processing is already running")
            return
        text = str(command.get("text") or "").strip()
        paths = [Path(str(value)) for value in command.get("files", []) if str(value or "").strip()]
        if not text and not paths:
            self._emit("error", message="Provide LLM transcript text or files")
            return
        if any(not path.is_file() for path in paths):
            self._emit("error", message="LLM transcript file does not exist")
            return
        modes = command.get("modes", [command.get("mode") or "summary"])
        if not isinstance(modes, list) or not modes or not all(isinstance(mode, str) for mode in modes):
            self._emit("error", message="Select at least one LLM mode")
            return
        custom_prompt = str(command.get("prompt") or "").strip()
        if "custom" in modes and not custom_prompt:
            self._emit("error", message="Custom LLM prompt is required")
            return
        settings = command.get("settings")
        if not isinstance(settings, dict):
            self._emit("error", message="LLM settings are required")
            return
        transcript = "\n\n".join(
            ([f"--- transcript ---\n{text}"] if text else [])
            + [f"--- {path.name} ---\n{path.read_text(encoding='utf-8')}" for path in paths]
        )
        target = Path(str(command.get("output_dir") or "")) if command.get("output_dir") else (paths[-1].parent if paths else None)
        self._cancel_requested.clear()
        self._task = self._thread_factory(
            target=self._run, args=(transcript, list(dict.fromkeys(modes)), custom_prompt, settings, target), daemon=True
        )
        self._task.start()

    def cancel(self) -> None:
        if not self.is_running():
            self._emit("error", message="No LLM request is running")
            return
        self._cancel_requested.set()

    def _run(self, transcript: str, modes: list[str], custom_prompt: str, settings: dict[str, Any], target: Path | None) -> None:
        saved_files: list[str] = []
        results: list[dict[str, str]] = []
        provider = str(settings.get("provider") or "API")
        try:
            for index, mode in enumerate(modes, 1):
                prompt = custom_prompt if mode == "custom" else PROMPTS.get(mode, custom_prompt)
                if not prompt:
                    raise ValueError(f"Unknown LLM mode: {mode}")
                self._emit("llm_started", mode=mode, index=index, total=len(modes))
                answer = self._run_provider(
                    settings, transcript, prompt, provider=provider, strict_empty_cli=True,
                    on_stream_chunk=lambda chunk, mode=mode: self._emit("llm_chunk", mode=mode, text=chunk),
                    cancel_check=self._cancel_requested.is_set,
                )
                if self._cancel_requested.is_set():
                    raise _Cancelled()
                results.append({"mode": mode, "text": answer})
                if target is not None:
                    target.mkdir(parents=True, exist_ok=True)
                    saved = target / f"session_llm_{mode}.txt"
                    saved.write_text(answer, encoding="utf-8")
                    saved_files.append(str(saved))
            self._emit("llm_completed", success=True, saved_files=saved_files, results=results)
        except _Cancelled:
            self._emit("llm_completed", success=False, cancelled=True, saved_files=saved_files, results=results)
        except Exception as exc:
            if self._cancel_requested.is_set():
                self._emit("llm_completed", success=False, cancelled=True, saved_files=saved_files, results=results)
            else:
                self._emit("llm_completed", success=False, message=str(exc))


class _Cancelled(Exception):
    pass
```

In `src/tui_worker.py`:
- add `from src.services.llm_worker_service import LLMWorkerService` at the top;
- in `__init__`, add `self._llm = LLMWorkerService(self.emit)`;
- in `handle`, replace the `llm_start` branch with `self._llm.start(command)` and add `elif command_type == "llm_cancel": self._llm.cancel()`;
- delete `_start_llm` and `_run_llm`;
- in `_start` (batch) add before creating the thread: `if self._llm.is_running(): self.emit("error", message="Processing is already running"); return`.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_llm_worker_service.py tests/test_tui_worker.py -q -p no:warnings`
Expected: all pass. Also run `tests/test_tui*.py tests/test_llm*.py` to catch TUI users of `llm_start`.

- [ ] **Step 5: Commit**

```bash
.venv/bin/python -m ruff check src/services/llm_worker_service.py src/tui_worker.py tests/test_llm_worker_service.py tests/test_tui_worker.py
git add src/services/llm_worker_service.py src/tui_worker.py tests/test_llm_worker_service.py tests/test_tui_worker.py
git commit -m "Extract LLMWorkerService with inline text, streaming and cancel

The native client sends pasted text rather than files and needs
streamed chunks and a cancel command; TuiWorker becomes a router.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `LiveWorkerService` (start/audio/pause/resume/stop)

**Files:**
- Create: `src/services/live_worker_service.py`
- Modify: `src/tui_worker.py` (route `live_start`, `live_audio`, `live_capture_event`, `live_pause`, `live_resume`, `live_stop`, batch exclusivity)
- Test: `tests/test_live_worker_service.py`, `tests/test_tui_worker.py`

**Interfaces:**
- Consumes: `PushCaptureAdapter` (Task 1), `LazyModelBackend` (Task 2), `LiveSession`, `LiveAsrScheduler`, `ExportSelection`, `LiveSettings`, `DiarizationMode`, `CaptureSource`, `CaptureEventKind`, `CaptureEvent`, `LiveStatus`, `TranscriptEvent`, `ModelLoader`.
- Produces: `class LiveWorkerService(emit, *, session_factory=LiveSession, scheduler_factory=None, model_loader_factory=None)` with `start(command)`, `audio(command)`, `capture_event(command)`, `pause()`, `resume()`, `stop()`, `is_running() -> bool`, `session` (property, `LiveSession | None`). Events as in spec: `live_status`, `live_partial`, `live_final`, `live_capture_event`, `live_stopped`, `log`, `error`.
- `live_start` fields: `session_root` (str, required, existing directory), `sources` (list of `"mic"`/`"system"`, non-empty), `sample_rate` (int, default 16000), `diarization_mode` (`off`/`live_estimate`/`after_stop`, default `off`), `diarization_backend` (default `pyannote`), `record_mic` (bool, default true), `record_system` (bool, default true), `exports` (dict with `ExportSelection` field names, default txt only), `backend`, `model`, `onnx_provider` (as batch `start`).
- `live_audio` fields: `source`, `seq`, `sample_offset`, `timestamp_ns`, `pcm` (base64 of int16 LE mono).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_live_worker_service.py
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
        self._on_partial(TranscriptEvent(f"{self.source.value}-{c.sample_offset}", 0, self.source, c.sample_offset, c.sample_offset + len(c.frames), 1, text, "partial"))

    def final(self, text):
        c = self.submitted[-1]
        self._on_final(TranscriptEvent(f"{self.source.value}-{c.sample_offset}", 1, self.source, c.sample_offset, c.sample_offset + len(c.frames), 1, text, "final"))


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
    assert errors == ["session_root must be an existing directory", "sources must contain mic and/or system", "Unknown live source: 'radio'"]

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
    status = [m for m in _messages(output) if m["type"] == "live_status"][-1]
    assert status["state"] == "failed" and status["failed_sources"] == ["mic"]


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
```

Add to `tests/test_tui_worker.py`:

```python
def test_tui_worker_routes_live_commands_to_service(tmp_path):
    output = io.StringIO()
    worker = TuiWorker(output=output)

    worker.handle({"type": "live_stop"})
    worker.handle({"type": "live_audio", "source": "mic", "seq": 0, "sample_offset": 0, "timestamp_ns": 0, "pcm": ""})

    assert [m["message"] for m in _messages(output)] == ["No live session is running", "No live session is running"]


def test_tui_worker_batch_start_is_rejected_while_live_session_runs(tmp_path, monkeypatch):
    class FakeLive:
        def is_running(self):
            return True

    worker = TuiWorker(output=io.StringIO())
    worker._live = FakeLive()
    sample = tmp_path / "a.wav"
    sample.write_bytes(b"x")
    output = io.StringIO()
    worker._output = output
    worker.handle({"type": "start", "files": [str(sample)]})
    assert _messages(output) == [{"type": "error", "message": "Processing is already running"}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_live_worker_service.py tests/test_tui_worker.py -q -p no:warnings`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.services.live_worker_service'` and `Unknown command: 'live_stop'`.

- [ ] **Step 3: Write the service**

```python
# src/services/live_worker_service.py
"""JSONL-facing live session controller; audio is pushed by the native client."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

from src.live.asr import LiveAsrScheduler
from src.live.asr_backend import LazyModelBackend
from src.live.capture.push import PushCaptureAdapter
from src.live.exports import ExportSelection
from src.live.session import LiveSession, LiveStatus
from src.live.types import (
    CaptureEvent,
    CaptureEventKind,
    CaptureSource,
    CaptureState,
    DiarizationMode,
    LiveSettings,
    TranscriptEvent,
)

_NOT_RUNNING = "No live session is running"


class LiveWorkerService:
    """One live session per worker, driven entirely by JSONL commands."""

    def __init__(
        self,
        emit: Callable[..., None],
        *,
        session_factory: Callable[..., LiveSession] = LiveSession,
        scheduler_factory: Callable[..., Any] | None = None,
        model_loader_factory: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self._emit = emit
        self._session_factory = session_factory
        self._scheduler_factory = scheduler_factory
        self._model_loader_factory = model_loader_factory or self._default_model_loader
        self._session: LiveSession | None = None
        self._adapters: dict[CaptureSource, PushCaptureAdapter] = {}
        self._sample_rate = 16_000

    @property
    def session(self) -> LiveSession | None:
        return self._session

    def is_running(self) -> bool:
        return self._session is not None

    # -- commands ---------------------------------------------------------

    def start(self, command: dict[str, Any]) -> None:
        if self._session is not None:
            self._emit("error", message="Processing is already running")
            return
        root = Path(str(command.get("session_root") or ""))
        if not root.is_dir():
            self._emit("error", message="session_root must be an existing directory")
            return
        raw_sources = command.get("sources") or []
        if not isinstance(raw_sources, list) or not raw_sources:
            self._emit("error", message="sources must contain mic and/or system")
            return
        sources: list[CaptureSource] = []
        for value in raw_sources:
            try:
                sources.append(CaptureSource(str(value)))
            except ValueError:
                self._emit("error", message=f"Unknown live source: {value!r}")
                return
        try:
            mode = DiarizationMode(str(command.get("diarization_mode") or "off"))
        except ValueError:
            self._emit("error", message=f"Unknown diarization mode: {command.get('diarization_mode')!r}")
            return
        self._sample_rate = int(command.get("sample_rate") or 16_000)
        settings = LiveSettings(
            diarization_mode=mode,
            source_sample_rate=self._sample_rate,
            asr_sample_rate=16_000,
            record_mic_audio=CaptureSource.MIC in sources and bool(command.get("record_mic", True)),
            record_system_audio=CaptureSource.SYSTEM in sources and bool(command.get("record_system", True)),
            record_source_audio=bool(command.get("record_mic", True)) or bool(command.get("record_system", True)),
            record_mix_audio=set(sources) == {CaptureSource.MIC, CaptureSource.SYSTEM},
        )
        exports_raw = command.get("exports") or {"txt": True}
        try:
            exports = ExportSelection(**{k: v for k, v in exports_raw.items() if k in ExportSelection.__dataclass_fields__})
        except TypeError as exc:
            self._emit("error", message=f"Invalid exports: {exc}")
            return
        adapters = {source: PushCaptureAdapter(source, self._sample_rate) for source in sources}
        scheduler_factory = self._scheduler_factory or self._make_scheduler_factory(command)
        backend = str(command.get("diarization_backend") or "pyannote")
        try:
            session = self._session_factory(
                root, settings, adapters,
                scheduler_factory=scheduler_factory,
                export_selection=exports,
                diarization_factory=lambda _backend, backend=backend: self._create_diarizer(backend),
                log=lambda message: self._emit("log", message=message),
            )
            session.subscribe(self._on_update)
            session.start()
        except Exception as exc:
            self._emit("error", message=f"Could not start live session: {exc}")
            return
        self._adapters = adapters
        self._session = session

    def audio(self, command: dict[str, Any]) -> None:
        adapter = self._adapter_for(command)
        if adapter is None:
            return
        try:
            raw = base64.b64decode(str(command.get("pcm") or ""), validate=True)
            if len(raw) % 2:
                raise ValueError("odd byte count")
            samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        except (binascii.Error, ValueError):
            self._emit("error", message="live_audio pcm is not valid base64 int16")
            return
        try:
            seq, offset, ts = int(command.get("seq", 0)), int(command.get("sample_offset", 0)), int(command.get("timestamp_ns", 0))
        except (TypeError, ValueError):
            self._emit("error", message="live_audio seq/sample_offset/timestamp_ns must be integers")
            return
        adapter.push(seq, offset, ts, samples[:, None])

    def capture_event(self, command: dict[str, Any]) -> None:
        adapter = self._adapter_for(command)
        if adapter is None:
            return
        try:
            kind = CaptureEventKind(str(command.get("kind") or ""))
        except ValueError:
            self._emit("error", message=f"Unknown capture event kind: {command.get('kind')!r}")
            return
        adapter.event(kind, str(command.get("detail") or ""))

    def pause(self) -> None:
        if self._session is None:
            self._emit("error", message=_NOT_RUNNING)
            return
        self._session.pause()

    def resume(self) -> None:
        if self._session is None:
            self._emit("error", message=_NOT_RUNNING)
            return
        self._session.resume()

    def stop(self) -> None:
        session = self._session
        if session is None:
            self._emit("error", message=_NOT_RUNNING)
            return
        try:
            result = session.stop()
        except Exception as exc:
            self._emit("live_stopped", session_dir=str(session.session_dir), saved_files=[], recordings={}, message=str(exc))
        else:
            self._emit(
                "live_stopped",
                session_dir=str(result.session_dir),
                saved_files=[str(path) for path in result.exports],
                recordings={source.value: str(path) for source, path in result.recordings.items()},
            )
        finally:
            self._session = None
            self._adapters = {}

    # -- helpers ------------------------------------------------------------

    def _adapter_for(self, command: dict[str, Any]) -> PushCaptureAdapter | None:
        if self._session is None:
            self._emit("error", message=_NOT_RUNNING)
            return None
        value = str(command.get("source") or "")
        try:
            source = CaptureSource(value)
        except ValueError:
            self._emit("error", message=f"Unknown live source: {value!r}")
            return None
        adapter = self._adapters.get(source)
        if adapter is None:
            self._emit("error", message=f"Unknown live source: {value!r}")
        return adapter

    def _on_update(self, value: TranscriptEvent | CaptureEvent | LiveStatus) -> None:
        if isinstance(value, LiveStatus):
            self._emit(
                "live_status",
                state=value.state.value,
                active_sources=sorted(s.value for s in value.active_sources),
                failed_sources=sorted(s.value for s in value.failed_sources),
            )
        elif isinstance(value, CaptureEvent):
            self._emit("live_capture_event", source=value.source.value, kind=value.kind.value, detail=value.detail)
        elif isinstance(value, TranscriptEvent):
            payload = {
                "event_id": value.event_id, "revision": value.revision, "source": value.source.value,
                "sample_start": value.sample_start, "sample_end": value.sample_end,
                "text": value.text, "speaker": value.speaker,
                "paragraph_break_after": value.paragraph_break_after,
            }
            self._emit("live_final" if value.status == "final" else "live_partial", **payload)

    def _make_scheduler_factory(self, command: dict[str, Any]):
        loader = self._model_loader_factory(command)
        backend = LazyModelBackend(loader, "Could not load the recognition model")

        def factory(source, on_final, on_partial, on_error):
            return LiveAsrScheduler(backend, on_final=on_final, on_partial=on_partial, on_error=on_error)

        return factory

    @staticmethod
    def _default_model_loader(command: dict[str, Any]):
        from src.core.model_loader import ModelLoader

        return ModelLoader(
            requested_backend=str(command.get("backend") or "auto"),
            model_revision=str(command.get("model") or "v3_e2e_rnnt"),
            onnx_provider=str(command.get("onnx_provider") or "auto"),
        )

    @staticmethod
    def _create_diarizer(backend: str):
        from src.core.diarization.factory import create_diarization_backend

        return create_diarization_backend(backend)


__all__ = ["LiveWorkerService", "CaptureState", "asdict"]
```

Remove the last `__all__` line's extra names before committing (`CaptureState`, `asdict` are only listed to keep ruff quiet during drafting; import only what is used). Final imports must be exactly what the module uses.

In `src/tui_worker.py`:
- `from src.services.live_worker_service import LiveWorkerService`;
- `self._live = LiveWorkerService(self.emit)` in `__init__`;
- route: `live_start → self._live.start(command)`, `live_audio → self._live.audio(command)`, `live_capture_event → self._live.capture_event(command)`, `live_pause → self._live.pause()`, `live_resume → self._live.resume()`, `live_stop → self._live.stop()`;
- in `_start` (batch) add `if self._live.is_running(): self.emit("error", message="Processing is already running"); return` next to the LLM check;
- in `LiveWorkerService.start` nothing knows about batch; add to `TuiWorker.handle` for `live_start`: `if self._task and self._task.is_alive(): self.emit("error", message="Processing is already running")` before delegating.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_live_worker_service.py tests/test_tui_worker.py tests/test_live_session.py -q -p no:warnings`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
.venv/bin/python -m ruff check src/services/live_worker_service.py src/tui_worker.py tests/
git add src/services/live_worker_service.py src/tui_worker.py tests/test_live_worker_service.py tests/test_tui_worker.py
git commit -m "Add LiveWorkerService: live sessions over the JSONL worker protocol

The native client pushes base64 int16 PCM; the service decodes it into
PushCaptureAdapter, runs the unchanged LiveSession and mirrors its
status/partial/final events to stdout.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Live assistant questions (`live_ask`, `live_ask_cancel`)

**Files:**
- Modify: `src/services/live_worker_service.py`, `src/tui_worker.py`
- Test: `tests/test_live_worker_service.py`

**Interfaces:**
- Produces: `LiveWorkerService.ask(command)` and `ask_cancel()`. `live_ask` fields: `question` (str), `settings` (LLM settings dict). Events `live_answer_chunk {turn_id, text}`, `live_answer {turn_id, status, text}`.
- Consumes: `LiveSession.ask_context()`, `begin_conversation()`, `append_conversation_answer()`, `finish_conversation()`, `cancel_conversation()`; `llm_service.run_provider`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_live_worker_service.py`:

```python
def test_live_ask_streams_answer_and_records_turn(service, monkeypatch):
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


class _InlineThread:
    def __init__(self, *, target, args=(), daemon=True):
        self._target, self._args = target, args

    def start(self):
        self._target(*self._args)

    def is_alive(self):
        return False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_live_worker_service.py -q -p no:warnings -k ask`
Expected: FAIL with `AttributeError: 'LiveWorkerService' object has no attribute 'ask'`

- [ ] **Step 3: Implement**

Add to `LiveWorkerService.__init__`: `self._run_provider = llm_service.run_provider`, `self._thread_factory = threading.Thread`, `self._ask_cancel = threading.Event()`, `self._ask_thread = None` (import `threading` and `from src.services import llm_service`).

```python
    def ask(self, command: dict[str, Any]) -> None:
        session = self._session
        if session is None:
            self._emit("error", message=_NOT_RUNNING)
            return
        question = str(command.get("question") or "").strip()
        if not question:
            self._emit("error", message="Question is required")
            return
        transcript = session.ask_context()
        if not transcript:
            self._emit("error", message="No final transcript events are available yet")
            return
        settings = command.get("settings")
        if not isinstance(settings, dict):
            self._emit("error", message="LLM settings are required")
            return
        if self._ask_thread is not None and self._ask_thread.is_alive():
            self._emit("error", message="An assistant question is already running")
            return
        turn = session.begin_conversation(question)
        self._ask_cancel = threading.Event()
        self._ask_thread = self._thread_factory(target=self._answer, args=(session, turn.id, transcript, question, settings, self._ask_cancel), daemon=True)
        self._ask_thread.start()

    def ask_cancel(self) -> None:
        if self._ask_thread is None or not self._ask_thread.is_alive():
            self._emit("error", message="No assistant question is running")
            return
        self._ask_cancel.set()

    def _answer(self, session, turn_id, transcript, question, settings, cancel) -> None:
        def stream(chunk: str) -> None:
            session.append_conversation_answer(turn_id, chunk)
            self._emit("live_answer_chunk", turn_id=turn_id, text=chunk)

        try:
            answer = self._run_provider(
                settings, transcript, question, provider=str(settings.get("provider") or "API"),
                strict_empty_cli=True, on_stream_chunk=stream, cancel_check=cancel.is_set,
            )
        except Exception as exc:
            if cancel.is_set():
                session.cancel_conversation(turn_id)
                self._emit("live_answer", turn_id=turn_id, status="cancelled", text="")
            else:
                session.finish_conversation(turn_id, "", status="error")
                self._emit("live_answer", turn_id=turn_id, status="error", text=str(exc))
            return
        if cancel.is_set():
            session.cancel_conversation(turn_id)
            self._emit("live_answer", turn_id=turn_id, status="cancelled", text="")
            return
        session.finish_conversation(turn_id, answer)
        self._emit("live_answer", turn_id=turn_id, status="complete", text=answer)
```

Route in `TuiWorker.handle`: `live_ask → self._live.ask(command)`, `live_ask_cancel → self._live.ask_cancel()`.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_live_worker_service.py tests/test_tui_worker.py -q -p no:warnings`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
.venv/bin/python -m ruff check src/services/live_worker_service.py src/tui_worker.py tests/test_live_worker_service.py
git add src/services/live_worker_service.py src/tui_worker.py tests/test_live_worker_service.py
git commit -m "Add live assistant questions to the worker protocol

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Live smoke mode for CI

**Files:**
- Modify: `scripts/native_worker_smoke.py`, `.github/workflows/build.yml` (offline Swift step), `tests/test_macos_swift_packaging.py`

**Interfaces:**
- Produces: `run_live_smoke(companion: Path, clip: Path, session_root: Path, *, timeout: float = 600.0) -> int` and CLI flag `--live`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_macos_swift_packaging.py`:

```python
def test_offline_swift_archive_runs_live_smoke_through_the_companion() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    offline = text.split("Assemble and verify offline native Swift archive", 1)[1].split("Upload offline native Swift artifact", 1)[0]
    assert "python3 scripts/native_worker_smoke.py --live" in offline


def test_native_worker_smoke_has_live_mode() -> None:
    source = Path("scripts/native_worker_smoke.py").read_text(encoding="utf-8")
    assert "def run_live_smoke(" in source
    assert '"type": "live_start"' in source and '"type": "live_audio"' in source and '"type": "live_stop"' in source
    assert '"--live"' in source
```

Add a unit test of the chunker to `tests/test_native_worker_smoke.py` (new):

```python
import numpy as np

from scripts.native_worker_smoke import pcm_chunks


def test_pcm_chunks_are_100ms_int16_base64():
    samples = np.linspace(-1, 1, 16_000 * 0.25.__int__() + 4000, dtype=np.float32)  # 0.25 s + 4000 frames
    chunks = list(pcm_chunks(samples, 16_000))
    assert [c["seq"] for c in chunks] == list(range(len(chunks)))
    assert chunks[0]["sample_offset"] == 0 and chunks[1]["sample_offset"] == 1600
    import base64
    assert len(base64.b64decode(chunks[0]["pcm"])) == 1600 * 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_macos_swift_packaging.py tests/test_native_worker_smoke.py -q -p no:warnings -k "live or smoke"`
Expected: FAIL (`ImportError: cannot import name 'pcm_chunks'`, assertion on workflow text)

- [ ] **Step 3: Implement**

In `scripts/native_worker_smoke.py` add:

```python
import base64
import wave

import numpy as np


def pcm_chunks(samples: np.ndarray, sample_rate: int, chunk_seconds: float = 0.1):
    """Yield live_audio payload dicts from float32 mono samples."""
    frames_per_chunk = int(sample_rate * chunk_seconds)
    for seq, start in enumerate(range(0, len(samples), frames_per_chunk)):
        block = samples[start:start + frames_per_chunk]
        pcm = np.clip(block * 32767.0, -32768, 32767).astype("<i2").tobytes()
        yield {
            "type": "live_audio", "source": "mic", "seq": seq, "sample_offset": start,
            "timestamp_ns": int(start / sample_rate * 1_000_000_000), "pcm": base64.b64encode(pcm).decode("ascii"),
        }


def _load_wav_16k_mono(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as handle:
        rate, channels, width = handle.getframerate(), handle.getnchannels(), handle.getsampwidth()
        raw = handle.readframes(handle.getnframes())
    if width != 2:
        raise SystemExit("live smoke expects a 16-bit PCM WAV")
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != 16_000:
        positions = np.arange(0, len(samples), rate / 16_000)
        samples = np.interp(positions, np.arange(len(samples)), samples).astype(np.float32)
    return samples


def run_live_smoke(companion: Path, clip: Path, session_root: Path, *, timeout: float = 600.0) -> int:
    session_root.mkdir(parents=True, exist_ok=True)
    samples = _load_wav_16k_mono(clip)
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    started = time.monotonic()
    proc = subprocess.Popen(
        [str(companion), "--native-worker"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=sys.stderr, cwd=companion.parent, env=env, text=True, bufsize=1,
    )
    assert proc.stdin is not None and proc.stdout is not None

    def send(message: dict) -> None:
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()

    send({"type": "live_start", "session_root": str(session_root), "sources": ["mic"], "sample_rate": 16_000,
          "diarization_mode": "off", "exports": {"txt": True}, "backend": "auto", "model": "v3_e2e_rnnt", "onnx_provider": "auto"})
    for chunk in pcm_chunks(samples, 16_000):
        send(chunk)
    # Trailing silence lets the scheduler finalize the last utterance.
    silence = np.zeros(16_000 * 4, dtype=np.float32)
    base = len(samples)
    for chunk in pcm_chunks(silence, 16_000):
        chunk["seq"] += len(samples) // 1600 + 1
        chunk["sample_offset"] += base
        send(chunk)
    send({"type": "live_stop"})

    finals, stopped = [], None
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                print(f"[worker] {line}", flush=True)
                continue
            kind = message.get("type")
            if kind == "log":
                print(f"[worker] {message.get('message', '')}", flush=True)
            elif kind == "error":
                print(f"[worker] ERROR: {message.get('message', '')}", flush=True)
            elif kind == "live_final":
                finals.append(message["text"])
            elif kind == "live_stopped":
                stopped = message
                break
            if time.monotonic() - started > timeout:
                print(f"live smoke: timeout after {timeout:.0f}s", flush=True)
                break
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
    if stopped is None:
        print("live smoke: worker exited without live_stopped", flush=True)
        return 1
    if not finals:
        print("live smoke: no live_final events", flush=True)
        return 1
    transcript = next((Path(p) for p in stopped.get("saved_files", []) if p.endswith("transcript.txt")), None)
    if transcript is None or not transcript.is_file() or not transcript.read_text(encoding="utf-8").strip():
        print(f"live smoke: transcript.txt missing or empty: {stopped}", flush=True)
        return 1
    print(f"live smoke: OK in {time.monotonic() - started:.1f}s, finals={finals!r}", flush=True)
    return 0
```

In `main()`: add `parser.add_argument("--live", action="store_true", help="прогнать клип через live_start/live_audio/live_stop")` and branch `if args.live: return run_live_smoke(...)`.

Note: the seq adjustment for the silence tail must produce a contiguous sequence; compute `next_seq = -(-len(samples) // 1600)` (ceil) and use `chunk["seq"] += next_seq`, replacing the `+ 1` draft above.

In `.github/workflows/build.yml`, inside the "Assemble and verify offline native Swift archive" step, right after the existing `python3 scripts/native_worker_smoke.py ...` line, add an equivalent invocation with `--live` and a separate output directory (`"${RUNNER_TEMP}/liquid-offline-live"`), same `HF_HOME`/`HF_HUB_OFFLINE=1` prefix.

- [ ] **Step 4: Run tests and a local live smoke**

Run: `.venv/bin/python -m pytest tests/test_macos_swift_packaging.py tests/test_native_worker_smoke.py -q -p no:warnings`
Expected: pass

Run locally against the source tree (the script accepts any executable; use a tiny wrapper):
```bash
printf '#!/bin/sh\nexec %s/.venv/bin/python -m src.tui_worker\n' "$PWD" > /tmp/worker.sh && chmod +x /tmp/worker.sh
.venv/bin/python scripts/native_worker_smoke.py --live /tmp/worker.sh uploads/test_gigaam_diarization.wav /tmp/live-smoke
```
Expected: `live smoke: OK ...` with at least one final. (The wrapper is needed because the script passes `--native-worker`; make `run_live_smoke` skip that flag when the companion path ends with `.sh`, or add `--worker-args ""`. Simplest: add optional `--worker-arg` defaulting to `--native-worker`.)

- [ ] **Step 5: Commit**

```bash
.venv/bin/python -m ruff check scripts/native_worker_smoke.py tests/
git add scripts/native_worker_smoke.py .github/workflows/build.yml tests/test_macos_swift_packaging.py tests/test_native_worker_smoke.py
git commit -m "Gate releases on a live-protocol smoke run through the companion

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Extract `WorkerProcess.swift` from `Transcription.swift`

**Files:**
- Create: `macos/GigaAMLiquid/Sources/GigaAMLiquid/WorkerProcess.swift`
- Modify: `macos/GigaAMLiquid/Sources/GigaAMLiquid/Transcription.swift`
- Test: `tests/test_macos_swift_packaging.py`

**Interfaces:**
- Produces (internal, file-private no more): `struct WorkerFailure: LocalizedError { init(_ message: String) }`, `final class LineReader` (same init signature as today), `enum WorkerRedaction { static func safeText(_ text: String, secrets: [String]) -> String }`, and `final class WorkerProcess` that owns `Process`, stdin handle, both `LineReader`s, with `init(runtime: PythonRuntime, arguments: [String], environment: [String: String], queue: DispatchQueue, onLine: @escaping (Data) -> Void, onStderr: @escaping (String) -> Void, onStdoutEnd: @escaping () -> Void, onExit: @escaping (Int32) -> Void) throws`, `func send(_ command: [String: Any]) throws`, `func closeInput()`, `func terminateGracefully(after: TimeInterval)`, `func kill()`, `var isRunning: Bool`, `func drain()`, `func close()`.
- `NativeTranscriptionJob` keeps its public API and events unchanged.

- [ ] **Step 1: Write the failing structural test**

```python
def test_swift_worker_process_is_shared_between_jobs() -> None:
    worker = Path("macos/GigaAMLiquid/Sources/GigaAMLiquid/WorkerProcess.swift").read_text(encoding="utf-8")
    assert "final class WorkerProcess" in worker
    assert "final class LineReader" in worker
    assert "enum WorkerRedaction" in worker
    assert "F_SETNOSIGPIPE" in worker
    transcription = Path("macos/GigaAMLiquid/Sources/GigaAMLiquid/Transcription.swift").read_text(encoding="utf-8")
    assert "final class LineReader" not in transcription
    assert "credentialPatterns" not in transcription
    assert "WorkerProcess(" in transcription
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_macos_swift_packaging.py -q -p no:warnings -k worker_process`
Expected: FAIL (`FileNotFoundError`)

- [ ] **Step 3: Extract**

Move `LineReader` (lines 419-508), `Failure` (rename to `WorkerFailure`), `credentialPatterns` + `safeText` (as `WorkerRedaction.safeText(_:secrets:)`) into `WorkerProcess.swift`. Implement `WorkerProcess` by moving the `Process`/pipe setup from `NativeTranscriptionJob.launch()` (the `let task = Process()` … `try? stderrPipe.fileHandleForWriting.close()` block, including the `F_SETNOSIGPIPE` guard and both `LineReader` constructions) and `send(_:)`, plus the two-stage `terminate → SIGKILL` timer from `requestTerminal` as `terminateGracefully(after:)`. `NativeTranscriptionJob` keeps `files`, `settings`, event decoding, `acceptResult`, `requestTerminal`, `workerExited`, `finish`, and calls `WorkerProcess` for the rest; `safeText` in the job becomes `WorkerRedaction.safeText(text, secrets: secrets)`.

- [ ] **Step 4: Build and run the existing structural tests plus a real transcription**

```bash
cd macos/GigaAMLiquid && swift build 2>&1 | grep -E "error:|warning:|Build complete" | grep -v "search path"
cd ../.. && .venv/bin/python -m pytest tests/test_macos_swift_packaging.py -q -p no:warnings
```
Expected: build complete with no warnings; all structural tests pass (`test_swift_job_launch_does_not_require_source_tree_with_frozen_companion` still finds its guard in `launch()`; keep that guard in `Transcription.swift`).
Then launch the debug binary with `GIGAAM_PROJECT_ROOT=$PWD`, add `uploads/test_gigaam_diarization.wav`, press Start, confirm «Обработка завершена».

- [ ] **Step 5: Commit**

```bash
git add macos/GigaAMLiquid/Sources/GigaAMLiquid/WorkerProcess.swift macos/GigaAMLiquid/Sources/GigaAMLiquid/Transcription.swift tests/test_macos_swift_packaging.py
git commit -m "Extract WorkerProcess from NativeTranscriptionJob for reuse by Live and LLM jobs

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: `LLMJob.swift`

**Files:**
- Create: `macos/GigaAMLiquid/Sources/GigaAMLiquid/LLMJob.swift`
- Test: `tests/test_macos_swift_packaging.py`

**Interfaces:**
- Consumes: `WorkerProcess`, `PythonRuntime.resolve()`, `WorkerRedaction`.
- Produces:
```swift
struct LLMRequest {
    var text: String            // pasted or loaded transcript
    var files: [URL]            // optional transcript files
    var modes: [String]         // "summary" | "tasks" | "terms" | "custom"
    var prompt: String          // custom prompt
    var settings: [String: Any] // provider settings as PyQt's _collect_llm_settings
    var outputDirectory: URL?
}
enum LLMJobEvent { case started(mode: String, index: Int, total: Int), chunk(mode: String, text: String), completed(results: [(mode: String, text: String)], saved: [URL]), cancelled, failed(String), log(String) }
final class LLMJob { init(request: LLMRequest, onEvent: @escaping (LLMJobEvent) -> Void); func start(); func cancel(); func terminate() }
```

- [ ] **Step 1: Write the failing structural test**

```python
def test_swift_llm_job_uses_worker_protocol_and_redacts_api_key() -> None:
    job = Path("macos/GigaAMLiquid/Sources/GigaAMLiquid/LLMJob.swift").read_text(encoding="utf-8")
    assert '"type": "llm_start"' in job and '"type": "llm_cancel"' in job
    for event in ('"llm_started"', '"llm_chunk"', '"llm_completed"'):
        assert event in job
    assert "WorkerProcess(" in job
    assert 'settings["api_key"]' in job  # secret collected for redaction
    assert "WorkerRedaction.safeText" in job
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_macos_swift_packaging.py -q -p no:warnings -k llm_job`
Expected: FAIL (`FileNotFoundError`)

- [ ] **Step 3: Implement**

```swift
import Foundation

struct LLMRequest {
    var text: String
    var files: [URL] = []
    var modes: [String]
    var prompt: String = ""
    var settings: [String: Any]
    var outputDirectory: URL?
}

enum LLMJobEvent {
    case started(mode: String, index: Int, total: Int)
    case chunk(mode: String, text: String)
    case completed(results: [(mode: String, text: String)], saved: [URL])
    case cancelled
    case failed(String)
    case log(String)
}

/// One worker per request; exactly one terminal event (completed/cancelled/failed).
final class LLMJob {
    private let request: LLMRequest
    private let onEvent: (LLMJobEvent) -> Void
    private let queue = DispatchQueue(label: "GigaAMLiquid.llm", qos: .userInitiated)
    private var worker: WorkerProcess?
    private var finished = false
    private var secrets: [String] = []

    init(request: LLMRequest, onEvent: @escaping (LLMJobEvent) -> Void) {
        self.request = request
        self.onEvent = onEvent
    }

    func start() {
        queue.async {
            guard self.worker == nil, !self.finished else { return }
            do { try self.launch() } catch { self.finish(.failed(self.safe(error.localizedDescription))) }
        }
    }

    func cancel() {
        queue.async {
            guard let worker = self.worker, !self.finished else { return }
            try? worker.send(["type": "llm_cancel"])
        }
    }

    func terminate() {
        queue.sync {
            guard !self.finished else { return }
            self.worker?.kill()
            self.finish(.cancelled)
        }
    }

    private func launch() throws {
        let runtime = try PythonRuntime.resolve()
        if let key = request.settings["api_key"] as? String, key.count >= 6 { secrets.append(key) }
        var command: [String: Any] = [
            "type": "llm_start", "text": request.text, "files": request.files.map(\.path),
            "modes": request.modes, "prompt": request.prompt, "settings": request.settings,
        ]
        if let directory = request.outputDirectory { command["output_dir"] = directory.path }
        let worker = try WorkerProcess(
            runtime: runtime, arguments: runtime.transcriptionArguments, environment: runtime.environment, queue: queue,
            onLine: { [weak self] in self?.consume($0) },
            onStderr: { [weak self] in self?.emit(.log($0)) },
            onStdoutEnd: { [weak self] in self?.finish(.failed("The LLM worker closed its output without completing.")) },
            onExit: { [weak self] status in self?.finish(.failed("The LLM worker exited (status \(status)).")) }
        )
        self.worker = worker
        try worker.send(command)
    }

    private func consume(_ line: Data) {
        guard !finished, let object = try? JSONSerialization.jsonObject(with: line) as? [String: Any],
              let type = object["type"] as? String else { return }
        switch type {
        case "llm_started":
            emit(.started(mode: object["mode"] as? String ?? "", index: object["index"] as? Int ?? 0, total: object["total"] as? Int ?? 0))
        case "llm_chunk":
            emit(.chunk(mode: object["mode"] as? String ?? "", text: object["text"] as? String ?? ""))
        case "llm_completed":
            if object["cancelled"] as? Bool == true { finish(.cancelled); return }
            guard object["success"] as? Bool == true else { finish(.failed(safe(object["message"] as? String ?? "LLM request failed."))); return }
            let results = (object["results"] as? [[String: Any]] ?? []).map { (mode: $0["mode"] as? String ?? "", text: $0["text"] as? String ?? "") }
            let saved = (object["saved_files"] as? [String] ?? []).map { URL(fileURLWithPath: $0) }
            finish(.completed(results: results, saved: saved))
        case "error":
            finish(.failed(safe(object["message"] as? String ?? "LLM worker error.")))
        case "log":
            emit(.log(safe(object["message"] as? String ?? "")))
        default: break
        }
    }

    private func safe(_ text: String) -> String { WorkerRedaction.safeText(text, secrets: secrets) }

    private func emit(_ event: LLMJobEvent) {
        guard !finished else { return }
        DispatchQueue.main.async { self.onEvent(event) }
    }

    private func finish(_ event: LLMJobEvent) {
        guard !finished else { return }
        finished = true
        worker?.closeInput()
        worker?.terminateGracefully(after: 2)
        DispatchQueue.main.async { self.onEvent(event) }
    }
}
```

- [ ] **Step 4: Build and test**

```bash
cd macos/GigaAMLiquid && swift build 2>&1 | grep -E "error:|warning:|Build complete" | grep -v "search path"; cd ../..
.venv/bin/python -m pytest tests/test_macos_swift_packaging.py -q -p no:warnings
```
Expected: build clean, tests pass.

- [ ] **Step 5: Commit**

```bash
git add macos/GigaAMLiquid/Sources/GigaAMLiquid/LLMJob.swift tests/test_macos_swift_packaging.py
git commit -m "Add LLMJob: llm_start/llm_cancel over the shared worker process

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: LLM page and LLM settings in `main.swift`

**Files:**
- Modify: `macos/GigaAMLiquid/Sources/GigaAMLiquid/main.swift` (`buildLLM`, `settingsPage("LLM")`, new handlers), `Localization.swift`
- Test: `tests/test_macos_swift_packaging.py`

**Interfaces:**
- Consumes: `LLMJob`, `LLMRequest`, `SecureStore`.
- Produces: UserDefaults keys `llm.provider` (values `API`, `Claude Code`, `Codex`, `OpenCode`, `Pi`, `Other`), `llm.apiUrl`, `llm.model`, `llm.temperature`, `llm.claudePath`, `llm.claudeArgs`, `llm.codexPath`, `llm.codexArgs`, `llm.opencodePath`, `llm.opencodeArgs`, `llm.piPath`, `llm.piProvider`, `llm.piArgs`, `llm.otherPath`, `llm.otherArgs`, `llm.source`, `llm.prompt`, `llm.mode`; Keychain account `llmApiKey`. Method `llmSettings() throws -> [String: Any]` returning exactly the dict shape of PyQt `_collect_llm_settings`.

- [ ] **Step 1: Write the failing structural tests**

```python
def test_swift_llm_page_is_wired_to_llm_job() -> None:
    main = MAIN_SWIFT.read_text(encoding="utf-8")
    page = main.split("private func buildLLM(into content: NSStackView)", 1)[1].split("private func buildAPI", 1)[0]
    assert "unavailableButton(" not in page
    assert "LLM-сервис не подключён" not in page
    assert "#selector(runLLM(_:))" in page and "#selector(cancelLLM(_:))" in page
    assert 'popup(["API", "Claude Code", "Codex", "OpenCode", "Pi", "Other"], key: "llm.provider")' in page
    settings = _swift_block(main, "private func llmSettings() throws -> [String: Any] {")
    assert 'SecureStore.string(for: "llmApiKey")' in settings
    assert '"temperature":' in settings and '"claude_path":' in settings and '"other_args":' in settings
    assert 'defaults.set(sender.stringValue, forKey: "llm.apiKey")' not in main
    handler = _swift_block(main, "@objc private func runLLM(_ sender: Any?) {")
    assert "LLMJob(request:" in handler
    assert "LLMJobEvent" in main


def test_swift_llm_api_key_uses_keychain() -> None:
    main = MAIN_SWIFT.read_text(encoding="utf-8")
    text_changed = _swift_block(main, "@objc private func textChanged(_ sender: NSTextField) {")
    assert 'key == "llm.apiKey"' in text_changed
    assert 'SecureStore.set(sender.stringValue.trimmingCharacters(in: .whitespacesAndNewlines), for: "llmApiKey")' in text_changed
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_macos_swift_packaging.py -q -p no:warnings -k llm_page`
Expected: FAIL on `unavailableButton(` still present.

- [ ] **Step 3: Implement**

In `AppController` add state: `private var llmJob: LLMJob?`, `private var llmResultText = ""`, `private weak var llmResultView: NSTextView?`, `private weak var llmRunButton: NSButton?`, `private weak var llmCancelButton: NSButton?`, `private weak var llmStatusLabel: NSTextField?`, `private var llmTranscriptFiles: [URL] = []`.

Rewrite `buildLLM`:
- «Исходный текст» card: keep file button (`chooseTranscript`), provider popup with key `llm.provider` and the six values, model text (`llm.model`), transcript editor (`llm.source`).
- «Шаблоны» card: three template buttons set `llm.mode` to `summary`/`tasks`/`custom`; prompt editor (`llm.prompt`); replace the note and `unavailableButton` with `button("Запустить обработку", primary: true, action: #selector(runLLM(_:)))` and `button("Отменить", action: #selector(cancelLLM(_:)))`, a status `wrappedLabel` stored in `llmStatusLabel`.
- «Результат» card: `textEditor` (read-only) stored in `llmResultView`, buttons «Копировать» (`copyLLMResult`) and «Сохранить .md» (`saveLLMResult`) enabled when `!llmResultText.isEmpty`.

Settings «LLM» category: provider popup (same key), `editableText` for `llm.apiUrl`, `llm.model`, `llm.temperature` (placeholder «0.2»), secure field for `llm.apiKey` built like the HF token field but with account `llmApiKey`, and `editableText` for each CLI path/args key. Keep it one column; `settingsField` per row.

```swift
    private func llmSettings() throws -> [String: Any] {
        let provider = option("llm.provider", values: ["API", "Claude Code", "Codex", "OpenCode", "Pi", "Other"])
        let apiURL = (defaults.string(forKey: "llm.apiUrl") ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let apiKey = (SecureStore.string(for: "llmApiKey") ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let model = (defaults.string(forKey: "llm.model") ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let temperatureText = (defaults.string(forKey: "llm.temperature") ?? "0.2").trimmingCharacters(in: .whitespacesAndNewlines)
        guard let temperature = Double(temperatureText), (0...2).contains(temperature) else {
            throw WorkerFailure(L10n.text("Temperature должно быть числом в диапазоне 0..2"))
        }
        if provider == "API" {
            guard !apiURL.isEmpty else { throw WorkerFailure(L10n.text("Укажите API URL")) }
            guard !apiKey.isEmpty else { throw WorkerFailure(L10n.text("Укажите API Key")) }
            guard !model.isEmpty else { throw WorkerFailure(L10n.text("Укажите модель")) }
        }
        func text(_ key: String, _ fallback: String = "") -> String {
            let value = (defaults.string(forKey: key) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            return value.isEmpty ? fallback : value
        }
        if provider == "Other", text("llm.otherPath").isEmpty { throw WorkerFailure(L10n.text("Укажите команду для провайдера «Другое»")) }
        return [
            "provider": provider, "api_url": apiURL, "api_key": apiKey, "model": model, "temperature": temperature,
            "claude_path": text("llm.claudePath", "claude"), "claude_args": text("llm.claudeArgs"),
            "codex_path": text("llm.codexPath", "codex"), "codex_args": text("llm.codexArgs"),
            "opencode_path": text("llm.opencodePath", "opencode"), "opencode_args": text("llm.opencodeArgs"),
            "pi_path": text("llm.piPath", "pi"), "pi_provider": text("llm.piProvider"), "pi_args": text("llm.piArgs"),
            "other_path": text("llm.otherPath"), "other_args": text("llm.otherArgs"),
        ]
    }

    @objc private func runLLM(_ sender: Any?) {
        window.makeFirstResponder(nil)
        guard llmJob == nil else { return }
        let source = (transcriptEditor?.string ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard !source.isEmpty || !llmTranscriptFiles.isEmpty else {
            showNotice("Не удалось запустить LLM", "Выберите файл с транскриптом или вставьте текст."); return
        }
        let mode = option("llm.mode", values: ["summary", "tasks", "custom"])
        let prompt = (promptEditor?.string ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if mode == "custom", prompt.isEmpty { showNotice("Не удалось запустить LLM", "Введите пользовательский промпт."); return }
        let settings: [String: Any]
        do { settings = try llmSettings() } catch { showNotice("LLM не настроена", error.localizedDescription); return }
        llmResultText = ""
        llmResultView?.string = ""
        llmStatusLabel?.stringValue = L10n.text("Запрос отправлен…")
        let request = LLMRequest(text: source, files: llmTranscriptFiles, modes: [mode], prompt: prompt, settings: settings, outputDirectory: outputDirectory)
        let job = LLMJob(request: request) { [weak self] event in self?.receiveLLMEvent(event) }
        llmJob = job
        refreshLLMControls()
        job.start()
    }

    @objc private func cancelLLM(_ sender: Any?) { llmJob?.cancel() }

    private func receiveLLMEvent(_ event: LLMJobEvent) {
        switch event {
        case .started(let mode, _, _): llmStatusLabel?.stringValue = L10n.text("Обработка: ") + mode
        case .chunk(_, let text):
            llmResultText += text
            llmResultView?.string = llmResultText
        case .completed(let results, let saved):
            llmResultText = results.map(\.text).joined(separator: "\n\n")
            llmResultView?.string = llmResultText
            llmStatusLabel?.stringValue = saved.isEmpty ? L10n.text("Готово.") : L10n.text("Готово. Сохранено: ") + saved.map(\.lastPathComponent).joined(separator: ", ")
            llmJob = nil; refreshLLMControls()
        case .cancelled:
            llmStatusLabel?.stringValue = L10n.text("Запрос отменён."); llmJob = nil; refreshLLMControls()
        case .failed(let message):
            llmStatusLabel?.stringValue = message; llmJob = nil; refreshLLMControls()
        case .log(let message): transcriptionLog += message + "\n"
        }
    }

    private func refreshLLMControls() {
        llmRunButton?.isEnabled = llmJob == nil
        llmCancelButton?.isEnabled = llmJob != nil
    }

    @objc private func copyLLMResult(_ sender: Any?) {
        NSPasteboard.general.clearContents(); NSPasteboard.general.setString(llmResultText, forType: .string)
    }

    @objc private func saveLLMResult(_ sender: Any?) {
        let panel = NSSavePanel(); panel.nameFieldStringValue = "llm_result.md"
        panel.beginSheetModal(for: window) { [weak self] response in
            guard response == .OK, let url = panel.url, let self else { return }
            do { try self.llmResultText.write(to: url, atomically: true, encoding: .utf8) }
            catch { self.showNotice("Не удалось сохранить", error.localizedDescription) }
        }
    }
```

`chooseTranscript` already exists (line ~2637); make it append chosen URLs to `llmTranscriptFiles` and show their names in `llmStatusLabel`. `textChanged`: add the `llm.apiKey → SecureStore("llmApiKey")` branch mirroring `settings.hfToken`. `textDidChange` (NSTextView delegate) already persists `llm.source`/`llm.prompt` by identifier; keep it. Terminate `llmJob` in `applicationShouldTerminate` and `windowWillClose` the same way `transcriptionJob` is terminated. Add all new strings to `Localization.swift` (run the duplicate-key test).

- [ ] **Step 4: Build, test, verify manually**

```bash
cd macos/GigaAMLiquid && swift build 2>&1 | grep -E "error:|warning:|Build complete" | grep -v "search path"; cd ../..
.venv/bin/python -m pytest tests/test_macos_swift_packaging.py -q -p no:warnings
```
Manual: run debug binary with `GIGAAM_PROJECT_ROOT=$PWD`, set provider «Other» with path `/bin/cat` in Settings → LLM, paste text on the LLM page, press «Запустить обработку»; result view must show the prompt+transcript echoed by `cat` and status «Готово. Сохранено: session_llm_summary.txt». Screenshot the page.

- [ ] **Step 5: Commit**

```bash
git add macos/GigaAMLiquid/Sources/GigaAMLiquid/main.swift macos/GigaAMLiquid/Sources/GigaAMLiquid/Localization.swift tests/test_macos_swift_packaging.py
git commit -m "Wire the Liquid LLM page and settings to the worker

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: `LiveCapture.swift` (microphone + system audio → 16 kHz int16 chunks)

**Files:**
- Create: `macos/GigaAMLiquid/Sources/GigaAMLiquid/LiveCapture.swift`
- Test: `tests/test_macos_swift_packaging.py`

**Interfaces:**
- Produces:
```swift
enum LiveSource: String { case mic, system }
struct LiveAudioChunk { let source: LiveSource; let seq: Int; let sampleOffset: Int; let timestampNs: Int64; let pcm: Data /* int16 LE mono 16 kHz */; let rms: Float }
enum LiveCaptureEvent { case chunk(LiveAudioChunk), permissionDenied(LiveSource, String), deviceRemoved(LiveSource, String), overflow(LiveSource, String), level(LiveSource, Float) }
struct LiveInputDevice { let id: String; let name: String; let isDefault: Bool }
protocol LiveCaptureSource: AnyObject { var source: LiveSource { get }; func start() throws; func pause(); func resume(); func stop() }
final class MicrophoneCapture: LiveCaptureSource { init(deviceID: String?, onEvent: @escaping (LiveCaptureEvent) -> Void); static func devices() -> [LiveInputDevice]; static func requestAccess(_ completion: @escaping (Bool) -> Void) }
final class SystemAudioCapture: LiveCaptureSource { init(onEvent: @escaping (LiveCaptureEvent) -> Void); static func requestAccess() -> Bool /* CGRequestScreenCaptureAccess */ }
final class PcmChunker { init(source: LiveSource, targetRate: Double = 16_000, chunkFrames: Int = 1600, onChunk: @escaping (LiveAudioChunk) -> Void); func append(buffer: AVAudioPCMBuffer, hostTime: UInt64) throws; func flush() }
```

- [ ] **Step 1: Write the failing structural test**

```python
def test_swift_live_capture_uses_avaudioengine_and_screencapturekit() -> None:
    capture = Path("macos/GigaAMLiquid/Sources/GigaAMLiquid/LiveCapture.swift").read_text(encoding="utf-8")
    assert "import AVFoundation" in capture and "import ScreenCaptureKit" in capture
    assert "final class MicrophoneCapture" in capture and "final class SystemAudioCapture" in capture
    assert "AVAudioEngine()" in capture and "installTap(onBus: 0" in capture
    assert "SCStreamConfiguration()" in capture and "capturesAudio = true" in capture
    assert "AVAudioConverter(" in capture
    assert "CGRequestScreenCaptureAccess()" in capture and "AVCaptureDevice.requestAccess(for: .audio" in capture
    assert "chunkFrames: Int = 1600" in capture  # 100 ms at 16 kHz
    assert "Int16" in capture
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_macos_swift_packaging.py -q -p no:warnings -k live_capture`
Expected: FAIL (`FileNotFoundError`)

- [ ] **Step 3: Implement**

```swift
import AVFoundation
import CoreGraphics
import Foundation
import ScreenCaptureKit

enum LiveSource: String { case mic, system }

struct LiveAudioChunk {
    let source: LiveSource
    let seq: Int
    let sampleOffset: Int
    let timestampNs: Int64
    let pcm: Data
    let rms: Float
}

enum LiveCaptureEvent {
    case chunk(LiveAudioChunk)
    case permissionDenied(LiveSource, String)
    case deviceRemoved(LiveSource, String)
    case overflow(LiveSource, String)
    case level(LiveSource, Float)
}

struct LiveInputDevice {
    let id: String
    let name: String
    let isDefault: Bool
}

protocol LiveCaptureSource: AnyObject {
    var source: LiveSource { get }
    func start() throws
    func pause()
    func resume()
    func stop()
}

/// Resamples arbitrary input buffers to 16 kHz mono int16 and emits fixed 100 ms chunks.
final class PcmChunker {
    private let source: LiveSource
    private let targetFormat: AVAudioFormat
    private let chunkFrames: Int
    private let onChunk: (LiveAudioChunk) -> Void
    private var converter: AVAudioConverter?
    private var inputFormat: AVAudioFormat?
    private var pending = [Int16]()
    private var seq = 0
    private var sampleOffset = 0
    private var paused = false

    init(source: LiveSource, targetRate: Double = 16_000, chunkFrames: Int = 1600, onChunk: @escaping (LiveAudioChunk) -> Void) {
        self.source = source
        self.chunkFrames = chunkFrames
        self.onChunk = onChunk
        self.targetFormat = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: targetRate, channels: 1, interleaved: true)!
    }

    var isPaused: Bool { get { paused } set { paused = newValue } }

    func append(buffer: AVAudioPCMBuffer, hostTime: UInt64) throws {
        guard !paused, buffer.frameLength > 0 else { return }
        if inputFormat != buffer.format {
            inputFormat = buffer.format
            converter = AVAudioConverter(from: buffer.format, to: targetFormat)
        }
        guard let converter else { throw WorkerFailure("Unsupported audio format for live capture.") }
        let ratio = targetFormat.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 64
        guard let output = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: capacity) else { return }
        var consumed = false
        var error: NSError?
        converter.convert(to: output, error: &error) { _, status in
            if consumed { status.pointee = .noDataNow; return nil }
            consumed = true
            status.pointee = .haveData
            return buffer
        }
        if let error { throw error }
        let frames = Int(output.frameLength)
        guard frames > 0, let samples = output.int16ChannelData?[0] else { return }
        pending.append(contentsOf: UnsafeBufferPointer(start: samples, count: frames))
        let timestampNs = Int64(AVAudioTime.seconds(forHostTime: hostTime) * 1_000_000_000)
        while pending.count >= chunkFrames {
            let chunk = Array(pending[0..<chunkFrames])
            pending.removeFirst(chunkFrames)
            emit(chunk, timestampNs: timestampNs)
        }
    }

    func flush() {
        guard !pending.isEmpty else { return }
        emit(pending, timestampNs: Int64(Date().timeIntervalSince1970 * 1_000_000_000))
        pending.removeAll()
    }

    private func emit(_ samples: [Int16], timestampNs: Int64) {
        let sumSquares = samples.reduce(0.0) { $0 + pow(Double($1) / 32768.0, 2) }
        let rms = Float(sqrt(sumSquares / Double(max(samples.count, 1))))
        let data = samples.withUnsafeBufferPointer { Data(buffer: $0) }
        onChunk(LiveAudioChunk(source: source, seq: seq, sampleOffset: sampleOffset, timestampNs: timestampNs, pcm: data, rms: rms))
        seq += 1
        sampleOffset += samples.count
    }
}

final class MicrophoneCapture: LiveCaptureSource {
    let source = LiveSource.mic
    private let engine = AVAudioEngine()
    private let chunker: PcmChunker
    private let onEvent: (LiveCaptureEvent) -> Void
    private let deviceID: String?

    init(deviceID: String?, onEvent: @escaping (LiveCaptureEvent) -> Void) {
        self.deviceID = deviceID
        self.onEvent = onEvent
        self.chunker = PcmChunker(source: .mic) { chunk in onEvent(.chunk(chunk)); onEvent(.level(.mic, chunk.rms)) }
    }

    static func devices() -> [LiveInputDevice] {
        let session = AVCaptureDevice.DiscoverySession(deviceTypes: [.microphone, .external], mediaType: .audio, position: .unspecified)
        let defaultID = AVCaptureDevice.default(for: .audio)?.uniqueID
        return session.devices.map { LiveInputDevice(id: $0.uniqueID, name: $0.localizedName, isDefault: $0.uniqueID == defaultID) }
    }

    static func requestAccess(_ completion: @escaping (Bool) -> Void) {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: completion(true)
        case .notDetermined: AVCaptureDevice.requestAccess(for: .audio) { granted in DispatchQueue.main.async { completion(granted) } }
        default: completion(false)
        }
    }

    func start() throws {
        if let deviceID, let device = AVCaptureDevice(uniqueID: deviceID) {
            try Self.select(device: device, on: engine.inputNode)
        }
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0 else { throw WorkerFailure(L10n.text("Микрофон недоступен.")) }
        input.installTap(onBus: 0, bufferSize: 4096, format: format) { [weak self] buffer, time in
            guard let self else { return }
            do { try self.chunker.append(buffer: buffer, hostTime: time.hostTime) }
            catch { self.onEvent(.overflow(.mic, error.localizedDescription)) }
        }
        engine.prepare()
        try engine.start()
        NotificationCenter.default.addObserver(self, selector: #selector(configurationChanged), name: .AVAudioEngineConfigurationChange, object: engine)
    }

    @objc private func configurationChanged() {
        if !engine.isRunning { onEvent(.deviceRemoved(.mic, L10n.text("Микрофон отключён."))) }
    }

    func pause() { chunker.isPaused = true }
    func resume() { chunker.isPaused = false }

    func stop() {
        NotificationCenter.default.removeObserver(self)
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        chunker.flush()
    }

    /// Route a specific CoreAudio device into the engine's input node.
    private static func select(device: AVCaptureDevice, on node: AVAudioInputNode) throws {
        guard let unit = node.audioUnit else { return }
        var deviceID = AudioDeviceID(0)
        var size = UInt32(MemoryLayout<AudioDeviceID>.size)
        var address = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyTranslateUIDToDevice, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        var uid = device.uniqueID as CFString
        let status = withUnsafeMutablePointer(to: &uid) { pointer in
            AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, UInt32(MemoryLayout<CFString>.size), pointer, &size, &deviceID)
        }
        guard status == noErr, deviceID != 0 else { return }
        AudioUnitSetProperty(unit, kAudioOutputUnitProperty_CurrentDevice, kAudioUnitScope_Global, 0, &deviceID, UInt32(MemoryLayout<AudioDeviceID>.size))
    }
}

final class SystemAudioCapture: NSObject, LiveCaptureSource, SCStreamOutput, SCStreamDelegate {
    let source = LiveSource.system
    private let chunker: PcmChunker
    private let onEvent: (LiveCaptureEvent) -> Void
    private var stream: SCStream?
    private let queue = DispatchQueue(label: "GigaAMLiquid.systemAudio")

    init(onEvent: @escaping (LiveCaptureEvent) -> Void) {
        self.onEvent = onEvent
        self.chunker = PcmChunker(source: .system) { chunk in onEvent(.chunk(chunk)); onEvent(.level(.system, chunk.rms)) }
        super.init()
    }

    static func requestAccess() -> Bool { CGPreflightScreenCaptureAccess() || CGRequestScreenCaptureAccess() }

    func start() throws {
        let semaphore = DispatchSemaphore(value: 0)
        var content: SCShareableContent?
        var failure: Error?
        SCShareableContent.getExcludingDesktopWindows(true, onScreenWindowsOnly: true) { result, error in
            content = result; failure = error; semaphore.signal()
        }
        semaphore.wait()
        if let failure { throw failure }
        guard let display = content?.displays.first else { throw WorkerFailure(L10n.text("Нет доступного дисплея для захвата системного звука.")) }
        let filter = SCContentFilter(display: display, excludingWindows: [])
        let configuration = SCStreamConfiguration()
        configuration.capturesAudio = true
        configuration.excludesCurrentProcessAudio = true
        configuration.sampleRate = 48_000
        configuration.channelCount = 1
        configuration.width = 2
        configuration.height = 2
        configuration.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        let stream = SCStream(filter: filter, configuration: configuration, delegate: self)
        try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: queue)
        let startSemaphore = DispatchSemaphore(value: 0)
        var startError: Error?
        stream.startCapture { error in startError = error; startSemaphore.signal() }
        startSemaphore.wait()
        if let startError { throw startError }
        self.stream = stream
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, let description = CMSampleBufferGetFormatDescription(sampleBuffer),
              let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(description) else { return }
        let frames = CMSampleBufferGetNumSamples(sampleBuffer)
        guard frames > 0, let format = AVAudioFormat(streamDescription: asbd),
              let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(frames)) else { return }
        buffer.frameLength = AVAudioFrameCount(frames)
        let status = CMSampleBufferCopyPCMDataIntoAudioBufferList(sampleBuffer, at: 0, frameCount: Int32(frames), into: buffer.mutableAudioBufferList)
        guard status == noErr else { return }
        let hostTime = UInt64(CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sampleBuffer)) * 1_000_000_000)
        do { try chunker.append(buffer: buffer, hostTime: AVAudioTime.hostTime(forSeconds: Double(hostTime) / 1_000_000_000)) }
        catch { onEvent(.overflow(.system, error.localizedDescription)) }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        onEvent(.deviceRemoved(.system, error.localizedDescription))
    }

    func pause() { chunker.isPaused = true }
    func resume() { chunker.isPaused = false }

    func stop() {
        let semaphore = DispatchSemaphore(value: 0)
        stream?.stopCapture { _ in semaphore.signal() }
        _ = semaphore.wait(timeout: .now() + 3)
        stream = nil
        chunker.flush()
    }
}
```

Note: `WorkerFailure` comes from Task 7. The timestamp conversion in `SystemAudioCapture.stream(_:didOutputSampleBuffer:of:)` is deliberately via host time so both sources share a clock with the mic tap; keep as written.

- [ ] **Step 4: Build and test**

```bash
cd macos/GigaAMLiquid && swift build 2>&1 | grep -E "error:|warning:|Build complete" | grep -v "search path"; cd ../..
.venv/bin/python -m pytest tests/test_macos_swift_packaging.py -q -p no:warnings
```
Expected: build clean (fix any deprecation warnings by pinning `@available(macOS 13.0, *)` on `SystemAudioCapture` if the compiler asks), tests pass.

- [ ] **Step 5: Commit**

```bash
git add macos/GigaAMLiquid/Sources/GigaAMLiquid/LiveCapture.swift tests/test_macos_swift_packaging.py
git commit -m "Add native microphone and system-audio capture producing 16 kHz PCM chunks

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: `LiveSessionJob.swift`

**Files:**
- Create: `macos/GigaAMLiquid/Sources/GigaAMLiquid/LiveSessionJob.swift`
- Test: `tests/test_macos_swift_packaging.py`

**Interfaces:**
- Consumes: `WorkerProcess`, `PythonRuntime`, `LiveCaptureSource`, `LiveAudioChunk`, `LiveCaptureEvent`, `WorkerRedaction`.
- Produces:
```swift
struct LiveSessionSettings {
    var sessionRoot: URL
    var sources: [LiveSource]
    var microphoneDeviceID: String?
    var diarizationMode: String        // off | live_estimate | after_stop
    var diarizationBackend: String     // pyannote | onnx | sortformer
    var recordMic: Bool
    var recordSystem: Bool
    var exports: [String: Any]         // ExportSelection fields
    var backend: String; var model: String; var onnxProvider: String
    var hfToken: String?
}
enum LiveSessionEvent {
    case status(state: String, active: [LiveSource], failed: [LiveSource])
    case partial(id: String, source: LiveSource, sampleStart: Int, text: String)
    case final(id: String, source: LiveSource, sampleStart: Int, sampleEnd: Int, text: String, speaker: String?)
    case level(LiveSource, Float)
    case captureEvent(source: LiveSource, kind: String, detail: String)
    case answerChunk(turnID: String, text: String)
    case answer(turnID: String, status: String, text: String)
    case stopped(sessionDir: URL, saved: [URL])
    case failed(String)
    case log(String)
}
final class LiveSessionJob {
    init(settings: LiveSessionSettings, captures: [LiveCaptureSource], onEvent: @escaping (LiveSessionEvent) -> Void)
    func start(); func pause(); func resume(); func stop(); func ask(_ question: String, settings: [String: Any]); func cancelAsk(); func terminate()
}
```

- [ ] **Step 1: Write the failing structural test**

```python
def test_swift_live_session_job_streams_pcm_and_handles_events() -> None:
    job = Path("macos/GigaAMLiquid/Sources/GigaAMLiquid/LiveSessionJob.swift").read_text(encoding="utf-8")
    for command in ('"live_start"', '"live_audio"', '"live_pause"', '"live_resume"', '"live_stop"', '"live_ask"', '"live_ask_cancel"', '"live_capture_event"'):
        assert command in job
    for event in ('"live_status"', '"live_partial"', '"live_final"', '"live_stopped"', '"live_answer_chunk"', '"live_answer"', '"live_capture_event"'):
        assert event in job
    assert "base64EncodedString()" in job
    assert "maxBufferedChunks" in job  # 5 s backlog guard → overflow
    assert "WorkerProcess(" in job
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_macos_swift_packaging.py -q -p no:warnings -k live_session_job`
Expected: FAIL (`FileNotFoundError`)

- [ ] **Step 3: Implement**

```swift
import Foundation

struct LiveSessionSettings {
    var sessionRoot: URL
    var sources: [LiveSource]
    var microphoneDeviceID: String?
    var diarizationMode = "off"
    var diarizationBackend = "pyannote"
    var recordMic = true
    var recordSystem = true
    var exports: [String: Any] = ["txt": true]
    var backend = "auto"
    var model = "v3_e2e_rnnt"
    var onnxProvider = "auto"
    var hfToken: String?
}

enum LiveSessionEvent {
    case status(state: String, active: [LiveSource], failed: [LiveSource])
    case partial(id: String, source: LiveSource, sampleStart: Int, text: String)
    case final(id: String, source: LiveSource, sampleStart: Int, sampleEnd: Int, text: String, speaker: String?)
    case level(LiveSource, Float)
    case captureEvent(source: LiveSource, kind: String, detail: String)
    case answerChunk(turnID: String, text: String)
    case answer(turnID: String, status: String, text: String)
    case stopped(sessionDir: URL, saved: [URL])
    case failed(String)
    case log(String)
}

/// Owns the worker for one live session and forwards captured PCM to it.
final class LiveSessionJob {
    private let settings: LiveSessionSettings
    private let captures: [LiveCaptureSource]
    private let onEvent: (LiveSessionEvent) -> Void
    private let queue = DispatchQueue(label: "GigaAMLiquid.live", qos: .userInitiated)
    private var worker: WorkerProcess?
    private var finished = false
    private var stopping = false
    private var secrets: [String] = []
    private var backlog = 0
    /// 50 chunks × 100 ms = 5 s per source; beyond that the worker is not keeping up.
    private let maxBufferedChunks = 50

    init(settings: LiveSessionSettings, captures: [LiveCaptureSource], onEvent: @escaping (LiveSessionEvent) -> Void) {
        self.settings = settings
        self.captures = captures
        self.onEvent = onEvent
    }

    func start() {
        queue.async {
            guard self.worker == nil, !self.finished else { return }
            do { try self.launch() } catch { self.finish(.failed(self.safe(error.localizedDescription))) }
        }
    }

    func pause() { queue.async { self.captures.forEach { $0.pause() }; try? self.worker?.send(["type": "live_pause"]) } }
    func resume() { queue.async { self.captures.forEach { $0.resume() }; try? self.worker?.send(["type": "live_resume"]) } }

    func stop() {
        queue.async {
            guard !self.finished, !self.stopping else { return }
            self.stopping = true
            self.captures.forEach { $0.stop() }   // flushes trailing chunks synchronously via handleCapture
            do { try self.worker?.send(["type": "live_stop"]) }
            catch { self.finish(.failed(self.safe(error.localizedDescription))) }
        }
    }

    func ask(_ question: String, settings: [String: Any]) {
        queue.async {
            if let key = settings["api_key"] as? String, key.count >= 6, !self.secrets.contains(key) { self.secrets.append(key) }
            try? self.worker?.send(["type": "live_ask", "question": question, "settings": settings])
        }
    }

    func cancelAsk() { queue.async { try? self.worker?.send(["type": "live_ask_cancel"]) } }

    func terminate() {
        queue.sync {
            guard !self.finished else { return }
            self.captures.forEach { $0.stop() }
            self.worker?.kill()
            self.finish(.failed(L10n.text("Сессия прервана.")))
        }
    }

    // MARK: - Capture → worker

    func handleCapture(_ event: LiveCaptureEvent) {
        queue.async {
            guard !self.finished else { return }
            switch event {
            case .chunk(let chunk):
                guard self.backlog < self.maxBufferedChunks else {
                    self.emit(.captureEvent(source: chunk.source, kind: "overflow", detail: L10n.text("Worker не успевает обрабатывать звук; фрагмент пропущен.")))
                    try? self.worker?.send(["type": "live_capture_event", "source": chunk.source.rawValue, "kind": "overflow", "detail": "client backlog"])
                    return
                }
                self.backlog += 1
                defer { self.backlog -= 1 }
                try? self.worker?.send([
                    "type": "live_audio", "source": chunk.source.rawValue, "seq": chunk.seq,
                    "sample_offset": chunk.sampleOffset, "timestamp_ns": chunk.timestampNs,
                    "pcm": chunk.pcm.base64EncodedString(),
                ])
            case .level(let source, let rms): self.emit(.level(source, rms))
            case .permissionDenied(let source, let detail):
                try? self.worker?.send(["type": "live_capture_event", "source": source.rawValue, "kind": "permission_denied", "detail": detail])
            case .deviceRemoved(let source, let detail):
                try? self.worker?.send(["type": "live_capture_event", "source": source.rawValue, "kind": "device_removed", "detail": detail])
            case .overflow(let source, let detail):
                try? self.worker?.send(["type": "live_capture_event", "source": source.rawValue, "kind": "overflow", "detail": detail])
            }
        }
    }

    // MARK: - Worker → UI

    private func launch() throws {
        let runtime = try PythonRuntime.resolve()
        var environment = runtime.environment
        if let token = settings.hfToken, !token.isEmpty { environment["HF_TOKEN"] = token; secrets.append(token) }
        let worker = try WorkerProcess(
            runtime: runtime, arguments: runtime.transcriptionArguments, environment: environment, queue: queue,
            onLine: { [weak self] in self?.consume($0) },
            onStderr: { [weak self] in self?.emit(.log($0)) },
            onStdoutEnd: { [weak self] in self?.finish(.failed("The live worker closed its output without stopping.")) },
            onExit: { [weak self] status in self?.finish(.failed("The live worker exited (status \(status)).")) }
        )
        self.worker = worker
        try worker.send([
            "type": "live_start", "session_root": settings.sessionRoot.path, "sources": settings.sources.map(\.rawValue),
            "sample_rate": 16_000, "diarization_mode": settings.diarizationMode, "diarization_backend": settings.diarizationBackend,
            "record_mic": settings.recordMic, "record_system": settings.recordSystem, "exports": settings.exports,
            "backend": settings.backend, "model": settings.model, "onnx_provider": settings.onnxProvider,
        ])
        for capture in captures {
            do { try capture.start() }
            catch { handleCapture(.permissionDenied(capture.source, error.localizedDescription)) }
        }
    }

    private func consume(_ line: Data) {
        guard !finished, let object = try? JSONSerialization.jsonObject(with: line) as? [String: Any],
              let type = object["type"] as? String else { return }
        func source(_ key: String = "source") -> LiveSource { LiveSource(rawValue: object[key] as? String ?? "") ?? .mic }
        switch type {
        case "live_status":
            let active = (object["active_sources"] as? [String] ?? []).compactMap(LiveSource.init(rawValue:))
            let failed = (object["failed_sources"] as? [String] ?? []).compactMap(LiveSource.init(rawValue:))
            emit(.status(state: object["state"] as? String ?? "", active: active, failed: failed))
        case "live_partial":
            emit(.partial(id: object["event_id"] as? String ?? "", source: source(), sampleStart: object["sample_start"] as? Int ?? 0, text: object["text"] as? String ?? ""))
        case "live_final":
            emit(.final(id: object["event_id"] as? String ?? "", source: source(), sampleStart: object["sample_start"] as? Int ?? 0,
                        sampleEnd: object["sample_end"] as? Int ?? 0, text: object["text"] as? String ?? "", speaker: object["speaker"] as? String))
        case "live_capture_event":
            emit(.captureEvent(source: source(), kind: object["kind"] as? String ?? "", detail: safe(object["detail"] as? String ?? "")))
        case "live_answer_chunk":
            emit(.answerChunk(turnID: object["turn_id"] as? String ?? "", text: object["text"] as? String ?? ""))
        case "live_answer":
            emit(.answer(turnID: object["turn_id"] as? String ?? "", status: object["status"] as? String ?? "", text: safe(object["text"] as? String ?? "")))
        case "live_stopped":
            let saved = (object["saved_files"] as? [String] ?? []).map { URL(fileURLWithPath: $0) }
            finish(.stopped(sessionDir: URL(fileURLWithPath: object["session_dir"] as? String ?? settings.sessionRoot.path), saved: saved))
        case "error":
            let message = safe(object["message"] as? String ?? "Live worker error.")
            if stopping { finish(.failed(message)) } else { emit(.log(message)) }
        case "log":
            emit(.log(safe(object["message"] as? String ?? "")))
        default: break
        }
    }

    private func safe(_ text: String) -> String { WorkerRedaction.safeText(text, secrets: secrets) }

    private func emit(_ event: LiveSessionEvent) {
        guard !finished else { return }
        DispatchQueue.main.async { self.onEvent(event) }
    }

    private func finish(_ event: LiveSessionEvent) {
        guard !finished else { return }
        finished = true
        captures.forEach { $0.stop() }
        worker?.closeInput()
        worker?.terminateGracefully(after: 2)
        DispatchQueue.main.async { self.onEvent(event) }
    }
}
```

The `captures` are constructed by the caller with `onEvent: job.handleCapture` (see Task 12); because `init` needs the captures and the captures need the job, the controller creates captures with a closure that forwards to a `var` job reference.

- [ ] **Step 4: Build and test**

Same commands as Task 10 Step 4. Expected: clean build, tests pass.

- [ ] **Step 5: Commit**

```bash
git add macos/GigaAMLiquid/Sources/GigaAMLiquid/LiveSessionJob.swift tests/test_macos_swift_packaging.py
git commit -m "Add LiveSessionJob: live_* protocol over the shared worker process

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Live page in `main.swift`

**Files:**
- Modify: `macos/GigaAMLiquid/Sources/GigaAMLiquid/main.swift` (`buildLive`, new handlers, `applicationShouldTerminate`, `windowWillClose`, About text at line ~1686), `Localization.swift`
- Test: `tests/test_macos_swift_packaging.py`

**Interfaces:**
- Consumes: `LiveSessionJob`, `LiveSessionSettings`, `MicrophoneCapture`, `SystemAudioCapture`, `LiveCaptureSource`, `llmSettings()` (Task 9).
- Produces: UserDefaults keys `live.microphone` (device id or `default`), `live.systemAudio` (bool toggle), `live.recordMic`, `live.recordSystem`, `live.diarizationMode` (`Выкл.`/`Оценка вживую`/`После остановки` mapped to `off`/`live_estimate`/`after_stop`), `live.diarizationEngine` (`pyannote`/`onnx`/`sortformer`), `live.sessionRoot` (path, default `~/Documents/GigaAM/live`), `live.txt`, `live.timestamps`, `live.diarize`, `live.diarizeTimestamps`, `live.md`, `live.srt`, `live.vtt`, `live.sentences`, `live.lines`, `live.characters`.

- [ ] **Step 1: Write the failing structural tests**

```python
def test_swift_live_page_is_wired_to_live_session_job() -> None:
    main = MAIN_SWIFT.read_text(encoding="utf-8")
    page = main.split("private func buildLive(into content: NSStackView)", 1)[1].split("private func buildLLM", 1)[0]
    assert "unavailableButton(" not in page and "unavailableIcon(" not in page
    assert "Захват аудио не подключён" not in page
    assert "#selector(startLive(_:))" in page and "#selector(pauseLive(_:))" in page and "#selector(stopLive(_:))" in page
    assert "#selector(askLive(_:))" in page
    assert "MicrophoneCapture.devices()" in page
    assert 'popup(["pyannote", "onnx", "sortformer"], key: "live.diarizationEngine")' in page
    assert '"live.speakers"' not in main  # Sortformer/live: no manual speaker count
    start = _swift_block(main, "@objc private func startLive(_ sender: Any?) {")
    assert "MicrophoneCapture.requestAccess" in start
    assert "SystemAudioCapture.requestAccess()" in start
    assert "LiveSessionJob(settings:" in start
    assert "Live и LLM пока не подключены" not in main
    receive = _swift_block(main, "private func receiveLiveEvent(_ event: LiveSessionEvent) {")
    assert "case .partial" in receive and "case .final" in receive and "case .stopped" in receive
    terminate = _swift_block(main, "func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {")
    assert "liveJob?.terminate()" in terminate and "llmJob?.terminate()" in terminate
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_macos_swift_packaging.py -q -p no:warnings -k live_page`
Expected: FAIL on `unavailableButton(` present.

- [ ] **Step 3: Implement**

State in `AppController`: `private var liveJob: LiveSessionJob?`, `private var liveState = "idle"`, `private var liveFinals: [(id: String, text: String, speaker: String?)] = []`, `private var livePartials: [LiveSource: String] = [:]`, `private var liveStartedAt: Date?`, `private var liveTimer: Timer?`, `private weak var liveTranscriptView: NSTextView?`, `private weak var liveClockLabel: NSTextField?`, `private weak var liveStatusLabel: NSTextField?`, `private weak var liveLevelView: ProgressTrackView?`, `private weak var liveStartButton: NSButton?`, `private weak var livePauseButton: NSButton?`, `private weak var liveStopButton: NSButton?`, `private weak var liveQuestionField: NSTextField?`, `private weak var liveAnswerView: NSTextView?`, `private var liveAnswerText = ""`.

Rewrite `buildLive`:
- «Источник аудио»: microphone popup built from `[("default", L10n.text("По умолчанию"))] + MicrophoneCapture.devices().map { ($0.id, $0.name) }` (store id in `representedObject`, persist to `live.microphone` in `popupChanged` when identifier is `live.microphone`), toggle «Системный звук» (`live.systemAudio`), toggles «Записывать микрофон»/«Записывать системный звук», popup «Диаризация» with `["Выкл.", "Оценка вживую", "После остановки"]` key `live.diarizationMode`, popup «Движок» `["pyannote", "onnx", "sortformer"]` key `live.diarizationEngine`.
- «Запись»: clock label (`liveClockLabel`), status label, `ProgressTrackView` for level (`liveLevelView`), three `iconButton`s: `record.circle` → `startLive`, `pause.fill` → `pauseLive`, `stop.fill` → `stopLive`.
- «Параметры»: exports checkboxes (seven keys above, defaults txt+srt on), subtitle popups `live.lines` `["2","1","3","4"]`, `live.characters` `["64","42","80"]`, toggle `live.sentences`, editable text `live.sessionRoot` with a «Изменить» button (`chooseLiveFolder`, same pattern as `chooseOutputFolder`).
- «Live transcript»: read-only `textEditor` stored in `liveTranscriptView`; below it a question row: `editableText` (`liveQuestionField`, identifier `live.question`, not persisted), button «Спросить» (`askLive`), button «Отменить» (`cancelAskLive`), and an answer `textEditor` (`liveAnswerView`, height 120).

Handlers:

```swift
    private var liveExports: [String: Any] {
        [
            "txt": enabledOption("live.txt", defaultValue: true), "txt_timecodes": enabledOption("live.timestamps", defaultValue: false),
            "txt_diarize": enabledOption("live.diarize", defaultValue: false), "txt_diarize_timecodes": enabledOption("live.diarizeTimestamps", defaultValue: false),
            "md": enabledOption("live.md", defaultValue: false), "srt": enabledOption("live.srt", defaultValue: true), "vtt": enabledOption("live.vtt", defaultValue: false),
            "sentence_split": enabledOption("live.sentences", defaultValue: true),
            "max_line_count": Int(option("live.lines", values: ["2", "1", "3", "4"])) ?? 2,
            "max_line_width": Int(option("live.characters", values: ["64", "42", "80"])) ?? 64,
        ]
    }

    private var liveSessionRoot: URL? {
        let raw = (defaults.string(forKey: "live.sessionRoot") ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let path = ((raw.isEmpty ? "~/Documents/GigaAM/live" : raw) as NSString).expandingTildeInPath
        guard path.hasPrefix("/") else { return nil }
        return URL(fileURLWithPath: path, isDirectory: true)
    }

    @objc private func startLive(_ sender: Any?) {
        window.makeFirstResponder(nil)
        if liveState == "paused" { liveJob?.resume(); return }
        guard liveJob == nil, transcriptionJob == nil, mediaDownloadJob == nil else { return }
        guard let root = liveSessionRoot else { showNotice("Не удалось начать запись", "Укажите папку сессий."); return }
        do { try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true) }
        catch { showNotice("Не удалось начать запись", error.localizedDescription); return }
        let wantsSystem = enabledOption("live.systemAudio", defaultValue: false)
        MicrophoneCapture.requestAccess { [weak self] granted in
            guard let self else { return }
            guard granted else { self.showNotice("Нет доступа к микрофону", "Разрешите доступ в Системных настройках → Конфиденциальность → Микрофон."); return }
            if wantsSystem, !SystemAudioCapture.requestAccess() {
                self.showNotice("Нет доступа к системному звуку", "Разрешите «Запись экрана и системного звука» для GigaAMLiquid в Системных настройках.")
                return
            }
            self.launchLive(root: root, withSystem: wantsSystem)
        }
    }

    private func launchLive(root: URL, withSystem: Bool) {
        let modeTitles = ["Выкл.": "off", "Оценка вживую": "live_estimate", "После остановки": "after_stop"]
        var settings = LiveSessionSettings(sessionRoot: root, sources: withSystem ? [.mic, .system] : [.mic])
        let device = defaults.string(forKey: "live.microphone") ?? "default"
        settings.microphoneDeviceID = device == "default" ? nil : device
        settings.diarizationMode = modeTitles[option("live.diarizationMode", values: Array(modeTitles.keys).sorted())] ?? "off"
        settings.diarizationBackend = option("live.diarizationEngine", values: ["pyannote", "onnx", "sortformer"])
        settings.recordMic = enabledOption("live.recordMic", defaultValue: true)
        settings.recordSystem = enabledOption("live.recordSystem", defaultValue: false)
        settings.exports = liveExports
        settings.backend = option("settings.backend", values: ["auto", "mlx", "onnx", "pytorch"])
        settings.model = option("settings.model", values: ["v3_e2e_rnnt", "multilingual_ctc", "multilingual_large_ctc"])
        settings.onnxProvider = option("settings.onnxProvider", values: ["auto", "cpu", "cuda", "tensorrt", "coreml", "directml"])
        settings.hfToken = SecureStore.string(for: "hfToken")
        var job: LiveSessionJob!
        let forward: (LiveCaptureEvent) -> Void = { event in job?.handleCapture(event) }
        var captures: [LiveCaptureSource] = [MicrophoneCapture(deviceID: settings.microphoneDeviceID, onEvent: forward)]
        if withSystem { captures.append(SystemAudioCapture(onEvent: forward)) }
        job = LiveSessionJob(settings: settings, captures: captures) { [weak self] event in self?.receiveLiveEvent(event) }
        liveJob = job
        liveFinals = []; livePartials = [:]; liveAnswerText = ""
        liveStartedAt = Date()
        liveTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in self?.refreshLiveClock() }
        liveState = "starting"
        refreshLiveControls()
        renderLiveTranscript()
        job.start()
    }

    @objc private func pauseLive(_ sender: Any?) { liveJob?.pause() }
    @objc private func stopLive(_ sender: Any?) { liveJob?.stop() }

    @objc private func askLive(_ sender: Any?) {
        guard let job = liveJob, let question = liveQuestionField?.stringValue.trimmingCharacters(in: .whitespacesAndNewlines), !question.isEmpty else { return }
        do { job.ask(question, settings: try llmSettings()) } catch { showNotice("LLM не настроена", error.localizedDescription); return }
        liveAnswerText = ""; liveAnswerView?.string = L10n.text("Ассистент отвечает…")
    }

    @objc private func cancelAskLive(_ sender: Any?) { liveJob?.cancelAsk() }

    private func receiveLiveEvent(_ event: LiveSessionEvent) {
        switch event {
        case .status(let state, _, let failed):
            liveState = state
            if !failed.isEmpty { liveStatusLabel?.stringValue = L10n.text("Источник недоступен: ") + failed.map(\.rawValue).joined(separator: ", ") }
            else { liveStatusLabel?.stringValue = L10n.text(["recording": "Идёт запись", "paused": "Пауза", "starting": "Запуск…", "stopping": "Остановка…"][state] ?? state) }
            refreshLiveControls()
        case .partial(_, let source, _, let text):
            livePartials[source] = text; renderLiveTranscript()
        case .final(let id, _, _, _, let text, let speaker):
            livePartials.removeAll()
            if let index = liveFinals.firstIndex(where: { $0.id == id }) { liveFinals[index] = (id, text, speaker) } else { liveFinals.append((id, text, speaker)) }
            renderLiveTranscript()
        case .level(_, let rms): liveLevelView?.fraction = Double(min(1, rms * 4))
        case .captureEvent(_, let kind, let detail):
            if kind != "status" { liveStatusLabel?.stringValue = detail }
            transcriptionLog += "[live/\(kind)] \(detail)\n"
        case .answerChunk(_, let text):
            liveAnswerText += text; liveAnswerView?.string = liveAnswerText
        case .answer(_, let status, let text):
            liveAnswerView?.string = status == "complete" ? text : (status == "cancelled" ? L10n.text("Запрос отменён.") : text)
        case .stopped(let dir, let saved):
            finishLive(status: L10n.text("Сессия сохранена: ") + dir.lastPathComponent + (saved.isEmpty ? "" : " · " + saved.map(\.lastPathComponent).joined(separator: ", ")))
        case .failed(let message):
            transcriptionLog += message + "\n"; finishLive(status: message)
        case .log(let message): transcriptionLog += message + "\n"
        }
    }

    private func finishLive(status: String) {
        liveJob = nil; liveState = "idle"; liveTimer?.invalidate(); liveTimer = nil
        liveStatusLabel?.stringValue = status
        if isTerminating { replyWhenJobsFinished(); return }
        refreshLiveControls()
    }

    private func renderLiveTranscript() {
        var lines = liveFinals.map { ($0.speaker.map { "\($0): " } ?? "") + $0.text }
        for (source, text) in livePartials.sorted(by: { $0.key.rawValue < $1.key.rawValue }) { lines.append("[\(source.rawValue) …] \(text)") }
        liveTranscriptView?.string = lines.joined(separator: "\n")
        liveTranscriptView?.scrollToEndOfDocument(nil)
    }

    private func refreshLiveClock() {
        guard let started = liveStartedAt else { return }
        let seconds = Int(Date().timeIntervalSince(started))
        liveClockLabel?.stringValue = String(format: "%02d:%02d:%02d", seconds / 3600, seconds % 3600 / 60, seconds % 60)
    }

    private func refreshLiveControls() {
        let running = liveJob != nil
        liveStartButton?.isEnabled = !running || liveState == "paused"
        livePauseButton?.isEnabled = running && liveState == "recording"
        liveStopButton?.isEnabled = running
        liveQuestionField?.isEnabled = running
        for key in ["live.microphone", "live.systemAudio", "live.recordMic", "live.recordSystem", "live.diarizationMode", "live.diarizationEngine", "live.sessionRoot"] {
            (window.contentView.map { findControl($0, key) })??.isEnabled = !running
        }
    }
```

`findControl(_:_:)` is a small recursive helper returning the first `NSControl` whose identifier matches. In `applicationShouldTerminate` and `windowWillClose` add `liveJob?.terminate()` and `llmJob?.terminate()` next to the existing transcription teardown; `replyWhenJobsFinished` must also require `liveJob == nil && llmJob == nil`. Update `refreshProcessingControls` so the batch start button is disabled while `liveJob != nil` with reason «Дождитесь завершения live-сессии.» Replace the About text «Импорт медиа и распознавание речи используют встроенные Python-сервисы проекта. Live и LLM пока не подключены.» with «Импорт медиа, распознавание, live-захват и LLM используют встроенные Python-сервисы проекта.» (add the English translation, drop the old key). Add every new string to `Localization.swift`.

- [ ] **Step 4: Build, test, verify with real hardware**

```bash
cd macos/GigaAMLiquid && swift build 2>&1 | grep -E "error:|warning:|Build complete" | grep -v "search path"; cd ../..
.venv/bin/python -m pytest tests/test_macos_swift_packaging.py -q -p no:warnings
```
Manual (this machine): run the debug binary with `GIGAAM_PROJECT_ROOT=$PWD`, open Live, press record, grant microphone, speak for 10 s, confirm partial → final lines appear, press stop, confirm status names the session folder and `transcript.txt` exists there. Repeat with «Системный звук» on while playing any audio; expect the screen-recording prompt once. Ask «о чём речь?» with provider «Other» = `/bin/cat` and confirm the answer view shows the echoed context. Screenshot both states.

- [ ] **Step 5: Commit**

```bash
git add macos/GigaAMLiquid/Sources/GigaAMLiquid/main.swift macos/GigaAMLiquid/Sources/GigaAMLiquid/Localization.swift tests/test_macos_swift_packaging.py
git commit -m "Wire the Liquid Live page to native capture and the live worker protocol

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: Release 2.1.0

**Files:**
- Modify: `packaging/_spec_common.py`, `src/__init__.py`, `tests/test_macos_packaging_config.py`, `tests/test_release_hardening.py`, `desktop/package.json`, `desktop/package-lock.json` (both `"version"` lines), `desktop/src-tauri/tauri.conf.json`, `desktop/src-tauri/Cargo.toml`, `desktop/src-tauri/Cargo.lock` (only the `gigaam-desktop` package), `docs/CHANGELOG.md`, `docs/RELEASE_NOTES_2.1.0.md` (new), `macos/GigaAMLiquid/README.md`

- [ ] **Step 1: Bump versions and write the failing version test**

Change `expected = "2.0.5"` to `"2.1.0"` in `tests/test_release_hardening.py` and the literal in `tests/test_macos_packaging_config.py`; run `pytest tests/test_release_hardening.py -k version` and watch it fail, then bump every other file to `2.1.0` and watch it pass.

- [ ] **Step 2: Changelog and release notes**

Add a `## [2.1.0] - <date>` section to `docs/CHANGELOG.md` under `### Добавлено`: live capture in Liquid (mic via AVAudioEngine, system audio via ScreenCaptureKit, 16 kHz PCM over the worker protocol, assistant questions), LLM page with six providers and Keychain key, worker commands `live_*`, `llm_cancel`, `llm_start.text`, CI live smoke; under `### Изменено`: `LLMWorkerService`/`LiveWorkerService` extracted, `LazyModelBackend` moved out of the GUI. Write `docs/RELEASE_NOTES_2.1.0.md` with the same structure as `docs/RELEASE_NOTES_2.0.5.md` (Russian sections, then an English paragraph), including the manual verification performed in Task 12 and the note that recordings are 16 kHz. Update `macos/GigaAMLiquid/README.md` so it no longer says Live/LLM are unavailable.

- [ ] **Step 3: Full verification**

```bash
rm -f tests/*sync-conflict*.py src/*sync-conflict*.py src/gui/*sync-conflict*.py src/live/*sync-conflict*.py ./*sync-conflict*.py
.venv/bin/python -m pytest tests/ -q -p no:warnings
.venv/bin/python -m ruff check src/ tests/ packaging/ web/ scripts/ *.py
cd macos/GigaAMLiquid && swift build -c release 2>&1 | grep -E "error:|warning:|Build complete" | grep -v "search path"; cd ../..
graphify update .
```
Expected: pytest exit 0, ruff clean, release build clean. Then assemble `dist/GigaAMLiquid.app` with the local script from the 2.0.5 session (`swift build -c release`, copy binary, Info.plist with `2.1.0`, `codesign --force --deep --sign -`), launch it next to `dist/GigaAMTranscriber.app`, and repeat the Task 12 manual checks on the release build.

- [ ] **Step 4: Commit, tag, push**

```bash
git add -A -- packaging src/__init__.py tests desktop macos/GigaAMLiquid/README.md
git add -f docs/CHANGELOG.md docs/RELEASE_NOTES_2.1.0.md
git commit -F - <<'MSG'
Add Live capture and LLM to GigaAMLiquid, release 2.1.0

Live: Swift owns AVAudioEngine/ScreenCaptureKit capture and pushes 16 kHz
int16 PCM as base64 JSON lines; the worker feeds PushCaptureAdapter into
the unchanged LiveSession. LLM: the page and settings drive LLMJob over
llm_start/llm_cancel with streamed chunks; keys live in Keychain.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
MSG
git tag -a v2.1.0 -m "GigaAM Transcriber 2.1.0"
git push origin main && git push origin v2.1.0
```
Then watch `gh run list --repo dubr1k/GigaAMGUI --limit 2` until «Build portable» is green and the release page lists both Liquid archives.

---

## Self-review

- Spec coverage: protocol tables → Tasks 3, 4, 5; `PushCaptureAdapter` → 1; `LazyModelBackend` → 2; services → 3, 4, 5; Swift capture → 10; jobs → 8, 11; pages → 9, 12; secrets → 9 (Keychain) and 8/11 (redaction); error handling (worker death, permission denied, overflow, mutual exclusion) → 4, 11, 12; CI smoke → 6; release → 13; out-of-scope items untouched.
- Placeholders: none; every code step is concrete. Task 6 flags two draft details (seq ceil, `--worker-arg`) with the exact replacement inline.
- Type consistency: `PushCaptureAdapter.push(seq, sample_offset, timestamp_ns, frames)` used identically in Tasks 1 and 4; `WorkerProcess` API from Task 7 used in 8 and 11; `LiveCaptureEvent`/`LiveAudioChunk` from 10 used in 11 and 12; `llmSettings()` from 9 used in 12; `WorkerFailure` from 7 used in 9 and 10.
