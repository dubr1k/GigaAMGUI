"""Тесты MCP-сервера (`src/services/mcp_server.py`) через in-memory транспорт SDK.

Бэкенд подменяется `FakeBackend`, который записывает вызовы и возвращает
готовые словари; никакого реального железа и моделей.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading

import pytest

pytest.importorskip("mcp")

import anyio  # noqa: E402
from mcp.client.session import ClientSession  # noqa: E402
from mcp.shared.memory import create_client_server_memory_streams  # noqa: E402

from src.services.mcp_backend import BackendError, TranscribeOptions  # noqa: E402
from src.services.mcp_server import build_server  # noqa: E402

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeBackend:
    """Записывает вызовы; прогресс шлёт из отдельного потока, как настоящий executor."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.transcribe_error: Exception | None = None
        self.summarize_error: Exception | None = None
        self.progress_events = [("downloading", None), ("transcription", 0.5), ("transcription", 1.0)]
        self.started = threading.Event()
        self.release = threading.Event()
        self.release.set()

    async def transcribe(self, *, url=None, path=None, audio_base64=None, filename=None, opts: TranscribeOptions,
                         progress):
        self.calls.append(("transcribe", {"url": url, "path": path, "audio_base64": audio_base64,
                                          "filename": filename, "opts": opts}))
        if self.transcribe_error is not None:
            raise self.transcribe_error
        if sum(x is not None for x in (url, path, audio_base64)) != 1:
            raise BackendError("invalid_request", "Exactly one of url, path or audio_base64 is required.")

        def work():
            self.started.set()
            for stage, fraction in self.progress_events:
                if progress is not None:
                    progress(stage, fraction)
            self.release.wait(5)

        await asyncio.to_thread(work)
        kind = "url" if url else "path" if path else "inline"
        return {"text": "привет мир", "duration": 1.5, "language": opts.language or "ru",
                "usage": {"type": "duration", "seconds": 2}, "source": {"kind": kind, "name": url or path or filename}}

    async def summarize(self, text, mode, prompt, provider, model):
        self.calls.append(("summarize", {"text": text, "mode": mode, "prompt": prompt, "provider": provider,
                                         "model": model}))
        if self.summarize_error is not None:
            raise self.summarize_error
        if mode == "custom" and not prompt:
            raise BackendError("prompt_required", "prompt is required when mode is 'custom'.", param="prompt")
        return {"mode": mode, "provider": provider or "API", "model": model or "gpt", "answer": f"Итог: {text[:10]}"}

    def models(self):
        self.calls.append(("models", {}))
        return {"object": "list", "data": [{"id": "v3_e2e_rnnt", "object": "model", "default": True}],
                "gigaam": {"backends": ["torch"], "onnx_providers": [], "active": None}}

    def llm_providers(self):
        self.calls.append(("llm_providers", {}))
        return {"providers": [{"name": "Claude Code", "available": True, "version": "1.0", "path": "/bin/claude"}],
                "api": {"configured": False, "model": ""}}

    def status(self):
        self.calls.append(("status", {}))
        return {"version": "2.5.0", "runtime": {"platform": "test"}, "asr": {"status": "ok"},
                "busy": {"active": 0, "max": 1},
                "limits": {"max_file_mb": 500.0, "max_inline_mb": 25.0, "max_concurrent": 1}}


@pytest.fixture
def backend():
    return FakeBackend()


@pytest.fixture
async def session(backend):
    server = build_server(backend)
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        async with anyio.create_task_group() as tg:
            lowlevel = server._lowlevel_server
            tg.start_soon(lowlevel.run, server_streams[0], server_streams[1], lowlevel.create_initialization_options())
            async with ClientSession(*client_streams) as s:
                await s.initialize()
                yield s
            tg.cancel_scope.cancel()


