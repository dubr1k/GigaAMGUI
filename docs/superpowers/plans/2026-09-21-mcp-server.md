# MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose GigaAM (transcription, subtitles, diarization, LLM post-processing, models, status) to AI agents through an MCP server that runs over stdio locally (`gigaam mcp`) and over Streamable HTTP at `/mcp` inside `api.py` and the web panel (→ `https://gigaam-site.dubr1k.space/mcp`).

**Architecture:** A transport-agnostic `MCPServer` (`src/services/mcp_server.py`) built over a small `Backend` protocol (`src/services/mcp_backend.py`) whose single implementation reuses the exact transcription path of `POST /v1/audio/transcriptions` (`api.py` is refactored to call the same function). Auth for HTTP is a pure-ASGI Bearer/X-API-Key guard over a shared `KeyStore` (`src/services/api_keys.py`, extracted from `api.py`). LLM settings for the Python layer come from `src/services/llm_settings.py`.

**Tech Stack:** Python 3.11+, `mcp>=2,<3` (`mcp.server.mcpserver.MCPServer`, `Context.report_progress`, `streamable_http_app`), FastAPI/Starlette mounts, pytest with `mcp.shared.memory` in-memory streams.

**Spec:** `docs/superpowers/specs/2026-09-21-mcp-server-design.md`

## Global Constraints

- Branch `mcp`, worktree `/Users/dubr1k/Syncthing/development/GigaAMv3/.worktrees/mcp` (off `main` @ 42cfa1f = 2.5.0 bump). Python: `/Users/dubr1k/Syncthing/development/GigaAMv3/.venv/bin/python` (absolute; `mcp` 2.2.0 is installed there). Run from the worktree root.
- Tests: `.venv/bin/python -m pytest tests/ -q`; lint `.venv/bin/python -m ruff check src tests api.py web`. Suite is green on `main`.
- No AI attribution in commits. Commit per task; never push.
- Tool/parameter names and error codes exactly as the spec §2/§4: tools `transcribe`, `summarize`, `list_models`, `list_llm_providers`, `server_status`; resources `gigaam://models`, `gigaam://status`; prompts `meeting_notes`, `subtitles_review`; formats `text|json|verbose|diarized|srt|vtt`; error message prefix `[<code>] `.
- Tool descriptions/parameter docs are English (machine contract); Python docstrings/comments may be Russian like the codebase; `docs/MCP.md` Russian prose with English code.
- `api.py`, `web/`, `cli.py` must never import `src.gui`.
- HTTP `/mcp` needs a valid key on every request; stdio needs none. `path` sources are refused in HTTP mode unless `GIGAAM_MCP_ALLOW_PATHS=1`.
- Never write to the user's real config or `.api_keys` from tests (use `tmp_path` + `monkeypatch`).

---

### Task 1: `KeyStore` — shared API-key store

**Files:**
- Create: `src/services/api_keys.py`
- Modify: `api.py` (replace `API_KEYS_FILE`, `VALID_API_KEY_HASHES`, `_HASH_RE`, `_hash_key`, `load_api_keys`, `save_api_keys`, and the compare in `verify_api_key` with a `KeyStore`; keep the public names `load_api_keys()`, `save_api_keys()`, `_hash_key()`, `VALID_API_KEY_HASHES` as thin wrappers so `tests/test_api_security.py` keeps passing unchanged)
- Test: `tests/test_api_keys.py`

**Interfaces:**
- Produces:
  ```python
  class KeyStore:
      def __init__(self, path: Path): ...
      hashes: set[str]
      def load(self) -> "KeyStore"           # reads file; migrates plaintext lines to sha256 and rewrites; missing file → create_default()
      def save(self) -> None                 # writes hashes, chmod 0600
      def create_default(self) -> str        # generates "gam_<uuid4 hex>", stores hash, saves, returns the raw key
      def verify(self, key: str | None) -> bool   # constant-time over all hashes; False for empty/None
      def add(self, key: str) -> None
  def hash_key(key: str) -> str
  def key_from_headers(headers: Mapping[str, str]) -> str | None   # "Bearer x" from authorization, else x-api-key; None if absent
  ```

- [ ] **Step 1: Write the failing tests**

