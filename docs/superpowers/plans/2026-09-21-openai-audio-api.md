# OpenAI-compatible Audio API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the bespoke `/api/v1/*` task-based REST API with an OpenAI Audio API-compatible one (`POST /v1/audio/transcriptions`, `GET /v1/models`, Bearer auth, OpenAI error envelope, SSE streaming) and update every consumer, test and doc.

**Architecture:** `api.py` becomes a thin FastAPI app: lifespan (model, keys, limiter, CORS), OpenAI error handlers, three routes. Response bodies are built by a pure module `src/services/transcript_formats.py` from the utterance list that `TranscriptionProcessor.process_file` now returns in `result["utterances"]`. Everything task-related (`tasks_storage`, restore, cleanup, batch, download) is deleted.

**Tech Stack:** Python 3.11, FastAPI, Starlette `StreamingResponse` (SSE), slowapi, pytest + `fastapi.testclient`, existing `src/core/formatters` (SRT/VTT).

**Spec:** `docs/superpowers/specs/2026-09-21-openai-audio-api-design.md`

## Global Constraints

- Branch `api-openai` off `main` in a git worktree (`.worktrees/api-openai`); the main checkout stays on `tui-2`. Python for tests: `/Users/dubr1k/Syncthing/development/GigaAMv3/.venv/bin/python` (absolute; the worktree has no venv). Run from the worktree root.
- Tests: `.venv/bin/python -m pytest tests/ -q`; lint `.venv/bin/python -m ruff check .`. Compare failures against a clean `main` — 4 pre-existing DPI/GUI failures are known.
- No AI attribution in commits (no `Co-Authored-By`, no "Generated with"). Commit per task; never push.
- Error messages in API responses are English (machine contract). Docs/README sections follow the language of the file they live in (README.md Russian, README_EN.md English, `docs/API.md` Russian with English code samples).
- Never import `src.gui` from api/web/cli code.
- Keep `.api_keys` format and `load_api_keys/save_api_keys/_hash_key/safe_filename` names — `tests/test_api_security.py` keeps using them.
- OpenAI wire names are exact: `transcript.text.delta`, `transcript.text.done`, `transcript.text.segment`, `usage: {"type": "duration", "seconds": N}`, error envelope `{"error": {"message","type","param","code"}}`.

---

### Task 0: Worktree and branch

**Files:** none (git only)

- [ ] **Step 1: Create the worktree from main**

```bash
cd /Users/dubr1k/Syncthing/development/GigaAMv3
git worktree add .worktrees/api-openai -b api-openai main
cd .worktrees/api-openai
git log --oneline -1   # expect main HEAD (129f97a or later)
```

- [ ] **Step 2: Copy the spec and plan into the branch and commit**

The spec/plan were written on the `tui-2` checkout as untracked files; copy them:

```bash
cp ../../docs/superpowers/specs/2026-09-21-openai-audio-api-design.md docs/superpowers/specs/
cp ../../docs/superpowers/plans/2026-09-21-openai-audio-api.md docs/superpowers/plans/
git add docs/superpowers/specs/2026-09-21-openai-audio-api-design.md docs/superpowers/plans/2026-09-21-openai-audio-api.md
git commit -m "API: design spec and plan for the OpenAI-compatible audio API"
```

- [ ] **Step 3: Baseline tests**

```bash
/Users/dubr1k/Syncthing/development/GigaAMv3/.venv/bin/python -m pytest tests/ -q -x --deselect tests/test_gui_layout.py 2>&1 | tail -3
```

Record the failing set (expected: only the known DPI/GUI failures).

---

### Task 1: `process_file` returns utterances

**Files:**
- Modify: `src/core/processor.py` (inside `process_file`, right after `result['saved_files'] = saved_files`, ~line 621)
- Test: `tests/test_processor_utterances.py` (create)

**Interfaces:**
- Produces: `result["utterances"]: list[dict]` — each `{"transcription": str, "boundaries": (start: float, end: float), "words"?: [{"text","start","end"}], "speaker"?: str}` exactly as the backend/diarizer produced them. Present only on success (`result["success"] is True`).

- [ ] **Step 1: Write the failing test**

```python
"""process_file must hand utterances back to callers (the API builds responses from them)."""
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.core.processor import TranscriptionProcessor


def _fake_utterances():
    return [
        {"transcription": "привет", "boundaries": (0.0, 1.0)},
        {"transcription": "мир", "boundaries": (1.0, 2.0), "words": [{"text": "мир", "start": 1.0, "end": 2.0}]},
    ]


def test_process_file_returns_utterances(tmp_path):
    loader = MagicMock()
    loader.transcribe_longform.return_value = _fake_utterances()
    processor = TranscriptionProcessor(loader, MagicMock(), logger=lambda *_: None)
    src = tmp_path / "a.wav"
    src.write_bytes(b"RIFF")
    with patch("src.core.processor.AudioConverter") as conv:
        conv.get_media_duration.return_value = 2.0
        conv.return_value.convert_to_wav.return_value = (str(src), 0.0)
        with patch.object(processor, "_prepare_audio", return_value=(str(src), None), create=True):
            result = processor.process_file(str(src), str(tmp_path), 0, 1, "a.wav", output_formats=[])
    assert result["success"] is True
    assert result["utterances"] == _fake_utterances()
    assert result["saved_files"] == []
```

Before writing the test, read `process_file` lines 194-300 to see exactly how conversion/preprocessing is invoked and patch the real names (the test above guesses `AudioConverter`/`convert_to_wav`; adapt the patches so the call reaches `transcribe_longform` without ffmpeg). Keep the assertions.

- [ ] **Step 2: Run it — must fail**

Run: `.venv/bin/python -m pytest tests/test_processor_utterances.py -v`
Expected: FAIL with `KeyError: 'utterances'`.

- [ ] **Step 3: Implement**

In `src/core/processor.py`, next to `result['saved_files'] = saved_files`:

```python
            result['saved_files'] = saved_files
            # Реплики нужны API, который собирает ответ в памяти, не читая файлы.
            result['utterances'] = utterances
```

- [ ] **Step 4: Run — pass; run the processor suite**

Run: `.venv/bin/python -m pytest tests/test_processor_utterances.py tests/ -q -k "processor" `
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/core/processor.py tests/test_processor_utterances.py
git commit -m "Processor: return utterances in the process_file result

The REST API is about to build OpenAI-style responses in memory; reading
the .txt it asked the processor to write was the only way to get the text
back, and it lost timestamps, words and speakers."
```

---

### Task 2: `transcript_formats` — pure response builders

**Files:**
- Create: `src/services/transcript_formats.py`
- Test: `tests/test_transcript_formats.py`

**Interfaces:**
- Produces:
  ```python
  Utterance = dict  # {"transcription", "boundaries": (s, e), "words"?, "speaker"?}
  def full_text(utts: list[Utterance]) -> str
  def usage(duration: float) -> dict           # {"type": "duration", "seconds": ceil(duration)}
  def build_json(utts, duration) -> dict       # {"text", "usage"}
  def build_verbose(utts, duration, language: str | None, granularities: set[str], diarized: bool) -> dict
  def build_diarized(utts, duration) -> dict
  def build_text(utts) -> str
  def build_srt(utts, options: SubtitleOptions | None = None) -> str
  def build_vtt(utts, options: SubtitleOptions | None = None) -> str
  def speaker_letters(utts) -> dict[str, str]  # {"SPEAKER_00": "A", ...} by first appearance
  def render(fmt: str, utts, duration, *, language, granularities, diarized, subtitle_options) -> tuple[str | dict, str]
      # returns (body, media_type); fmt in {"json","text","srt","vtt","verbose_json","diarized_json"}
  ```

- [ ] **Step 1: Write the failing tests**

```python
"""Pure OpenAI-shaped response builders (no FastAPI)."""
import json

import pytest

from src.services import transcript_formats as tf

UTTS = [
    {"transcription": "Привет,", "boundaries": (0.0, 1.5), "speaker": "SPEAKER_01"},
    {"transcription": "как дела?", "boundaries": (1.5, 3.25), "speaker": "SPEAKER_00",
     "words": [{"text": "как", "start": 1.5, "end": 2.0}, {"text": "дела?", "start": 2.0, "end": 3.25}]},
]