def _error_text(result) -> str:
    """Текст ошибки без обёртки SDK `Error executing tool <name>: ` — начинается с `[code]`."""
    assert result.is_error, result
    text = "".join(getattr(block, "text", "") for block in result.content)
    prefix, sep, rest = text.partition(": ")
    assert sep and prefix.startswith("Error executing tool "), text
    return rest


async def _settle(seen, count: int) -> None:
    """Клиент SDK вызывает progress_callback в отдельной задаче — даём ему доехать."""
    with anyio.fail_after(5):
        while len(seen) < count:
            await anyio.sleep(0.01)


# ==================== СПИСКИ ====================


async def test_list_tools_names_and_descriptions(session):
    tools = (await session.list_tools()).tools
    names = {t.name for t in tools}
    assert names == {"transcribe", "summarize", "list_models", "list_llm_providers", "server_status"}
    for tool in tools:
        assert tool.description and tool.description.strip(), tool.name
        schema = tool.input_schema
        for prop_name, prop in schema.get("properties", {}).items():
            assert prop.get("description"), f"{tool.name}.{prop_name} has no description"


async def test_transcribe_schema_enumerates_formats_and_backends(session):
    tools = {t.name: t for t in (await session.list_tools()).tools}
    props = tools["transcribe"].input_schema["properties"]
    assert "ctx" not in props
    assert set(props) >= {"url", "path", "audio_base64", "filename", "model", "language", "format", "word_timestamps",
                          "diarize", "diarization_backend", "num_speakers", "audio_preprocessing", "asr_backend",
                          "onnx_provider"}
    assert props["format"]["enum"] == ["text", "json", "verbose", "diarized", "srt", "vtt"]
    assert props["diarization_backend"]["enum"] == ["pyannote", "sortformer", "onnx"]
    assert props["format"]["default"] == "json"
    assert tools["summarize"].input_schema["properties"]["mode"]["enum"] == ["summary", "tasks", "terms", "custom"]


async def test_list_resources_and_prompts(session):
    uris = {str(r.uri) for r in (await session.list_resources()).resources}
    assert uris == {"gigaam://models", "gigaam://status"}
    prompts = {p.name for p in (await session.list_prompts()).prompts}
    assert prompts == {"meeting_notes", "subtitles_review"}


async def test_server_instructions_explain_sources():
    server = build_server(FakeBackend(), name="Test", version="9.9")
    text = server.instructions or ""
    assert "url" in text and "path" in text and "audio_base64" in text
    assert "progress" in text.lower()
    assert server.name == "Test"


# ==================== TRANSCRIBE ====================


async def test_transcribe_url_returns_structured_result_and_forwards_options(session, backend):
    result = await session.call_tool("transcribe", {
        "url": "https://example.com/a.mp3", "format": "verbose", "language": "ru", "word_timestamps": True,
        "diarize": True, "diarization_backend": "sortformer", "audio_preprocessing": "auto",
    })
    assert not result.is_error, result
    assert result.structured_content["text"] == "привет мир"
    assert result.structured_content["source"] == {"kind": "url", "name": "https://example.com/a.mp3"}
    assert result.structured_content["usage"] == {"type": "duration", "seconds": 2}
    name, call = backend.calls[0]
    assert name == "transcribe"
    assert call["url"] == "https://example.com/a.mp3" and call["path"] is None and call["audio_base64"] is None
    opts = call["opts"]
    assert isinstance(opts, TranscribeOptions)
    assert (opts.model, opts.format, opts.language, opts.word_timestamps) == ("v3_e2e_rnnt", "verbose", "ru", True)
    assert (opts.diarize, opts.diarization_backend, opts.audio_preprocessing) == (True, "sortformer", "auto")


async def test_transcribe_inline_source_passes_filename(session, backend):
    result = await session.call_tool("transcribe", {"audio_base64": "AAAA", "filename": "clip.wav", "format": "text"})
    assert not result.is_error
    assert result.structured_content["source"] == {"kind": "inline", "name": "clip.wav"}
    _, call = backend.calls[0]
    assert call["audio_base64"] == "AAAA" and call["filename"] == "clip.wav"