```python
"""KeyStore: hashed API keys shared by api.py, web panel and the MCP guard."""
import os
import stat

from src.services.api_keys import KeyStore, hash_key, key_from_headers


def test_hash_key_is_sha256_hex():
    h = hash_key("gam_test")
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)


def test_missing_file_creates_a_default_key(tmp_path, capsys):
    store = KeyStore(tmp_path / ".api_keys").load()
    raw = capsys.readouterr().out
    assert "gam_" in raw
    key = next(line.strip() for line in raw.splitlines() if line.strip().startswith("gam_"))
    assert store.verify(key) is True
    assert (tmp_path / ".api_keys").read_text().strip() == hash_key(key)
    assert stat.S_IMODE(os.stat(tmp_path / ".api_keys").st_mode) == 0o600


def test_plaintext_lines_are_migrated(tmp_path):
    path = tmp_path / ".api_keys"
    path.write_text("gam_plain\n" + hash_key("gam_hashed") + "\n")
    store = KeyStore(path).load()
    assert store.verify("gam_plain") and store.verify("gam_hashed")
    assert "gam_plain" not in path.read_text()


def test_verify_rejects_wrong_and_empty(tmp_path):
    store = KeyStore(tmp_path / ".api_keys")
    store.add("gam_ok")
    assert store.verify("gam_ok")
    assert not store.verify("gam_no") and not store.verify("") and not store.verify(None)


def test_key_from_headers_prefers_bearer():
    assert key_from_headers({"authorization": "Bearer abc"}) == "abc"
    assert key_from_headers({"authorization": "bearer abc"}) == "abc"
    assert key_from_headers({"authorization": "Basic zzz", "x-api-key": "k"}) == "k"
    assert key_from_headers({"x-api-key": " k "}) == "k"
    assert key_from_headers({}) is None
    assert key_from_headers({"authorization": "Bearer "}) is None
```