def test_full_text_joins_with_single_spaces():
    assert tf.full_text(UTTS) == "Привет, как дела?"
    assert tf.full_text([]) == ""


def test_usage_rounds_duration_up():
    assert tf.usage(3.25) == {"type": "duration", "seconds": 4}
    assert tf.usage(0.0) == {"type": "duration", "seconds": 0}


def test_build_json():
    assert tf.build_json(UTTS, 3.25) == {"text": "Привет, как дела?", "usage": {"type": "duration", "seconds": 4}}


def test_build_verbose_segments_have_openai_fields():
    out = tf.build_verbose(UTTS, 3.25, "ru", {"segment"}, diarized=False)
    assert out["task"] == "transcribe" and out["language"] == "ru" and out["duration"] == 3.25
    assert out["text"] == "Привет, как дела?"
    seg = out["segments"][1]
    assert seg == {
        "id": 1, "seek": 0, "start": 1.5, "end": 3.25, "text": "как дела?", "tokens": [],
        "temperature": 0.0, "avg_logprob": 0.0, "compression_ratio": 0.0, "no_speech_prob": 0.0,
    }
    assert "words" not in out and "speaker" not in seg


def test_build_verbose_words_and_speakers():
    out = tf.build_verbose(UTTS, 3.25, None, {"segment", "word"}, diarized=True)
    assert out["language"] == "ru"  # default when unknown
    assert out["words"] == [{"word": "как", "start": 1.5, "end": 2.0}, {"word": "дела?", "start": 2.0, "end": 3.25}]
    assert [s["speaker"] for s in out["segments"]] == ["A", "B"]


def test_build_verbose_word_granularity_without_backend_words_gives_empty_list():
    out = tf.build_verbose([{"transcription": "x", "boundaries": (0, 1)}], 1.0, "ru", {"word"}, diarized=False)
    assert out["words"] == []


def test_speaker_letters_by_first_appearance():
    assert tf.speaker_letters(UTTS) == {"SPEAKER_01": "A", "SPEAKER_00": "B"}


def test_build_diarized():
    out = tf.build_diarized(UTTS, 3.25)
    assert out["task"] == "transcribe" and out["duration"] == 3.25
    assert out["segments"][0] == {"id": 0, "type": "transcript.text.segment", "start": 0.0, "end": 1.5, "speaker": "A", "text": "Привет,"}
    assert out["text"] == "Привет, как дела?"


def test_build_diarized_without_speakers_uses_single_speaker_a():
    out = tf.build_diarized([{"transcription": "x", "boundaries": (0, 1)}], 1.0)
    assert out["segments"][0]["speaker"] == "A"


def test_srt_and_vtt_delegate_to_formatters():
    srt = tf.build_srt(UTTS)
    vtt = tf.build_vtt(UTTS)
    assert srt.startswith("1\n00:00:00,000 --> ")
    assert vtt.startswith("WEBVTT")


@pytest.mark.parametrize("fmt,media", [
    ("json", "application/json"), ("verbose_json", "application/json"), ("diarized_json", "application/json"),
    ("text", "text/plain; charset=utf-8"), ("srt", "application/x-subrip"), ("vtt", "text/vtt"),
])
def test_render_media_types(fmt, media):
    body, media_type = tf.render(fmt, UTTS, 3.25, language="ru", granularities={"segment"}, diarized=True, subtitle_options=None)
    assert media_type == media
    if media == "application/json":
        json.dumps(body)  # serialisable
    else:
        assert isinstance(body, str)


def test_render_unknown_format_raises():
    with pytest.raises(ValueError):
        tf.render("xml", UTTS, 1.0, language=None, granularities=set(), diarized=False, subtitle_options=None)
```

- [ ] **Step 2: Run — fail** (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `src/services/transcript_formats.py`**

```python
"""Сборка ответов в формате OpenAI Audio API из списка реплик процессора.

Чистые функции без FastAPI — api.py только выбирает формат и media type.
"""
from __future__ import annotations

import math
from typing import Any

from src.core import formatters
from src.core.subtitles import SubtitleOptions

Utterance = dict[str, Any]

FORMATS = ("json", "text", "srt", "vtt", "verbose_json", "diarized_json")
MEDIA_TYPES = {
    "json": "application/json",
    "verbose_json": "application/json",
    "diarized_json": "application/json",
    "text": "text/plain; charset=utf-8",
    "srt": "application/x-subrip",
    "vtt": "text/vtt",
}
DEFAULT_LANGUAGE = "ru"


def _bounds(utt: Utterance) -> tuple[float, float]:
    start, end = utt.get("boundaries", (0.0, 0.0))
    return float(start), float(end)


def full_text(utts: list[Utterance]) -> str:
    return " ".join(part for part in (u.get("transcription", "").strip() for u in utts) if part)


def usage(duration: float) -> dict[str, Any]:
    return {"type": "duration", "seconds": int(math.ceil(max(float(duration or 0.0), 0.0)))}


def speaker_letters(utts: list[Utterance]) -> dict[str, str]:
    """SPEAKER_00 → 'A' по порядку первого появления, как в diarized_json OpenAI."""
    letters: dict[str, str] = {}
    for utt in utts:
        speaker = utt.get("speaker")
        if speaker and speaker not in letters:
            letters[speaker] = _letter(len(letters))
    return letters