async def test_transcribe_resolves_model_alias_before_backend(session, backend):
    result = await session.call_tool("transcribe", {"path": "/tmp/a.wav", "model": "whisper-1"})
    assert not result.is_error
    assert backend.calls[0][1]["opts"].model == "v3_e2e_rnnt"


async def test_transcribe_unknown_model_is_model_not_found(session, backend):
    result = await session.call_tool("transcribe", {"path": "/tmp/a.wav", "model": "gpt-9"})
    assert _error_text(result).startswith("[model_not_found]")
    assert "gpt-9" in _error_text(result)
    assert backend.calls == []  # до бэкенда не дошли


async def test_transcribe_two_sources_is_invalid_request(session):
    result = await session.call_tool("transcribe", {"url": "https://x/a.mp3", "path": "/tmp/a.wav"})
    assert _error_text(result).startswith("[invalid_request]")


async def test_transcribe_no_source_is_invalid_request(session):
    result = await session.call_tool("transcribe", {})
    assert _error_text(result).startswith("[invalid_request]")


async def test_transcribe_backend_error_keeps_code_prefix(session, backend):
    backend.transcribe_error = BackendError("file_too_large", "File exceeds 500 MB.", 413)
    result = await session.call_tool("transcribe", {"url": "https://x/a.mp3"})
    assert _error_text(result) == "[file_too_large] File exceeds 500 MB."


async def test_transcribe_unexpected_exception_is_internal_error_without_details(session, backend, caplog):
    backend.transcribe_error = RuntimeError("secret /home/user/token.txt")
    with caplog.at_level(logging.ERROR):
        result = await session.call_tool("transcribe", {"url": "https://x/a.mp3"})
    text = _error_text(result)
    assert text.startswith("[internal_error]")
    assert "secret" not in text and "token.txt" not in text
    assert any("secret" in rec.getMessage() or "token.txt" in rec.getMessage() for rec in caplog.records)


async def test_transcribe_streams_progress_notifications(session):
    seen: list[tuple[float, float | None, str | None]] = []

    async def on_progress(progress, total, message):
        seen.append((progress, total, message))

    result = await session.call_tool("transcribe", {"url": "https://x/a.mp3"}, progress_callback=on_progress)
    assert not result.is_error
    await _settle(seen, 3)
    assert (50.0, 100.0, "transcription") in seen
    assert (100.0, 100.0, "transcription") in seen
    assert seen[0][2] == "downloading"
    assert seen == sorted(seen, key=lambda item: item[0])  # монотонно, None-доля не откатывает назад


async def test_progress_is_delivered_while_backend_is_still_running(session, backend):
    backend.release.clear()
    seen: list[str | None] = []
    got_progress = asyncio.Event()

    async def on_progress(progress, total, message):
        seen.append(message)
        got_progress.set()

    async def call():
        return await session.call_tool("transcribe", {"url": "https://x/a.mp3"}, progress_callback=on_progress)

    task = asyncio.ensure_future(call())
    await asyncio.wait_for(asyncio.to_thread(backend.started.wait, 5), 5)
    await asyncio.wait_for(got_progress.wait(), 5)
    assert not task.done()  # прогресс пришёл до завершения бэкенда
    backend.release.set()
    result = await asyncio.wait_for(task, 5)
    assert not result.is_error
    assert "transcription" in seen


async def test_transcribe_rejects_unknown_format_at_schema_level(session, backend):
    result = await session.call_tool("transcribe", {"url": "https://x/a.mp3", "format": "xml"})
    assert result.is_error
    assert backend.calls == []


# ==================== SUMMARIZE ====================


async def test_summarize_defaults(session, backend):
    result = await session.call_tool("summarize", {"text": "длинный транскрипт"})
    assert not result.is_error
    assert result.structured_content == {"mode": "summary", "provider": "API", "model": "gpt",
                                         "answer": "Итог: длинный тр"}
    assert backend.calls[0] == ("summarize", {"text": "длинный транскрипт", "mode": "summary", "prompt": None,
                                              "provider": None, "model": None})