- [ ] **Step 2: Run — fail** (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `src/services/api_keys.py`**

```python
"""Хранилище API-ключей: в файле только SHA-256, сравнение constant-time.

Одно на api.py, веб-панель и MCP-guard; формат .api_keys прежний.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import uuid
from collections.abc import Mapping
from pathlib import Path

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def key_from_headers(headers: Mapping[str, str]) -> str | None:
    """Bearer из Authorization, иначе X-API-Key; ключи заголовков — в нижнем регистре."""
    lowered = {k.lower(): v for k, v in headers.items()}
    auth = lowered.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        return token or None
    key = (lowered.get("x-api-key") or "").strip()
    return key or None


class KeyStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.hashes: set[str] = set()

    def load(self) -> KeyStore:
        if not self.path.exists():
            self.create_default()
            return self
        migrated = False
        hashes: set[str] = set()
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            if _HASH_RE.match(line):
                hashes.add(line)
            else:
                hashes.add(hash_key(line))
                migrated = True
        self.hashes = hashes
        if migrated:
            self.save()
            print("API-ключи мигрированы в хэшированный вид (.api_keys)")
        return self

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            for key_hash in sorted(self.hashes):
                handle.write(f"{key_hash}\n")
        os.chmod(self.path, 0o600)

    def add(self, key: str) -> None:
        self.hashes.add(hash_key(key))

    def create_default(self) -> str:
        key = f"gam_{uuid.uuid4().hex}"
        self.hashes = {hash_key(key)}
        self.save()
        print(f"\n{'=' * 60}")
        print("ПЕРВЫЙ API КЛЮЧ СОЗДАН (показывается только один раз):")
        print(f"  {key}")
        print("Сохраните его в безопасном месте! В файле хранится только хэш.")
        print(f"{'=' * 60}\n")
        return key

    def verify(self, key: str | None) -> bool:
        if not key:
            return False
        candidate = hash_key(key)
        return any(hmac.compare_digest(candidate, valid) for valid in self.hashes)
```

In `api.py`: `key_store = KeyStore(API_KEYS_FILE)`; `load_api_keys()` → `key_store.load(); VALID_API_KEY_HASHES.clear(); VALID_API_KEY_HASHES.update(key_store.hashes)`; `save_api_keys()` → `key_store.hashes = set(VALID_API_KEY_HASHES); key_store.save()`; `_hash_key = hash_key`; `verify_api_key` compares via `key_from_headers({...})` + `any(hmac.compare_digest(hash_key(key), h) for h in VALID_API_KEY_HASHES)` (keep the module-level set because tests monkeypatch it). `_upload_guard_error` uses `key_from_headers` for the presence check.

- [ ] **Step 4: Run — pass**: `tests/test_api_keys.py tests/test_api_security.py tests/test_api_openai.py`; ruff.

- [ ] **Step 5: Commit** `API: extract the hashed key store into src/services/api_keys.py`

---

### Task 2: `llm_settings.resolve()` — LLM settings for the Python layer

**Files:**
- Create: `src/services/llm_settings.py`
- Test: `tests/test_llm_settings.py`

**Interfaces:**
- Produces: `def resolve(overrides: dict | None = None, *, config_dir: Path | None = None, env: Mapping[str, str] | None = None) -> dict` — the same dict shape `llm_service.run_provider` expects: `provider, api_url, api_key, model, temperature, claude_path, codex_path, opencode_path, pi_path, omp_path, other_path, <prefix>_provider, <prefix>_args, llm_allow_tools`. Also `def config_dir() -> Path` (same rules as the TUI: `GIGAAM_CONFIG_DIR` → else desktop dir from `src.utils.user_settings`).
- Precedence: `overrides` > env (`LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_URL`, `LLM_API_KEY`, `LLM_TEMPERATURE`) > `user_settings.json` (`llm_provider`, `llm_model`, `llm_api_url`, `llm_temperature`, `llm_allow_tools`, `llm_<prefix>_path`, `llm_<prefix>_provider`, `llm_<prefix>_args`) > `tui_settings.json` (same keys without the `llm_` prefix? — check `tui/src/settings.rs` field names and mirror them) > defaults (`provider="API"`, `api_url=src.config.LLM_API_URL`, `temperature=0.2`). `api_key` additionally from `.env` in `config_dir()` (`LLM_API_KEY=...` line) when env is empty. Tool paths default from `cli_tools.scan()` (`tool.path`) then binary names.

- [ ] **Step 1: Write the failing tests**

```python
"""llm_settings.resolve(): env > user_settings.json > tui_settings.json > defaults."""
import json

from src.services import llm_settings


def test_defaults_when_nothing_configured(tmp_path):
    out = llm_settings.resolve(config_dir=tmp_path, env={})
    assert out["provider"] == "API" and out["temperature"] == 0.2 and out["api_key"] == ""
    assert out["claude_path"] and out["codex_path"] and out["llm_allow_tools"] is False


def test_user_settings_beat_tui_settings(tmp_path):
    (tmp_path / "user_settings.json").write_text(json.dumps({"llm_provider": "Claude Code", "llm_model": "sonnet"}))
    (tmp_path / "tui_settings.json").write_text(json.dumps({"llm_provider": "Codex", "llm_model": "o3"}))
    out = llm_settings.resolve(config_dir=tmp_path, env={})
    assert out["provider"] == "Claude Code" and out["model"] == "sonnet"


def test_env_beats_files_and_dotenv_supplies_key(tmp_path):
    (tmp_path / "user_settings.json").write_text(json.dumps({"llm_provider": "API", "llm_model": "a"}))
    (tmp_path / ".env").write_text("LLM_API_KEY=sk-from-dotenv\n")
    out = llm_settings.resolve(config_dir=tmp_path, env={"LLM_MODEL": "b", "LLM_TEMPERATURE": "0.7"})
    assert out["model"] == "b" and out["temperature"] == 0.7 and out["api_key"] == "sk-from-dotenv"
    out = llm_settings.resolve(config_dir=tmp_path, env={"LLM_API_KEY": "sk-env"})
    assert out["api_key"] == "sk-env"


def test_overrides_win(tmp_path):
    out = llm_settings.resolve({"provider": "Other", "model": "x"}, config_dir=tmp_path, env={"LLM_PROVIDER": "API"})
    assert out["provider"] == "Other" and out["model"] == "x"
```

- [ ] **Step 2: Run — fail.**
- [ ] **Step 3: Implement** (read `tui/src/settings.rs` for the exact `tui_settings.json` field names and `tui/src/worker.rs::llm_settings_from` for the payload shape; read `src/utils/user_settings.py` for the desktop config dir).
- [ ] **Step 4: Run — pass; ruff.**
- [ ] **Step 5: Commit** `LLM: resolve provider settings for the Python layer (env, user_settings, tui_settings)`

---

### Task 3: `mcp_backend` — one transcription path for REST and MCP

**Files:**
- Create: `src/services/mcp_backend.py`
- Modify: `api.py` (`create_transcription`'s `blocking()` + `_run_processor` move into `mcp_backend.run_transcription`; `api.py` calls it)
- Test: `tests/test_mcp_backend.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass
  class TranscribeOptions:
      model: str = "v3_e2e_rnnt"            # already resolved id
      language: str | None = None
      format: str = "json"                  # text|json|verbose|diarized|srt|vtt
      word_timestamps: bool = False
      diarize: bool = False
      diarization_backend: str = "pyannote"
      num_speakers: int | None = None
      audio_preprocessing: str | None = None
      asr_backend: str | None = None
      onnx_provider: str | None = None

  class BackendError(Exception):            # code + message; MCP prefixes "[code] "
      def __init__(self, code: str, message: str, status: int = 400)

  ProgressFn = Callable[[str, float | None], None]   # (stage, fraction 0..1 or None)

  def run_transcription(file_path: Path, work_dir: Path, opts: TranscribeOptions, *,
                        model_loader, stats_manager, loader_factory, logger, progress: ProgressFn | None) -> dict
      # blocking; acquires/unloads a per-request loader when opts asks for another backend/model/provider;
      # returns processor result (with "utterances", "media_duration", "diarization")

  def render_result(result: dict, opts: TranscribeOptions) -> dict
      # {"text", "duration", "language", "usage", "segments"?, "words"?, "subtitles"?}
      # via transcript_formats: json→text only; verbose→segments(+words); diarized→segments with speaker; srt/vtt→subtitles

  class LocalBackend:
      def __init__(self, *, model_loader, stats_manager, semaphore: asyncio.Semaphore | None,
                   upload_dir: Path, media_downloader, loader_factory, logger, http_mode: bool,
                   allow_paths: bool, path_root: Path | None, max_file_size: int, max_inline_bytes: int)
      async def transcribe(self, *, url=None, path=None, audio_base64=None, filename=None,
                           opts: TranscribeOptions, progress: ProgressFn | None) -> dict
      async def summarize(self, text: str, mode: str, prompt: str | None, provider: str | None, model: str | None) -> dict
      def models(self) -> dict            # == api.models_payload() shape (move models_payload/_model_object/resolve_model/MODEL_ALIASES into mcp_backend and re-export from api.py)
      def llm_providers(self) -> dict     # {"providers": [{"name", "available", "version", "path"}], "api": {"configured": bool, "model"}}
      def status(self) -> dict            # {"version", "runtime", "asr", "busy", "limits"}
  ```
- `transcribe` source handling: exactly one of url/path/audio_base64 else `BackendError("invalid_request", ...)`; `url` → `media_downloader.download(url, work_dir, progress_callback=...)` (first file of `DownloadResult.files`); `path` → refused with `BackendError("paths_not_allowed")` when `http_mode and not allow_paths`, refused with `path_outside_root` when `path_root` set and the resolved path is outside, `file_not_found` otherwise; `audio_base64` → decoded into `work_dir/filename` (`safe_filename`, extension in `SUPPORTED_FORMATS` else `unsupported_file`; size > max_inline_bytes → `file_too_large`). Work dir `tempfile.mkdtemp(prefix="mcp_", dir=upload_dir)` removed in `finally`. Runs `run_transcription` in the default executor under the semaphore (if given). Progress: `progress("downloading", None)` before download, then the processor stages.
- `summarize`: `llm_settings.resolve({"provider": provider, "model": model} minus Nones)`; prompt from `llm_worker_service.PROMPTS` or the custom prompt (`custom` without prompt → `BackendError("prompt_required")`); runs `llm_service.run_provider(settings, text, prompt, provider=<normalized>, strict_empty_cli=True)` in the executor; provider normalization via `cli_tools.provider_by_name`/existing normalizer used by `web_app._run_llm_provider` (read it and reuse the same function).

- [ ] **Step 1: Write failing tests** (fake processor via `monkeypatch.setattr(transcription_service, "build_processor", ...)` returning `{"success": True, "media_duration": 3.25, "utterances": [...], "diarization": {...}}` like `tests/test_api_openai.py`; fake `media_downloader` writing a file; fake `run_provider`). Cover: each source kind; mutual exclusion; base64 too large; unsupported extension; http_mode path refusal + allow flag + root check; every format's `render_result` keys; progress calls include `downloading` for url and `transcription` from the fake; work dir removed on success and on processor failure; `summarize` modes incl. `custom` without prompt; per-request loader unloaded (fake factory).
- [ ] **Step 2: Run — fail.**
- [ ] **Step 3: Implement**, then refactor `api.py`: `blocking()` becomes `mcp_backend.run_transcription(file_path, work_dir, opts, model_loader=..., stats_manager=..., loader_factory=ModelLoader, logger=logger, progress=progress_callback_adapter)` where the adapter converts `(stage, fraction)` into the existing `_queue_progress` comment. `render()` keeps using `transcript_formats.render` for the REST response formats (unchanged wire contract).
- [ ] **Step 4: Run — pass**: `tests/test_mcp_backend.py tests/test_api_openai.py tests/test_transcript_formats.py`; ruff.
- [ ] **Step 5: Commit** `MCP: backend that shares the REST transcription path (sources: url, path, inline)`

---

### Task 4: `mcp_server.build_server(backend)` — tools, resources, prompts

**Files:**
- Create: `src/services/mcp_server.py`
- Test: `tests/test_mcp_server.py`

**Interfaces:**
- Produces: `def build_server(backend, *, name="GigaAM", version=src.__version__) -> MCPServer`; tools per spec §2 (`transcribe`, `summarize`, `list_models`, `list_llm_providers`, `server_status`), resources `gigaam://models`, `gigaam://status`, prompts `meeting_notes(transcript, language="ru")`, `subtitles_review(srt)`. `instructions=` text tells agents which source to use (url for remote, path for local, base64 only for short clips). Errors: catch `BackendError` → raise `ToolError(f"[{code}] {message}")` (check the 2.x exception class: `from mcp.server.mcpserver.exceptions import ToolError` or the equivalent — verify by import).
- `transcribe` tool signature (pydantic-typed, all documented):
  ```python
  async def transcribe(ctx: Context, url: str | None = None, path: str | None = None,
                       audio_base64: str | None = None, filename: str | None = None,
                       model: str = "v3_e2e_rnnt", language: str | None = None,
                       format: Literal["text","json","verbose","diarized","srt","vtt"] = "json",
                       word_timestamps: bool = False, diarize: bool = False,
                       diarization_backend: Literal["pyannote","sortformer","onnx"] = "pyannote",
                       num_speakers: int | None = None,
                       audio_preprocessing: Literal["off","auto","light","denoise"] | None = None,
                       asr_backend: str | None = None, onnx_provider: str | None = None) -> dict
  ```
  Progress: `await ctx.report_progress(fraction*100 or 0, 100, f"{stage}")` from a thread-safe bridge (backend calls the sync `ProgressFn` from the executor thread → `loop.call_soon_threadsafe(queue.put_nowait, ...)`; the tool drains the queue while awaiting the backend task — same pattern as `api.py`'s SSE loop). Model alias resolution via `mcp_backend.resolve_model` → `[model_not_found]`.

- [ ] **Step 1: Write failing tests** using the SDK in-memory transport:

```python
import anyio, pytest
from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams
from src.services.mcp_server import build_server

class FakeBackend:  # records calls, returns canned dicts; progress callback invoked with ("transcription", 0.5)
    ...

@pytest.fixture
async def session():
    server = build_server(FakeBackend())
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        async with anyio.create_task_group() as tg:
            tg.start_soon(server._mcp_server.run, server_streams[0], server_streams[1], server._mcp_server.create_initialization_options())  # adapt to the 2.x API — read mcp/server/mcpserver/*.py for the run entry point used by run_stdio_async
            async with ClientSession(*client_streams) as s:
                await s.initialize()
                yield s
            tg.cancel_scope.cancel()
```
  Tests: `list_tools` names == the five; `list_resources` URIs; `list_prompts` names; `call_tool("transcribe", {"url": ...})` returns structured content with `text`; progress notifications received (pass `progress_callback` to `call_tool`); `[invalid_request]` when two sources; `[model_not_found]` for `model="gpt-9"`; `summarize` custom without prompt → error text contains `[prompt_required]`; `read_resource("gigaam://models")` JSON; `get_prompt("meeting_notes", {...})` contains the transcript. (Use `pytest-anyio`/`anyio` backend — check what the repo already uses for async tests: `grep -rn "anyio\|pytest.mark.asyncio" tests | head`.)
- [ ] **Step 2: Run — fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run — pass; ruff.**
- [ ] **Step 5: Commit** `MCP: server with transcribe/summarize/models/status tools, resources and prompts`

---

### Task 5: Transports — stdio entry point and `/mcp` mounts with the key guard

**Files:**
- Create: `src/mcp_server.py` (`python -m src.mcp_server`), `src/services/mcp_http.py` (guard + mount helper)
- Modify: `api.py` (mount), `web/web_app.py` (KeyStore + mount), `requirements.txt`, `requirements-tui.txt` (`mcp>=2,<3`), `docker-compose.yml` (`API_KEYS_FILE=/data/.api_keys`)
- Test: `tests/test_mcp_http.py`

**Interfaces:**
- `mcp_http.build_mcp_asgi(server: MCPServer, key_store: KeyStore, *, max_body: int) -> ASGIApp` — `server.streamable_http_app(streamable_http_path="/", stateless_http=True, max_request_body_size=max_body, transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))` wrapped by `_KeyGuard` (pure ASGI, like `api._UploadGuard`): missing/invalid key → 401 JSON `{"error": {"message": "...", "type": "authentication_error", "param": null, "code": "invalid_api_key"}}`; OPTIONS passes. `mount_mcp(app: FastAPI, path: str, backend_factory: Callable[[], LocalBackend], key_store)` — mounts lazily so the backend is created after lifespan set `model_loader`; registers the session manager lifespan (`async with mcp_app_state...` — read `MCPServer.streamable_http_app` / `session_manager.run()` in the installed SDK and wire it into the host app's lifespan).
- `src/mcp_server.py`: CLI `--http --host --port --config-dir`; stdio default. Loads `ModelLoader` like `api.py` lifespan (model, stats, semaphore), builds `LocalBackend(http_mode=False, allow_paths=True)` and `server.run("stdio")`; `--http` → `uvicorn` on `mcp_http.build_mcp_asgi(..., key_store)` at `/mcp` with `http_mode=True`. **All logging to stderr** (stdout is the protocol).
- `api.py`: `mount_mcp(app, "/mcp", lambda: LocalBackend(model_loader=model_loader, stats_manager=stats_manager, semaphore=processing_semaphore, upload_dir=UPLOAD_DIR, media_downloader=MediaDownloader(), loader_factory=ModelLoader, logger=logger, http_mode=True, allow_paths=os.getenv("GIGAAM_MCP_ALLOW_PATHS")=="1", path_root=..., max_file_size=MAX_FILE_SIZE, max_inline_bytes=...), key_store)`; the `_UploadGuard` must not intercept `/mcp` (it only matches `/v1/audio/`).
- `web/web_app.py`: `key_store = KeyStore(Path(os.getenv("API_KEYS_FILE", ".api_keys")))` loaded in lifespan (prints the key on first start), same `mount_mcp` with the web app's `model_loader`/`media_downloader`/`processing_semaphore`. Web session auth is untouched.

- [ ] **Step 1: Failing tests** (`tests/test_mcp_http.py`): with `TestClient(api.app)` fixture from `tests/test_api_openai.py` (fake loader, key `VALID_KEY`): `POST /mcp` without key → 401 envelope; with Bearer → `initialize` JSON-RPC (headers `Accept: application/json, text/event-stream`, `Content-Type: application/json`) returns `serverInfo.name == "GigaAM"` (parse SSE or JSON body depending on `json_response`); `tools/list` lists `transcribe`; same for `web.web_app` with a temporary `API_KEYS_FILE` and `WEB_*` env (see how existing web tests build the app — `grep -rn "web_app" tests | head`). Also `python -m src.mcp_server --help` exits 0 (subprocess) and a stdio smoke: spawn `python -m src.mcp_server` with `GIGAAM_MCP_FAKE_MODEL=1`? — no: instead unit-test `src/mcp_server.build_stdio_backend()` with a monkeypatched `ModelLoader`.
- [ ] **Step 2: Run — fail.** — [ ] **Step 3: Implement.** — [ ] **Step 4: Run — pass; full suite; ruff.**
- [ ] **Step 5: Commit** `MCP: stdio entry point and /mcp Streamable HTTP mounts in the API and the web panel`

---

### Task 6: Launcher, skill, docs, changelog

**Files:**
- Modify: `scripts/tui/gigaam-launcher.sh` (`gigaam mcp [--http --host H --port N]` → `"$VENV/bin/python" -m src.mcp_server …`; help text), `scripts/install_tui.sh` (print the `claude mcp add gigaam -- gigaam mcp` hint after install), `skills/gigaam/SKILL.md` (MCP section: stdio + remote URL + tool list), `README.md`, `README_EN.md` (MCP section + link), `docs/START_HERE.md`, `docs/API.md` (link to MCP), `docs/CHANGELOG.md` (`[2.5.0]` → «Добавлено: MCP-сервер…»), `docs/RELEASE_NOTES_2.5.0.md` (ru + en sections), `.env.example` (`GIGAAM_MCP_ALLOW_PATHS`, `GIGAAM_MCP_PATH_ROOT`, `GIGAAM_MCP_MAX_INLINE_MB`, `API_KEYS_FILE`).
- Create: `docs/MCP.md`, `deploy/nginx-mcp-location.conf` (the `location /mcp` snippet from the spec §7)
- Test: `tests/test_docs_mcp.py` — builds the server with a stub backend, collects tool/resource/prompt names via `list_tools()`… (or from the registry attributes) and asserts each appears in `docs/MCP.md`; asserts README/README_EN/SKILL mention `gigaam mcp` and `/mcp`; asserts `docs/MCP.md` has the client snippets for Claude Code (`claude mcp add`), Codex (`~/.codex/config.toml` `[mcp_servers.gigaam]`), Cursor (`.cursor/mcp.json`) and the remote HTTP form with `Authorization`.
- `docs/MCP.md` sections: Что это; Быстрый старт локально (`gigaam mcp` + три клиента); Удалённо (`https://gigaam-site.dubr1k.space/mcp`, ключ, `claude mcp add --transport http … --header`); Инструменты (таблица параметров и пример запроса/ответа для каждого); Ресурсы и промпты; Источники аудио и лимиты (url/path/base64, `GIGAAM_MCP_*`); Прогресс и длительные вызовы (таймауты клиента, nginx); Ошибки (коды); Переменные окружения; Деплой за nginx (snippet); Безопасность (path в HTTP-режиме).
- [ ] Steps: failing doc test → write docs → pass → commit `Docs: MCP server reference, launcher command, skill and release notes`.

---

### Task 7: Live check and deployment (controller)

- [ ] stdio: `claude mcp add gigaam-dev -- /Users/dubr1k/Syncthing/development/GigaAMv3/.venv/bin/python -m src.mcp_server` (cwd = worktree) → in a scripted `claude -p` or via the in-memory client: `server_status`, `transcribe(path=scratchpad/hl/speech.wav, format="srt")`, `summarize(mode="summary")` with provider Other=/bin/cat. Remove the dev entry afterwards.
- [ ] HTTP local: start `api.py` on 8765, `curl` initialize with Bearer; `tools/call transcribe {url: <local http file server URL of speech.wav>}`.
- [ ] After merge to main and the image rebuild on `home`: add `deploy/nginx-mcp-location.conf` to `/etc/nginx/sites-available/gigaam-site.dubr1k.space`, `nginx -t`, reload; read the key from `docker logs gigaam-web`; `curl` initialize on `https://gigaam-site.dubr1k.space/mcp`; `claude mcp add --transport http gigaam https://gigaam-site.dubr1k.space/mcp --header "Authorization: Bearer …"` and call `server_status`.