def _letter(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(ord("A") + rem) + name
    return name


def build_json(utts: list[Utterance], duration: float) -> dict[str, Any]:
    return {"text": full_text(utts), "usage": usage(duration)}


def build_text(utts: list[Utterance]) -> str:
    return full_text(utts)


def build_verbose(
    utts: list[Utterance],
    duration: float,
    language: str | None,
    granularities: set[str],
    diarized: bool,
) -> dict[str, Any]:
    letters = speaker_letters(utts) if diarized else {}
    segments = []
    words: list[dict[str, Any]] = []
    for index, utt in enumerate(utts):
        start, end = _bounds(utt)
        segment: dict[str, Any] = {
            "id": index,
            "seek": 0,
            "start": start,
            "end": end,
            "text": utt.get("transcription", ""),
            "tokens": [],
            "temperature": 0.0,
            "avg_logprob": 0.0,
            "compression_ratio": 0.0,
            "no_speech_prob": 0.0,
        }
        if diarized:
            segment["speaker"] = letters.get(utt.get("speaker"), "A")
        segments.append(segment)
        for word in utt.get("words") or []:
            words.append({"word": word["text"], "start": float(word["start"]), "end": float(word["end"])})
    out: dict[str, Any] = {
        "task": "transcribe",
        "language": language or DEFAULT_LANGUAGE,
        "duration": float(duration or 0.0),
        "text": full_text(utts),
        "segments": segments,
    }
    if "word" in granularities:
        out["words"] = words
    return out


def build_diarized(utts: list[Utterance], duration: float) -> dict[str, Any]:
    letters = speaker_letters(utts)
    segments = []
    for index, utt in enumerate(utts):
        start, end = _bounds(utt)
        segments.append({
            "id": index,
            "type": "transcript.text.segment",
            "start": start,
            "end": end,
            "speaker": letters.get(utt.get("speaker"), "A"),
            "text": utt.get("transcription", ""),
        })
    return {"task": "transcribe", "duration": float(duration or 0.0), "text": full_text(utts), "segments": segments}


def build_srt(utts: list[Utterance], options: SubtitleOptions | None = None) -> str:
    return formatters.generate_srt(utts, options)


def build_vtt(utts: list[Utterance], options: SubtitleOptions | None = None) -> str:
    return formatters.generate_vtt(utts, options)


def render(
    fmt: str,
    utts: list[Utterance],
    duration: float,
    *,
    language: str | None,
    granularities: set[str],
    diarized: bool,
    subtitle_options: SubtitleOptions | None,
) -> tuple[str | dict[str, Any], str]:
    if fmt not in MEDIA_TYPES:
        raise ValueError(f"unsupported response_format: {fmt}")
    if fmt == "json":
        body: str | dict[str, Any] = build_json(utts, duration)
    elif fmt == "verbose_json":
        body = build_verbose(utts, duration, language, granularities, diarized)
    elif fmt == "diarized_json":
        body = build_diarized(utts, duration)
    elif fmt == "text":
        body = build_text(utts)
    elif fmt == "srt":
        body = build_srt(utts, subtitle_options)
    else:
        body = build_vtt(utts, subtitle_options)
    return body, MEDIA_TYPES[fmt]
```

Check `formatters.generate_srt` handles a `speaker` key without `diarized` flag (it prints `speaker_label` when present — that is fine for SRT with diarization; without diarization there is no key).

- [ ] **Step 4: Run — pass; ruff**

Run: `.venv/bin/python -m pytest tests/test_transcript_formats.py -v && .venv/bin/python -m ruff check src/services/transcript_formats.py tests/test_transcript_formats.py`

- [ ] **Step 5: Commit**

```bash
git add src/services/transcript_formats.py tests/test_transcript_formats.py
git commit -m "API: pure builders for OpenAI transcription response formats"
```

---

### Task 3: Rewrite `api.py` — app, auth, errors, `/v1/models`, `/health`

**Files:**
- Rewrite: `api.py` (delete everything task-related; keep config, key store, `safe_filename`, `is_supported_format`, `_asr_health`, `_runtime_info`, lifespan skeleton, limiter, CORS)
- Delete: `tests/test_api_integration.py`, `tests/test_api_progress.py`
- Modify: `tests/test_api_security.py` (drop the `validated_task_id` tests — the function is removed; keep everything else)
- Test: `tests/test_api_openai.py` (create — this task adds the auth/models/errors part; Task 4 adds transcription tests to the same file)

**Interfaces:**
- Produces (module `api`): `app`, `model_loader`, `stats_manager`, `logger`, `processing_semaphore`, `MAX_FILE_SIZE`, `UPLOAD_DIR`, `VALID_API_KEY_HASHES`, `load_api_keys()`, `save_api_keys()`, `_hash_key()`, `safe_filename()`, `is_supported_format()`, `verify_api_key(authorization: str | None = Header(None), x_api_key: str | None = Header(None, alias="X-API-Key")) -> str`, `class OpenAIError(Exception)` with `status_code, message, type, param, code` and `def openai_error(status, message, *, type_="invalid_request_error", param=None, code=None) -> OpenAIError`, `MODEL_ALIASES: dict[str, str]`, `resolve_model(model: str) -> str` (raises `OpenAIError` 404 `model_not_found`), `def models_payload() -> dict`.
- Consumes: `transcript_formats` (Task 4 uses it; Task 3 only imports).

- [ ] **Step 1: Write failing tests (`tests/test_api_openai.py`, first half)**

```python
"""OpenAI-compatible API: auth, models, error envelope. Model and processor are fakes."""
import importlib
import json

import pytest

try:
    from fastapi.testclient import TestClient
    _HAS_CLIENT = True
except Exception:  # pragma: no cover
    _HAS_CLIENT = False

pytestmark = pytest.mark.skipif(not _HAS_CLIENT, reason="нужен fastapi TestClient")

api = importlib.import_module("api")
VALID_KEY = "gam_openai_test"


class _FakeModelLoader:
    requested_backend = "auto"
    requested_model = "v3_e2e_rnnt"
    requested_provider = "auto"

    def load_model(self, logger=None):
        return True

    def is_loaded(self):
        return True

    def diagnostics(self):
        return {"requested_backend": "auto", "active_backend": "mlx", "model": "v3_e2e_rnnt", "device": "mps"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api, "ModelLoader", _FakeModelLoader)
    monkeypatch.setattr(api, "HF_TOKEN", "hf_dummytoken")
    monkeypatch.setattr(api, "VALID_API_KEY_HASHES", {api._hash_key(VALID_KEY)})
    monkeypatch.setattr(api, "load_api_keys", lambda: None)
    monkeypatch.setattr(api.limiter, "enabled", False)  # 10/minute would trip across the module
    with TestClient(api.app) as c:
        yield c


BEARER = {"Authorization": f"Bearer {VALID_KEY}"}


def _error(resp):
    body = resp.json()
    assert set(body) == {"error"} and set(body["error"]) == {"message", "type", "param", "code"}
    return body["error"]


def test_health_needs_no_key(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["model_loaded"] is True


def test_root_points_to_docs(client):
    body = client.get("/").json()
    assert body["docs"] == "/docs" and "/v1/audio/transcriptions" in json.dumps(body)


def test_models_require_key_in_openai_envelope(client):
    r = client.get("/v1/models")
    assert r.status_code == 401
    err = _error(r)
    assert err["type"] == "authentication_error" and err["code"] == "invalid_api_key"


def test_models_accept_bearer_and_x_api_key(client):
    assert client.get("/v1/models", headers=BEARER).status_code == 200
    assert client.get("/v1/models", headers={"X-API-Key": VALID_KEY}).status_code == 200
    assert client.get("/v1/models", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_models_list_shape(client):
    body = client.get("/v1/models", headers=BEARER).json()
    assert body["object"] == "list"
    ids = [m["id"] for m in body["data"]]
    assert ids == ["v3_e2e_rnnt", "multilingual_ctc", "multilingual_large_ctc"]
    default = body["data"][0]
    assert default["object"] == "model" and default["owned_by"] == "gigaam" and default["created"] == 0
    assert default["default"] is True and "whisper-1" in default["aliases"]
    assert body["gigaam"]["active"]["active_backend"] == "mlx"
    assert "backends" in body["gigaam"] and "onnx_providers" in body["gigaam"]


def test_model_by_id_and_alias(client):
    assert client.get("/v1/models/whisper-1", headers=BEARER).json()["id"] == "v3_e2e_rnnt"
    r = client.get("/v1/models/nope", headers=BEARER)
    assert r.status_code == 404 and _error(r)["code"] == "model_not_found"


def test_old_routes_are_gone_with_openai_404(client):
    r = client.post("/api/v1/transcribe", headers=BEARER)
    assert r.status_code == 404 and _error(r)["type"] == "invalid_request_error"


def test_translations_rejected(client):
    r = client.post("/v1/audio/translations", headers=BEARER, files={"file": ("a.wav", b"RIFF", "audio/wav")}, data={"model": "whisper-1"})
    assert r.status_code == 400 and _error(r)["code"] == "translation_not_supported"


def test_validation_errors_use_envelope(client):
    r = client.post("/v1/audio/transcriptions", headers=BEARER, data={"model": "whisper-1"})  # no file
    assert r.status_code == 422
    err = _error(r)
    assert err["type"] == "invalid_request_error" and err["param"] == "file"
```

- [ ] **Step 2: Run — fail** (routes missing / wrong envelope).

- [ ] **Step 3: Rewrite `api.py`**

Replace the whole file. Skeleton (Task 4 fills `create_transcription`):

```python
"""GigaAM v3 Transcriber — REST API, совместимый с OpenAI Audio API.

POST /v1/audio/transcriptions, GET /v1/models, GET /health. Клиенты OpenAI SDK
работают, поменяв base_url и ключ. Формат ошибок — конверт OpenAI.
"""

import asyncio
import hashlib
import hmac
import os
import re
import shutil
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from platform import machine
from platform import platform as runtime_platform
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException as StarletteHTTPException

from src import __version__
from src.config import AUDIO_PREPROCESSING_MODE, HF_TOKEN, SUPPORTED_FORMATS
from src.core.asr.models import ASR_MODELS
from src.core.model_loader import ModelLoader
from src.services import file_policy, transcript_formats, transcription_service
from src.services import health as health_service
from src.utils.audio_converter import ffmpeg_available
from src.utils.diarization import normalize_diarization_backend
from src.utils.logger import setup_logger
from src.utils.processing_stats import ProcessingStats

# (pyannote patch block, load_dotenv block, API_KEYS_FILE, CORS_ORIGINS, API_DEBUG,
#  UPLOAD_DIR, MAX_FILE_SIZE, MAX_CONCURRENT_TASKS, API_HOST/PORT/WORKERS — copy verbatim
#  from the old file; drop RESULTS_DIR, TASK_CLEANUP_HOURS, TASK_ID_RE)

model_loader = None
stats_manager = None
logger = None
processing_semaphore = None

# ==================== OpenAI error envelope ====================


class OpenAIError(Exception):
    def __init__(self, status_code: int, message: str, *, type_: str = "invalid_request_error",
                 param: str | None = None, code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.type = type_
        self.param = param
        self.code = code

    def payload(self) -> dict[str, Any]:
        return {"error": {"message": self.message, "type": self.type, "param": self.param, "code": self.code}}


def openai_error(status: int, message: str, *, type_: str = "invalid_request_error",
                 param: str | None = None, code: str | None = None) -> OpenAIError:
    return OpenAIError(status, message, type_=type_, param=param, code=code)


_STATUS_TYPES = {401: "authentication_error", 429: "rate_limit_error"}


def _type_for_status(status: int) -> str:
    if status in _STATUS_TYPES:
        return _STATUS_TYPES[status]
    return "server_error" if status >= 500 else "invalid_request_error"


# ==================== ключи (unchanged) ====================
# _HASH_RE, _hash_key, load_api_keys, save_api_keys — copy verbatim


def verify_api_key(
    authorization: str | None = Header(None),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
) -> str:
    """Bearer (как у OpenAI SDK) или X-API-Key; сравнение хэшей constant-time."""
    key = None
    if authorization and authorization.lower().startswith("bearer "):
        key = authorization[7:].strip()
    elif x_api_key:
        key = x_api_key.strip()
    if not key:
        raise openai_error(401, "Missing API key. Send 'Authorization: Bearer <key>'.",
                           type_="authentication_error", code="invalid_api_key")
    candidate = _hash_key(key)
    if not any(hmac.compare_digest(candidate, valid) for valid in VALID_API_KEY_HASHES):
        raise openai_error(401, "Incorrect API key provided.", type_="authentication_error", code="invalid_api_key")
    return key


def is_supported_format(filename: str) -> bool:
    return file_policy.is_supported_by_glob(filename, SUPPORTED_FORMATS[1])


def safe_filename(filename: str | None) -> str:
    return file_policy.safe_filename(filename)


# ==================== модели ====================

DEFAULT_MODEL = "v3_e2e_rnnt"
MODEL_ALIASES = {
    "whisper-1": DEFAULT_MODEL,
    "gpt-4o-transcribe": DEFAULT_MODEL,
    "gpt-4o-mini-transcribe": DEFAULT_MODEL,
    "gigaam": DEFAULT_MODEL,
}


def resolve_model(model: str | None) -> str:
    name = (model or DEFAULT_MODEL).strip()
    name = MODEL_ALIASES.get(name, name)
    if name not in ASR_MODELS:
        raise openai_error(404, f"The model '{model}' does not exist.", param="model", code="model_not_found")
    return name


def _model_object(model_id: str) -> dict[str, Any]:
    aliases = sorted(alias for alias, target in MODEL_ALIASES.items() if target == model_id)
    return {
        "id": model_id,
        "object": "model",
        "created": 0,
        "owned_by": "gigaam",
        "description": ASR_MODELS[model_id],
        "default": model_id == DEFAULT_MODEL,
        "aliases": aliases,
    }


def models_payload() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [_model_object(m) for m in ASR_MODELS],
        "gigaam": {
            "backends": transcription_service.available_asr_backends(),
            "onnx_providers": list(transcription_service.ONNX_PROVIDERS),
            "active": model_loader.diagnostics() if model_loader is not None else {},
        },
    }


# ==================== lifespan / app ====================
# lifespan: copy the old one minus restore_tasks_from_results() and the cleanup task.

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="GigaAM v3 Transcriber API",
    description="OpenAI-compatible speech-to-text API (POST /v1/audio/transcriptions).",
    version=__version__,
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_credentials=False,
                   allow_methods=["GET", "POST"], allow_headers=["Authorization", "X-API-Key", "Content-Type"])


@app.exception_handler(OpenAIError)
async def _openai_error_handler(_: Request, exc: OpenAIError):
    return JSONResponse(exc.payload(), status_code=exc.status_code)


@app.exception_handler(StarletteHTTPException)
async def _http_error_handler(_: Request, exc: StarletteHTTPException):
    err = openai_error(exc.status_code, str(exc.detail), type_=_type_for_status(exc.status_code))
    return JSONResponse(err.payload(), status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def _validation_handler(_: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    loc = [str(p) for p in first.get("loc", []) if p not in ("body", "query")]
    err = openai_error(422, first.get("msg", "Invalid request"), param=".".join(loc) or None)
    return JSONResponse(err.payload(), status_code=422)


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(_: Request, exc: RateLimitExceeded):
    err = openai_error(429, f"Rate limit exceeded: {exc.detail}", type_="rate_limit_error", code="rate_limit_exceeded")
    return JSONResponse(err.payload(), status_code=429)


@app.get("/")
async def root():
    return {
        "service": "GigaAM v3 Transcriber API",
        "version": __version__,
        "docs": "/docs",
        "openai_compatible": True,
        "endpoints": ["POST /v1/audio/transcriptions", "GET /v1/models", "GET /v1/models/{id}", "GET /health"],
    }


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "version": __version__,
        "model_loaded": model_loader is not None and model_loader.is_loaded(),
        "runtime": health_service.runtime_info(runtime_platform, machine),
        "asr": health_service.asr_health(model_loader),
    }


@app.get("/v1/models", dependencies=[Depends(verify_api_key)])
async def list_models():
    return models_payload()


@app.get("/v1/models/{model_id}", dependencies=[Depends(verify_api_key)])
async def get_model(model_id: str):
    return _model_object(resolve_model(model_id))


@app.post("/v1/audio/translations", dependencies=[Depends(verify_api_key)])
async def create_translation():
    raise openai_error(400, "GigaAM transcribes speech but does not translate it; use /v1/audio/transcriptions.",
                       code="translation_not_supported")


@app.post("/v1/audio/transcriptions", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
async def create_transcription(request: Request, file: UploadFile = File(...), model: str = Form(...)):
    raise openai_error(501, "not implemented yet", type_="server_error")  # Task 4
```

Also: the OpenAI SDK sends `OpenAI-Organization`/`OpenAI-Project` headers and `Authorization`; the CORS `allow_headers` must include `Authorization`. `RateLimitExceeded` handler replaces slowapi's `_rate_limit_exceeded_handler` so the envelope is uniform.

`tests/test_api_security.py`: delete the two `validated_task_id` tests and the `from fastapi import HTTPException` import if it becomes unused; adapt `test_verify_api_key_accept_reject` to the new signature (`api.verify_api_key(authorization=f"Bearer {valid_raw}")`, `api.verify_api_key(x_api_key=valid_raw)`, and a wrong key raising `api.OpenAIError` with `status_code == 401`). Delete `tests/test_api_integration.py` and `tests/test_api_progress.py` (`git rm`).

- [ ] **Step 4: Run — pass**

Run: `.venv/bin/python -m pytest tests/test_api_openai.py tests/test_api_security.py -v && .venv/bin/python -m ruff check api.py tests/`

- [ ] **Step 5: Commit**

```bash
git add api.py tests/test_api_openai.py tests/test_api_security.py
git rm -q tests/test_api_integration.py tests/test_api_progress.py
git commit -m "API: OpenAI-style app shell — Bearer auth, error envelope, /v1/models

The task queue, batch upload, downloads and on-disk task restore are gone:
the OpenAI contract is synchronous, so the request itself carries the result."
```

---

### Task 4: `POST /v1/audio/transcriptions` — sync formats and streaming

**Files:**
- Modify: `api.py` (`create_transcription` + helpers `_save_upload`, `_run_processor`, `_sse`)
- Test: `tests/test_api_openai.py` (second half)

**Interfaces:**
- Consumes: `transcript_formats.render/build_json/full_text/usage`, `transcription_service.normalize_asr_selection/acquire_request_model_loader/build_processor`, `normalize_diarization_backend`, `result["utterances"]` (Task 1).
- Produces: `api._save_upload(file: UploadFile) -> tuple[Path, Path]` (dir, path), `api._run_processor(...)` (blocking, run in executor), the route.

- [ ] **Step 1: Failing tests (append to `tests/test_api_openai.py`)**

```python
UTTS = [
    {"transcription": "Привет,", "boundaries": (0.0, 1.5), "speaker": "SPEAKER_01"},
    {"transcription": "как дела?", "boundaries": (1.5, 3.25), "speaker": "SPEAKER_00",
     "words": [{"text": "как", "start": 1.5, "end": 2.0}, {"text": "дела?", "start": 2.0, "end": 3.25}]},
]


class _FakeProcessor:
    calls: list = []

    def __init__(self, *_, **kw):
        self.progress_callback = kw.get("progress_callback")

    def process_file(self, filepath, output_dir, *_a, **kw):
        _FakeProcessor.calls.append({"filepath": filepath, "output_dir": output_dir, **kw})
        if self.progress_callback:
            self.progress_callback("transcription", 0.5, total_seconds=3.25, processed_seconds=1.6)
        if kw.get("_fail"):
            return {"success": False}
        return {"success": True, "media_duration": 3.25, "total_time": 0.1, "utterances": list(UTTS),
                "diarization": {"requested": bool(kw.get("enable_diarization")), "applied": bool(kw.get("enable_diarization"))}}


@pytest.fixture
def fake_processor(monkeypatch):
    _FakeProcessor.calls = []
    monkeypatch.setattr(api.transcription_service, "build_processor", lambda *a, **kw: _FakeProcessor(*a, **kw))
    return _FakeProcessor


def _post(client, data=None, **kw):
    payload = {"model": "whisper-1"}
    payload.update(data or {})
    return client.post("/v1/audio/transcriptions", headers=BEARER,
                       files={"file": ("speech.wav", b"RIFF....", "audio/wav")}, data=payload, **kw)


def test_transcription_json_default(client, fake_processor):
    r = _post(client)
    assert r.status_code == 200, r.text
    assert r.json() == {"text": "Привет, как дела?", "usage": {"type": "duration", "seconds": 4}}
    call = fake_processor.calls[0]
    assert call["output_formats"] == [] and call["enable_diarization"] is False
    assert call["filepath"].endswith("speech.wav")


def test_transcription_cleans_temp_dir(client, fake_processor):
    _post(client)
    assert not Path(fake_processor.calls[0]["output_dir"]).exists()


def test_transcription_text_srt_vtt(client, fake_processor):
    r = _post(client, {"response_format": "text"})
    assert r.headers["content-type"].startswith("text/plain") and r.text == "Привет, как дела?"
    r = _post(client, {"response_format": "srt"})
    assert r.headers["content-type"].startswith("application/x-subrip") and r.text.startswith("1\n")
    r = _post(client, {"response_format": "vtt"})
    assert r.headers["content-type"].startswith("text/vtt") and r.text.startswith("WEBVTT")


def test_transcription_verbose_with_words(client, fake_processor):
    r = _post(client, {"response_format": "verbose_json", "timestamp_granularities[]": ["segment", "word"], "language": "ru"})
    body = r.json()
    assert body["task"] == "transcribe" and body["duration"] == 3.25 and len(body["segments"]) == 2
    assert body["words"][0] == {"word": "как", "start": 1.5, "end": 2.0}
    assert "speaker" not in body["segments"][0]


def test_diarized_json_turns_diarization_on(client, fake_processor):
    r = _post(client, {"response_format": "diarized_json"})
    assert r.status_code == 200
    assert [s["speaker"] for s in r.json()["segments"]] == ["A", "B"]
    assert fake_processor.calls[0]["enable_diarization"] is True


def test_diarize_flag_adds_speakers_to_verbose(client, fake_processor):
    r = _post(client, {"response_format": "verbose_json", "diarize": "true", "num_speakers": "2"})
    assert r.json()["segments"][0]["speaker"] == "A"
    assert fake_processor.calls[0]["num_speakers"] == 2


def test_unknown_model_404(client, fake_processor):
    r = _post(client, {"model": "gpt-9"})
    assert r.status_code == 404 and _error(r)["code"] == "model_not_found" and _error(r)["param"] == "model"


def test_unsupported_extension_400(client, fake_processor):
    r = client.post("/v1/audio/transcriptions", headers=BEARER, files={"file": ("x.exe", b"MZ", "application/octet-stream")}, data={"model": "whisper-1"})
    assert r.status_code == 400 and _error(r)["code"] == "unsupported_file" and _error(r)["param"] == "file"


def test_file_too_large_413(client, fake_processor, monkeypatch):
    monkeypatch.setattr(api, "MAX_FILE_SIZE", 4)
    r = _post(client)
    assert r.status_code == 413 and _error(r)["code"] == "file_too_large"


def test_bad_response_format_400(client, fake_processor):
    r = _post(client, {"response_format": "xml"})
    assert r.status_code == 400 and _error(r)["code"] == "unsupported_response_format"


def test_stream_only_json_formats(client, fake_processor):
    r = _post(client, {"stream": "true", "response_format": "srt"})
    assert r.status_code == 400 and _error(r)["code"] == "stream_not_supported"


def test_known_speaker_names_rejected(client, fake_processor):
    r = _post(client, {"known_speaker_names[]": ["Alice"]})
    assert r.status_code == 400 and _error(r)["param"] == "known_speaker_names"


def test_sortformer_with_num_speakers_400(client, fake_processor):
    r = _post(client, {"diarize": "true", "diarization_backend": "sortformer", "num_speakers": "2"})
    assert r.status_code == 400 and _error(r)["param"] == "num_speakers"


def test_processor_failure_is_server_error_and_cleans_up(client, fake_processor, monkeypatch):
    def boom(self, *a, **kw):
        _FakeProcessor.calls.append({"output_dir": a[1]})
        raise RuntimeError("ffmpeg exploded")
    monkeypatch.setattr(_FakeProcessor, "process_file", boom)
    r = _post(client)
    assert r.status_code == 500 and _error(r)["type"] == "server_error"
    assert "ffmpeg" not in _error(r)["message"]  # no internals leaked
    assert not Path(fake_processor.calls[0]["output_dir"]).exists()


def _sse_events(text):
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def test_stream_json_events(client, fake_processor):
    r = _post(client, {"stream": "true"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = _sse_events(r.text)
    assert [e["type"] for e in events] == ["transcript.text.delta", "transcript.text.delta", "transcript.text.done"]
    assert events[0]["delta"] == "Привет, " and events[1]["delta"] == "как дела?"
    assert events[-1] == {"type": "transcript.text.done", "text": "Привет, как дела?", "usage": {"type": "duration", "seconds": 4}}
    assert ": progress" in r.text  # keep-alive comment from the progress callback


def test_stream_error_event(client, fake_processor, monkeypatch):
    monkeypatch.setattr(_FakeProcessor, "process_file", lambda self, *a, **kw: (_ for _ in ()).throw(RuntimeError("x")))
    r = _post(client, {"stream": "true"})
    events = _sse_events(r.text)
    assert events[-1]["type"] == "error" and events[-1]["error"]["type"] == "server_error"
```

Add `from pathlib import Path` to the test imports. Note the delta rule: every delta except the last carries a trailing space so concatenated deltas equal `text`.

- [ ] **Step 2: Run — fail** (501 from the stub).

- [ ] **Step 3: Implement the route in `api.py`**

Replace the stub:

```python
_GRANULARITIES = {"segment", "word"}
_STREAM_FORMATS = {"json", "verbose_json"}


def _save_upload(file: UploadFile) -> tuple[Path, Path]:
    """Кладёт загрузку в свою временную директорию под UPLOAD_DIR; лимит размера — по мере записи."""
    filename = safe_filename(file.filename)
    if not is_supported_format(filename):
        raise openai_error(400, f"Unsupported file type: '{filename}'. Supported: {', '.join(SUPPORTED_FORMATS[1])}",
                           param="file", code="unsupported_file")
    work_dir = Path(tempfile.mkdtemp(prefix="req_", dir=UPLOAD_DIR))
    target = work_dir / filename
    written = 0
    with open(target, "wb") as out:
        while chunk := file.file.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_FILE_SIZE:
                shutil.rmtree(work_dir, ignore_errors=True)
                raise openai_error(413, f"File exceeds the maximum size of {MAX_FILE_SIZE} bytes.",
                                   param="file", code="file_too_large")
            out.write(chunk)
    return work_dir, target


def _cleanup(work_dir: Path) -> None:
    shutil.rmtree(work_dir, ignore_errors=True)


def _parse_bool(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _run_processor(request_loader, file_path: Path, work_dir: Path, *, enable_diarization: bool,
                   diarization_backend: str, num_speakers: int | None, audio_preprocessing: str,
                   progress_callback) -> dict[str, Any]:
    """Синхронно: грузит модель под запрос (если нужна другая) и запускает процессор."""
    processor = transcription_service.build_processor(
        request_loader, stats_manager,
        logger=lambda msg: logger.debug(f"[api] {msg}") if logger else None,
        progress_callback=progress_callback,
    )
    result = processor.process_file(
        str(file_path), str(work_dir), 0, 1, file_path.name,
        enable_diarization=enable_diarization, num_speakers=num_speakers, output_formats=[],
        diarization_backend=diarization_backend, audio_preprocessing_mode=audio_preprocessing,
    )
    if not result.get("success"):
        raise RuntimeError("processing failed")
    return result


@app.post("/v1/audio/transcriptions", dependencies=[Depends(verify_api_key)],
          summary="Transcribe audio (OpenAI-compatible)")
@limiter.limit("10/minute")
async def create_transcription(
    request: Request,
    file: UploadFile = File(..., description="Audio or video file"),
    model: str = Form(..., description="GigaAM model id or an OpenAI alias (whisper-1, gpt-4o-transcribe)"),
    language: str | None = Form(None),
    prompt: str | None = Form(None, description="Accepted and ignored"),
    response_format: str = Form("json"),
    temperature: float | None = Form(None, description="Accepted and ignored"),
    stream: str | None = Form(None),
    timestamp_granularities: list[str] | None = Form(None, alias="timestamp_granularities[]"),
    include: list[str] | None = Form(None, alias="include[]", description="Accepted and ignored"),
    chunking_strategy: str | None = Form(None, description="Accepted and ignored (VAD chunking is always on)"),
    known_speaker_names: list[str] | None = Form(None, alias="known_speaker_names[]"),
    known_speaker_references: list[str] | None = Form(None, alias="known_speaker_references[]"),
    # --- GigaAM extensions ---
    diarize: str | None = Form(None, description="GigaAM extension: speaker diarization"),
    diarization_backend: str = Form("pyannote", description="GigaAM extension: pyannote | sortformer"),
    num_speakers: int | None = Form(None, ge=1, description="GigaAM extension"),
    asr_backend: str | None = Form(None, description="GigaAM extension: auto | pytorch | onnx | mlx"),
    onnx_provider: str | None = Form(None, description="GigaAM extension"),
    audio_preprocessing: str | None = Form(None, description="GigaAM extension: off | auto | deepfilter"),
):
    if response_format not in transcript_formats.FORMATS:
        raise openai_error(400, f"Unsupported response_format '{response_format}'. Use one of: {', '.join(transcript_formats.FORMATS)}.",
                           param="response_format", code="unsupported_response_format")
    streaming = _parse_bool(stream)
    if streaming and response_format not in _STREAM_FORMATS:
        raise openai_error(400, "stream=true is only supported with response_format=json or verbose_json.",
                           param="stream", code="stream_not_supported")
    if known_speaker_names or known_speaker_references:
        raise openai_error(400, "known_speaker_names/known_speaker_references are not supported by GigaAM.",
                           param="known_speaker_names", code="unsupported_parameter")
    granularities = set(timestamp_granularities or ["segment"])
    bad = granularities - _GRANULARITIES
    if bad:
        raise openai_error(400, f"Unknown timestamp granularity: {', '.join(sorted(bad))}.",
                           param="timestamp_granularities", code="unsupported_parameter")
    model_id = resolve_model(model)
    enable_diarization = _parse_bool(diarize) or response_format == "diarized_json"
    diarization_backend = normalize_diarization_backend(diarization_backend)
    if enable_diarization and diarization_backend == "sortformer" and num_speakers is not None:
        raise openai_error(400, "num_speakers cannot be combined with diarization_backend=sortformer.",
                           param="num_speakers", code="unsupported_parameter")
    if enable_diarization and not (HF_TOKEN and HF_TOKEN.startswith("hf_")) and diarization_backend == "pyannote":
        raise openai_error(503, "Diarization is unavailable: HF_TOKEN is not configured on the server.",
                           type_="server_error", code="diarization_unavailable")
    if model_loader is None:
        raise openai_error(503, "ASR model is not loaded.", type_="server_error", code="model_not_loaded")
    try:
        asr_selection = transcription_service.normalize_asr_selection(
            model_loader, backend=asr_backend, model=model_id, onnx_provider=onnx_provider)
    except ValueError as exc:
        raise openai_error(400, str(exc), param="asr_backend", code="unsupported_parameter")
    preprocessing = (audio_preprocessing or AUDIO_PREPROCESSING_MODE)

    work_dir, file_path = _save_upload(file)
    loop = asyncio.get_running_loop()
    progress_queue: asyncio.Queue[str | None] = asyncio.Queue()

    def progress_callback(event_or_stage, progress=None, **_):
        # Процессор шлёт ProgressEvent одним аргументом (или (stage, value) — legacy).
        # Вызывается из executor-потока — переключаемся в loop.
        stage = getattr(event_or_stage, "stage", None) or (event_or_stage if isinstance(event_or_stage, str) else "processing")
        value = getattr(event_or_stage, "file_progress", None)
        if value is None:
            value = progress
        pct = f"{int(float(value) * 100)}%" if isinstance(value, (int, float)) else "…"
        loop.call_soon_threadsafe(progress_queue.put_nowait, f": progress {stage} {pct}\n\n")

    def blocking() -> dict[str, Any]:
        request_loader, owns = model_loader, False
        if asr_selection is not None:
            request_loader, owns = transcription_service.acquire_request_model_loader(
                model_loader, asr_selection, loader_factory=ModelLoader)
        if owns and not request_loader.load_model(logger=(logger.info if logger else None)):
            raise RuntimeError("could not load the requested ASR backend")
        return _run_processor(
            request_loader, file_path, work_dir, enable_diarization=enable_diarization,
            diarization_backend=diarization_backend, num_speakers=num_speakers,
            audio_preprocessing=preprocessing, progress_callback=progress_callback)

    async def run() -> dict[str, Any]:
        async with processing_semaphore:
            return await loop.run_in_executor(None, blocking)

    def render(result):
        return transcript_formats.render(
            response_format, result.get("utterances") or [], result.get("media_duration") or 0.0,
            language=language, granularities=granularities,
            diarized=bool(result.get("diarization", {}).get("applied")) or enable_diarization,
            subtitle_options=None)

    if not streaming:
        try:
            result = await run()
        except OpenAIError:
            _cleanup(work_dir)
            raise
        except Exception as exc:
            _cleanup(work_dir)
            if logger:
                logger.error(f"[api] transcription failed: {exc}", exc_info=True)
            raise openai_error(500, "Transcription failed on the server. See the server log.",
                               type_="server_error", code="processing_failed")
        _cleanup(work_dir)
        body, media_type = render(result)
        if isinstance(body, dict):
            return JSONResponse(body)
        return Response(content=body, media_type=media_type)

    async def events():
        task = asyncio.ensure_future(run())
        getter = asyncio.ensure_future(progress_queue.get())
        try:
            while not task.done():
                done, _ = await asyncio.wait({task, getter}, timeout=5.0, return_when=asyncio.FIRST_COMPLETED)
                if getter in done:
                    yield getter.result()
                    getter = asyncio.ensure_future(progress_queue.get())
                elif not done:
                    yield ": keepalive\n\n"
            getter.cancel()
            while not progress_queue.empty():  # комментарии, пришедшие вместе с завершением
                yield progress_queue.get_nowait()
            result = task.result()
            utts = result.get("utterances") or []
            for index, utt in enumerate(utts):
                text = utt.get("transcription", "").strip()
                if not text:
                    continue
                delta = text if index == len(utts) - 1 else text + " "
                yield _sse({"type": "transcript.text.delta", "delta": delta})
            done = {"type": "transcript.text.done", "text": transcript_formats.full_text(utts),
                    "usage": transcript_formats.usage(result.get("media_duration") or 0.0)}
            if response_format == "verbose_json":
                done.update({k: v for k, v in render(result)[0].items() if k not in done})
            yield _sse(done)
        except Exception as exc:
            if logger:
                logger.error(f"[api] streamed transcription failed: {exc}", exc_info=True)
            err = openai_error(500, "Transcription failed on the server. See the server log.",
                               type_="server_error", code="processing_failed")
            yield _sse({"type": "error", "error": err.payload()["error"]})
        finally:
            _cleanup(work_dir)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                             background=BackgroundTask(_cleanup, work_dir))


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
```

`import json` at the top. The trailing-delta rule: middle deltas end with a space, the last does not, so concatenation equals `full_text`.

If FastAPI's `Form(..., alias="timestamp_granularities[]")` does not bind list fields from multipart in the installed version, read the raw form instead: `form = await request.form(); granularities = form.getlist("timestamp_granularities[]")` — and do the same for `include[]`, `known_speaker_names[]`, `known_speaker_references[]`. Keep the parameters declared for OpenAPI either way.

- [ ] **Step 4: Run — pass; full suite; ruff**

```bash
.venv/bin/python -m pytest tests/test_api_openai.py -v
.venv/bin/python -m pytest tests/ -q 2>&1 | tail -3
.venv/bin/python -m ruff check .
```

Expected: only the known pre-existing GUI failures remain. `tests/test_release_hardening.py::test_tauri_api_examples_match_authenticated_v1_contract` will fail until Task 5 — acceptable here, fixed next task.

- [ ] **Step 5: Commit**

```bash
git add api.py tests/test_api_openai.py
git commit -m "API: POST /v1/audio/transcriptions — six response formats, SSE streaming

Synchronous like OpenAI: the upload lives in a per-request temp dir that is
removed after the response (and on failure). Streaming emits SSE comments
while the processor works so proxies keep the connection, then the deltas."
```

---

### Task 5: Update consumers — Liquid, PyQt API tab, desktop prototype, release-hardening test

**Files:**
- Modify: `macos/GigaAMLiquid/Sources/GigaAMLiquid/main.swift` (`apiExample`, ~L2090 — grep `api/v1`), `macos/GigaAMLiquid/Sources/GigaAMLiquid/Localization.swift` (grep `api/v1`)
- Modify: `src/gui/support_surfaces_mixin.py:185-205`
- Modify: `desktop/ui/app.js:30-60`, `desktop/ui/index.html:155-170`
- Modify: `tests/test_release_hardening.py:15-30`

**Interfaces:** none (text only). Every example must use `POST /v1/audio/transcriptions` with `Authorization: Bearer <key>` and `model=whisper-1` (or `v3_e2e_rnnt`).

- [ ] **Step 1: Update the release-hardening test first**

Rename `test_tauri_api_examples_match_authenticated_v1_contract` → `test_api_examples_use_openai_contract` and make it scan all four consumer files:

```python
def test_api_examples_use_openai_contract():
    files = [
        "desktop/ui/app.js",
        "desktop/ui/index.html",
        "src/gui/support_surfaces_mixin.py",
        "macos/GigaAMLiquid/Sources/GigaAMLiquid/main.swift",
        "macos/GigaAMLiquid/Sources/GigaAMLiquid/Localization.swift",
    ]
    for rel in files:
        text = Path(rel).read_text(encoding="utf-8")
        assert "/api/v1/" not in text, rel
    for rel in files[:4]:
        text = Path(rel).read_text(encoding="utf-8")
        assert "/v1/audio/transcriptions" in text, rel
        assert "Authorization: Bearer" in text or "Bearer " in text, rel
```

Run: `.venv/bin/python -m pytest tests/test_release_hardening.py -v` → FAIL on `/api/v1/`.

- [ ] **Step 2: Liquid `apiExample` + Localization**

Read the current `apiExample` string in `main.swift` and the ru/en strings in `Localization.swift` that mention `/api/v1`. Replace with:

```
curl http://127.0.0.1:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $GIGAAM_API_KEY" \
  -F file=@meeting.mp3 -F model=whisper-1 -F response_format=verbose_json
```

Localization strings (ru): «REST API совместим с OpenAI Audio API: укажите base_url `http://127.0.0.1:8000/v1` в любом клиенте OpenAI.»; (en): "The REST API is OpenAI Audio API-compatible: point any OpenAI client at base_url `http://127.0.0.1:8000/v1`." Keep the existing key names; do not add new localization keys unless the current ones are insufficient.

Build check: `cd macos/GigaAMLiquid && swift build 2>&1 | tail -3` (must compile).

- [ ] **Step 3: PyQt API tab (`support_surfaces_mixin.py`)**

Replace the ru/en HTML lines describing endpoints with three lines each:

ru:
```
"<code>POST /v1/audio/transcriptions</code> — распознать файл (multipart: file, model, response_format).<br>"
"<code>GET /v1/models</code> — доступные модели.<br>"
"<code>GET /health</code> — состояние сервера.<br>"
"Совместимо с OpenAI Audio API: base_url <code>http://127.0.0.1:8000/v1</code>, заголовок <code>Authorization: Bearer &lt;ключ&gt;</code>."
```
en:
```
"<code>POST /v1/audio/transcriptions</code> — transcribe a file (multipart: file, model, response_format).<br>"
"<code>GET /v1/models</code> — available models.<br>"
"<code>GET /health</code> — server status.<br>"
"OpenAI Audio API-compatible: base_url <code>http://127.0.0.1:8000/v1</code>, header <code>Authorization: Bearer &lt;key&gt;</code>."
```

Run `.venv/bin/python -m pytest tests/ -q -k "support or i18n or gui_text" 2>&1 | tail -2`.

- [ ] **Step 4: Desktop prototype (`desktop/ui/app.js`, `index.html`)**

Replace the three snippets (python/curl/js) in `app.js` and the sample in `index.html`:

```js
const apiSnippets = {
  python: `from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="gam_...")
with open("meeting.mp3", "rb") as f:
    result = client.audio.transcriptions.create(model="whisper-1", file=f, response_format="verbose_json")
print(result.text)`,
  curl: `curl http://127.0.0.1:8000/v1/audio/transcriptions \\
  -H "Authorization: Bearer gam_..." \\
  -F file=@meeting.mp3 -F model=whisper-1 -F diarize=true -F response_format=diarized_json`,
  js: `import OpenAI from "openai";
const client = new OpenAI({ baseURL: "http://127.0.0.1:8000/v1", apiKey: "gam_..." });
const result = await client.audio.transcriptions.create({ model: "whisper-1", file: fs.createReadStream("meeting.mp3") });
console.log(result.text);`,
};
```

Keep the variable/property names the existing code reads (inspect how `app.js` renders them before renaming anything).

- [ ] **Step 5: Run the hardening test and the whole suite, commit**

```bash
.venv/bin/python -m pytest tests/test_release_hardening.py tests/ -q 2>&1 | tail -3
git add macos/GigaAMLiquid/Sources/GigaAMLiquid/main.swift macos/GigaAMLiquid/Sources/GigaAMLiquid/Localization.swift src/gui/support_surfaces_mixin.py desktop/ui/app.js desktop/ui/index.html tests/test_release_hardening.py
git commit -m "API: point every in-app example at /v1/audio/transcriptions"
```

---

### Task 6: Documentation, Postman, CHANGELOG

**Files:**
- Create: `docs/API.md`
- Delete: `docs/API_GUIDE.md`, `docs/API_QUICKSTART.md`, `docs/API_SCHEMA.md`, `docs/API_ENDPOINTS_MAP.txt`, `docs/POSTMAN_CHEATSHEET.md`, `docs/POSTMAN_COMPLETE.md`, `docs/POSTMAN_DOCUMENTATION_REPORT.md`, `docs/POSTMAN_GUIDE.md`, `docs/POSTMAN_SETUP.md`, `docs/POSTMAN_STEP_BY_STEP.md`, `docs/START_WITH_POSTMAN.md`
- Rewrite: `postman/GigaAM_API.postman_collection.json`, `postman/README.md`
- Modify: `docs/START_HERE.md` (links, ~L200 and ~L255), `README.md` (§API, ~L420-440), `README_EN.md` (§API, ~L330-340), `docs/CHANGELOG.md` (top entry)
- Test: `tests/test_docs_api.py` (create)

- [ ] **Step 1: Failing doc test**

```python
"""Docs must describe the API that ships: no references to the removed /api/v1 routes."""
from pathlib import Path

TRACKED_DOCS = ["README.md", "README_EN.md", "docs/API.md", "docs/START_HERE.md", "postman/README.md",
                "postman/GigaAM_API.postman_collection.json"]


def test_docs_do_not_mention_removed_api():
    for rel in TRACKED_DOCS:
        text = Path(rel).read_text(encoding="utf-8")
        assert "/api/v1/" not in text, rel


def test_api_reference_covers_contract():
    text = Path("docs/API.md").read_text(encoding="utf-8")
    for needle in ["/v1/audio/transcriptions", "/v1/models", "Authorization: Bearer", "verbose_json",
                   "diarized_json", "stream=true", "transcript.text.delta", "from openai import OpenAI",
                   "diarize", "invalid_api_key", "model_not_found"]:
        assert needle in text, needle


def test_removed_docs_are_gone():
    for rel in ["docs/API_GUIDE.md", "docs/API_QUICKSTART.md", "docs/POSTMAN_GUIDE.md", "docs/START_WITH_POSTMAN.md"]:
        assert not Path(rel).exists(), rel


def test_postman_collection_targets_new_routes():
    import json
    coll = json.loads(Path("postman/GigaAM_API.postman_collection.json").read_text(encoding="utf-8"))
    dump = json.dumps(coll)
    assert "/v1/audio/transcriptions" in dump and "/v1/models" in dump and "Bearer" in dump
```

Run: `.venv/bin/python -m pytest tests/test_docs_api.py -v` → FAIL.

- [ ] **Step 2: Write `docs/API.md`** (Russian prose, English code). Sections, in order:

1. `# REST API (совместим с OpenAI Audio API)` — one paragraph: what, base_url, key from `.api_keys` printed at first start, `python api.py` / `uvicorn api:app`, `/docs`.
2. `## Быстрый старт` — three tabs: curl (json), Python `openai` SDK (`OpenAI(base_url="http://127.0.0.1:8000/v1", api_key=...)`, `client.audio.transcriptions.create(model="whisper-1", file=open(...,"rb"))`), Node `openai`.
3. `## Авторизация` — Bearer, X-API-Key, `.api_keys`, 401 example.
4. `## POST /v1/audio/transcriptions` — the parameter table from the spec §3 (all rows, including "принимается, игнорируется" ones and the GigaAM extensions), then one example response per `response_format` (copy the shapes from `tests/test_transcript_formats.py`).
5. `## Стриминг (stream=true)` — SSE example with `: progress transcription 42%` comments, two deltas, done; note that deltas arrive after recognition; only `json`/`verbose_json`; error event.
6. `## GET /v1/models` и `GET /v1/models/{id}` — example body with `gigaam` extension; alias table.
7. `## Ошибки` — envelope, table of `type`/`code`/HTTP status from spec §4.
8. `## Отличия от OpenAI` — bullet list: only Russian for `v3_e2e_rnnt`; `language/prompt/temperature/include/chunking_strategy` ignored; `known_speaker_*` rejected; translations 400; `words` empty for backends without word timings; no request timeout — use `stream=true` behind proxies; rate limit 10/min.
9. `## Расширения GigaAM` — `diarize`, `diarization_backend`, `num_speakers`, `asr_backend`, `onnx_provider`, `audio_preprocessing` with `extra_body={"diarize": True}` SDK example.
10. `## Postman` — one paragraph: import `postman/GigaAM_API.postman_collection.json`, set `baseUrl` and `apiKey` variables.
11. `## Переменные окружения` — `API_HOST`, `API_PORT`, `API_WORKERS`, `MAX_FILE_SIZE`, `MAX_CONCURRENT_TASKS`, `CORS_ORIGINS`, `UPLOAD_DIR`, `API_KEYS_FILE`, `API_DEBUG` (copy meanings from the top of `api.py`).

- [ ] **Step 3: Postman collection**

Write `postman/GigaAM_API.postman_collection.json` (schema `https://schema.getpostman.com/json/collection/v2.1.0/collection.json`) with variables `baseUrl=http://127.0.0.1:8000`, `apiKey`, collection-level `auth: {"type": "bearer", "bearer": [{"key": "token", "value": "{{apiKey}}"}]}` and requests: `Health` (GET `{{baseUrl}}/health`, no auth), `Models` (GET `/v1/models`), `Model by alias` (GET `/v1/models/whisper-1`), `Transcribe json`, `Transcribe text`, `Transcribe srt`, `Transcribe verbose_json + words`, `Transcribe diarized_json`, `Transcribe stream` (json, `stream=true`), `Error: unknown model`, `Error: translations`. Each transcription request: `body.mode = "formdata"` with `file` (type `file`), `model`, and the format fields. `postman/README.md`: 15 lines — import, set variables, run.

- [ ] **Step 4: README / README_EN / START_HERE / CHANGELOG**

- `README.md` §API (find the block around `GET /api/v1/asr/options`): replace with a 12-line block: title «REST API (совместим с OpenAI)», the curl example, the SDK example, link to `docs/API.md`. Same in English for `README_EN.md`.
- `docs/START_HERE.md`: replace links to `API_GUIDE.md`/`API_QUICKSTART.md`/Postman guides with `docs/API.md`.
- `docs/CHANGELOG.md`: under the top «Unreleased» (create if missing) add:
  ```
  ### Изменено (breaking)
  - REST API заменён на совместимый с OpenAI Audio API: `POST /v1/audio/transcriptions` (json/text/srt/vtt/verbose_json/diarized_json, SSE `stream=true`), `GET /v1/models`, авторизация `Authorization: Bearer`, ошибки в формате OpenAI. Старые маршруты `/api/v1/*`, очередь задач, batch и скачивание удалены. Документация — `docs/API.md`.
  ```
- `git rm` the eleven obsolete docs. Grep the repo once more: `git grep -n "API_GUIDE\|API_QUICKSTART\|POSTMAN_\|START_WITH_POSTMAN\|api/v1" -- ':!docs/RELEASE_NOTES_*' ':!docs/CHANGELOG.md' ':!docs/superpowers'` must be empty.

- [ ] **Step 5: Run, commit**

```bash
.venv/bin/python -m pytest tests/test_docs_api.py tests/test_release_hardening.py -v
git add -A docs postman README.md README_EN.md tests/test_docs_api.py
git commit -m "Docs: single API reference for the OpenAI-compatible endpoints; drop the old guides"
```

(`docs/` is partially ignored — if `git add` skips `docs/API.md`, use `git add -f docs/API.md`.)

---

### Task 7: Live check with a real model (controller) and graph update

**Files:** none committed except `graphify-out` is ignored.

- [ ] **Step 1: Start the API from the worktree**

```bash
cd .worktrees/api-openai
GIGAAM_CONFIG_DIR=/tmp/api-check API_PORT=8765 /Users/dubr1k/Syncthing/development/GigaAMv3/.venv/bin/python api.py
```
Copy the key printed at first start (or read `.api_keys` is hashed — so use the printed one).

- [ ] **Step 2: Exercise with the SDK and curl**

```bash
KEY=gam_...
curl -s http://127.0.0.1:8765/v1/models -H "Authorization: Bearer $KEY" | head -c 300
curl -s http://127.0.0.1:8765/v1/audio/transcriptions -H "Authorization: Bearer $KEY" \
  -F file=@scratchpad/hl/speech.wav -F model=whisper-1
curl -s http://127.0.0.1:8765/v1/audio/transcriptions -H "Authorization: Bearer $KEY" \
  -F file=@scratchpad/hl/speech.wav -F model=whisper-1 -F response_format=srt
curl -sN http://127.0.0.1:8765/v1/audio/transcriptions -H "Authorization: Bearer $KEY" \
  -F file=@scratchpad/hl/speech.wav -F model=whisper-1 -F stream=true
/Users/dubr1k/Syncthing/development/GigaAMv3/.venv/bin/python - <<'EOF'
from openai import OpenAI
c = OpenAI(base_url="http://127.0.0.1:8765/v1", api_key="gam_...")
print(c.audio.transcriptions.create(model="whisper-1", file=open("scratchpad/hl/speech.wav","rb"), response_format="verbose_json").segments[0])
EOF
```
(`openai` package: install into the venv only if missing — `pip install openai`; it is not a project dependency.)

Expected: Russian text from `speech.wav` in all four; SSE shows `: progress` then deltas.

- [ ] **Step 3: `graphify update .`** in the worktree; no commit.