async def test_summarize_custom_without_prompt(session):
    result = await session.call_tool("summarize", {"text": "abc", "mode": "custom"})
    assert _error_text(result).startswith("[prompt_required]")


async def test_summarize_forwards_overrides(session, backend):
    result = await session.call_tool("summarize", {"text": "abc", "mode": "custom", "prompt": "Переведи",
                                                   "provider": "Claude Code", "model": "opus"})
    assert not result.is_error
    assert backend.calls[0][1] == {"text": "abc", "mode": "custom", "prompt": "Переведи", "provider": "Claude Code",
                                   "model": "opus"}


async def test_summarize_backend_failure_prefix(session, backend):
    backend.summarize_error = BackendError("llm_failed", "LLM provider 'API' failed: boom", 502)
    result = await session.call_tool("summarize", {"text": "abc"})
    assert _error_text(result) == "[llm_failed] LLM provider 'API' failed: boom"


async def test_summarize_unexpected_exception(session, backend):
    backend.summarize_error = ValueError("stack details")
    result = await session.call_tool("summarize", {"text": "abc"})
    text = _error_text(result)
    assert text.startswith("[internal_error]") and "stack details" not in text


# ==================== ИНТРОСПЕКЦИЯ ====================


async def test_list_models_tool(session, backend):
    result = await session.call_tool("list_models", {})
    assert not result.is_error
    assert result.structured_content == backend.models()


async def test_list_llm_providers_tool(session, backend):
    result = await session.call_tool("list_llm_providers", {})
    assert not result.is_error
    assert result.structured_content["providers"][0]["name"] == "Claude Code"
    assert result.structured_content["api"] == {"configured": False, "model": ""}


async def test_server_status_tool(session, backend):
    result = await session.call_tool("server_status", {})
    assert not result.is_error
    assert result.structured_content["busy"] == {"active": 0, "max": 1}
    assert result.structured_content["limits"]["max_inline_mb"] == 25.0


async def test_introspection_backend_errors_are_prefixed(session, backend, monkeypatch):
    def broken():
        raise BackendError("model_not_loaded", "Model is not loaded.", 503)

    monkeypatch.setattr(backend, "status", broken)
    result = await session.call_tool("server_status", {})
    assert _error_text(result) == "[model_not_loaded] Model is not loaded."


# ==================== РЕСУРСЫ ====================


async def test_read_models_resource(session, backend):
    result = await session.read_resource("gigaam://models")
    [content] = result.contents
    assert content.mime_type == "application/json"
    assert json.loads(content.text) == backend.models()


async def test_read_status_resource(session, backend):
    result = await session.read_resource("gigaam://status")
    [content] = result.contents
    assert content.mime_type == "application/json"
    assert json.loads(content.text)["version"] == "2.5.0"


# ==================== ПРОМПТЫ ====================


async def test_meeting_notes_prompt_russian_default(session):
    result = await session.get_prompt("meeting_notes", {"transcript": "Обсудили релиз 2.5"})
    [message] = result.messages
    assert message.role == "user"
    text = message.content.text
    assert "Обсудили релиз 2.5" in text
    assert "решения" in text.lower() and "задачи" in text.lower()


async def test_meeting_notes_prompt_english(session):
    result = await session.get_prompt("meeting_notes", {"transcript": "We shipped 2.5", "language": "en"})
    text = result.messages[0].content.text
    assert "We shipped 2.5" in text
    assert "decisions" in text.lower() and "action items" in text.lower()
    assert "решения" not in text.lower()


async def test_subtitles_review_prompt(session):
    srt = "1\n00:00:00,000 --> 00:00:01,000\nПривет\n"
    result = await session.get_prompt("subtitles_review", {"srt": srt})
    text = result.messages[0].content.text
    assert srt.strip() in text
    assert "субтитр" in text.lower()
