"""MCP-сервер GigaAM: инструменты, ресурсы и промпты поверх `mcp_backend`.

Модуль не знает о транспорте: `build_server(backend)` возвращает `MCPServer`,
который запускается по stdio (`gigaam-mcp`) или монтируется в FastAPI (`/mcp`).
Вся логика распознавания и LLM живёт в бэкенде (`LocalBackend` или его
двойник в тестах); здесь — только контракт для агентов:

- ошибки бэкенда (`BackendError`) превращаются в `ToolError("[code] message")`,
  неожиданные исключения — в `[internal_error]` без внутренностей (они уходят
  в лог);
- синхронный `ProgressFn(stage, fraction)` бэкенда, вызываемый из executor-потока,
  переправляется в event loop через очередь и отдаётся клиенту как
  MCP progress notifications, пока инструмент ждёт результат.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal, TypeVar

from mcp.server.mcpserver import Context, MCPServer, UserMessage
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from src import __version__
from src.services import mcp_backend
from src.services.mcp_backend import BackendError, ProgressFn, TranscribeOptions

logger = logging.getLogger("gigaam.mcp")

T = TypeVar("T")

Format = Literal["text", "json", "verbose", "diarized", "srt", "vtt"]
DiarizationBackend = Literal["pyannote", "sortformer", "onnx"]
Preprocessing = Literal["off", "auto", "light", "denoise"]
SummaryMode = Literal["summary", "tasks", "terms", "custom"]

INSTRUCTIONS = """\
GigaAM is a Russian speech-to-text server (GigaAM v3 models). Use `transcribe` to turn \
audio or video into text, subtitles or speaker-labelled segments, and `summarize` to \
post-process a transcript with an LLM.

Choosing the audio source for `transcribe` (exactly one of the three):
- `url` — an http(s) link to a media file or a page yt-dlp understands. Preferred when \
the server runs remotely: the server downloads the media itself.
- `path` — a filesystem path visible to the server. Use it only with a local (stdio) \
server or when the operator enabled paths explicitly; remote servers reject it.
- `audio_base64` + `filename` — the file contents inline. Only for short clips within \
the server's inline limit (see `server_status().limits.max_inline_mb`); prefer `url` or \
`path` for anything longer.

Transcription runs at real-time-ish speed on the server's hardware and can take minutes \
for long recordings. The server sends MCP progress notifications (stage + percent) while \
the call is running, so keep the request open instead of retrying. Call `list_models` or \
`server_status` first if you need to know which model is loaded or whether the server is busy.

Errors start with a bracketed code, e.g. `[file_too_large] ...` or `[model_not_found] ...`.\
"""


# ==================== ОШИБКИ И ПРОГРЕСС ====================


def _tool_error(exc: BaseException, what: str) -> ToolError:
    """BackendError → `[code] message`; всё остальное → `[internal_error]` без деталей."""
    if isinstance(exc, BackendError):
        return ToolError(f"[{exc.code}] {exc.message}")
    logger.error(f"[mcp] {what} failed: {exc!r}", exc_info=exc)
    return ToolError(f"[internal_error] {what} failed unexpectedly; see the server log.")


async def _guarded(what: str, call: Callable[[], Awaitable[T]]) -> T:
    try:
        return await call()
    except (BackendError, Exception) as exc:  # noqa: BLE001 — контракт: наружу только ToolError
        raise _tool_error(exc, what) from exc


class _ProgressBridge:
    """Очередь стадий из executor-потока в loop + отправка `ctx.report_progress`.

    Доля `None` («стадия началась, процент неизвестен») не откатывает прогресс —
    повторяем последнее значение, чтобы уведомления оставались монотонными.
    """

    def __init__(self, ctx: Context, loop: asyncio.AbstractEventLoop):
        self.ctx = ctx
        self.loop = loop
        self.queue: asyncio.Queue[tuple[str, float | None]] = asyncio.Queue()
        self.last = 0.0

    def callback(self, stage: str, fraction: float | None) -> None:
        """Синхронный `ProgressFn` для бэкенда; вызывается из любого потока."""
        try:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, (stage, fraction))
        except RuntimeError:
            pass  # loop уже закрыт — прогресс некому отдавать

    async def report(self, stage: str, fraction: float | None) -> None:
        if isinstance(fraction, (int, float)):
            self.last = max(self.last, min(100.0, float(fraction) * 100))
        try:
            await self.ctx.report_progress(self.last, 100.0, stage)
        except Exception as exc:  # noqa: BLE001 — потеря уведомления не должна ронять транскрипцию
            logger.debug(f"[mcp] progress notification dropped: {exc!r}")

    async def run(self, work: Callable[[ProgressFn], Awaitable[T]]) -> T:
        """Ждёт `work(progress)` и параллельно сливает очередь стадий клиенту."""
        task = asyncio.ensure_future(work(self.callback))
        getter = asyncio.ensure_future(self.queue.get())
        try:
            while not task.done():
                done, _ = await asyncio.wait({task, getter}, return_when=asyncio.FIRST_COMPLETED)
                if getter in done:
                    await self.report(*getter.result())
                    getter = asyncio.ensure_future(self.queue.get())
            # Хвост: стадия, пришедшая вместе с завершением, могла попасть в свежий getter.
            # Ещё не завершённый getter отменяем ДО слива очереди — иначе он проснётся во
            # время `await report(...)`, заберёт элемент себе, и тот потеряется.
            if getter.done():
                if not getter.cancelled():
                    await self.report(*getter.result())
            else:
                getter.cancel()
            while not self.queue.empty():
                await self.report(*self.queue.get_nowait())
            return task.result()
        except asyncio.CancelledError:
            task.cancel()
            raise
        finally:
            if not getter.done():
                getter.cancel()


# ==================== СЕРВЕР ====================


def build_server(backend, *, name: str = "GigaAM", version: str | None = None) -> MCPServer:
    """Собирает MCP-сервер над `backend` (интерфейс `mcp_backend.LocalBackend`)."""
    server = MCPServer(name=name, version=version or __version__, instructions=INSTRUCTIONS)

    # ---------- tools ----------

    @server.tool(
        description=(
            "Transcribe an audio or video recording with GigaAM (Russian speech-to-text). "
            "Provide exactly one source: `url`, `path` or `audio_base64`. Returns the text plus, depending on "
            "`format`, timestamped segments/words, speaker labels or SRT/VTT subtitles. Long recordings take "
            "minutes; progress notifications report the current stage and percent."
        ),
    )
    async def transcribe(
        ctx: Context,
        url: Annotated[str | None, Field(description=(
            "http(s) link to a media file or a page supported by yt-dlp; the server downloads it. "
            "Preferred for remote servers."))] = None,
        path: Annotated[str | None, Field(description=(
            "Path to a media file on the server's filesystem. Local (stdio) servers only, unless the operator "
            "allowed paths explicitly."))] = None,
        audio_base64: Annotated[str | None, Field(description=(
            "Base64-encoded file contents for short clips (the inline size limit applies). Requires `filename`."))] = None,
        filename: Annotated[str | None, Field(description=(
            "File name with extension for `audio_base64` (e.g. `clip.wav`, `call.mp3`)."))] = None,
        model: Annotated[str, Field(description=(
            "Model id or alias (`whisper-1`, `gigaam` map to the default). See `list_models`."))] = mcp_backend.DEFAULT_MODEL,
        language: Annotated[str | None, Field(description=(
            "Language hint echoed into the result (`ru` by default); GigaAM recognises Russian."))] = None,
        format: Annotated[Format, Field(description=(
            "`text` (plain text), `json` (text + duration), `verbose` (segments, optional words), "
            "`diarized` (segments with `speaker`), `srt` / `vtt` (subtitles in `subtitles`)."))] = "json",
        word_timestamps: Annotated[bool, Field(description="Include per-word timestamps in `verbose` output.")] = False,
        diarize: Annotated[bool, Field(description=(
            "Label speakers; enabled automatically for `format=\"diarized\"`."))] = False,
        diarization_backend: Annotated[DiarizationBackend, Field(description=(
            "`pyannote` (needs an HF token on the server), `sortformer` or `onnx`."))] = "pyannote",
        num_speakers: Annotated[int | None, Field(description=(
            "Expected number of speakers (>= 1). Not supported with `sortformer`."))] = None,
        audio_preprocessing: Annotated[Preprocessing | None, Field(description=(
            "`off`, `auto`, `light` or `denoise`; defaults to the server setting."))] = None,
        asr_backend: Annotated[str | None, Field(description=(
            "ASR runtime override (e.g. `torch`, `onnx`); defaults to the server setting."))] = None,
        onnx_provider: Annotated[str | None, Field(description=(
            "ONNX execution provider when `asr_backend=\"onnx\"` (e.g. `cpu`, `cuda`)."))] = None,
    ) -> dict[str, Any]:
        """Transcribe a recording (see the parameter descriptions in the tool schema)."""
        opts = TranscribeOptions(
            model=model, language=language, format=format, word_timestamps=word_timestamps, diarize=diarize,
            diarization_backend=diarization_backend, num_speakers=num_speakers,
            audio_preprocessing=audio_preprocessing, asr_backend=asr_backend, onnx_provider=onnx_provider,
        )
        # Алиас → id модели до вызова бэкенда: [model_not_found] не зависит от реализации бэкенда
        opts.model = _resolve_model(model)
        bridge = _ProgressBridge(ctx, asyncio.get_running_loop())

        def work(progress: ProgressFn) -> Awaitable[dict[str, Any]]:
            return backend.transcribe(url=url, path=path, audio_base64=audio_base64, filename=filename,
                                      opts=opts, progress=progress)

        return await _guarded("transcribe", lambda: bridge.run(work))

    @server.tool(
        description=(
            "Post-process a transcript with the configured LLM: a summary, a task list, a glossary of terms, "
            "or a custom instruction (`mode=\"custom\"` with `prompt`). Uses the server's LLM settings unless "
            "`provider` / `model` override them; see `list_llm_providers`."
        ),
    )
    async def summarize(
        text: Annotated[str, Field(description="The transcript to process (required, non-empty).")],
        mode: Annotated[SummaryMode, Field(description=(
            "`summary` (concise summary), `tasks` (action items), `terms` (glossary), `custom` (your `prompt`)."))] = "summary",
        prompt: Annotated[str | None, Field(description="Instruction for `mode=\"custom\"`; ignored otherwise.")] = None,
        provider: Annotated[str | None, Field(description=(
            "LLM provider override: `API`, `Claude Code`, `Codex`, `OpenCode`, `Pi`, `oh-my-pi`, `Other`. "
            "Defaults to the server's LLM settings."))] = None,
        model: Annotated[str | None, Field(description="Model name override for the chosen provider.")] = None,
    ) -> dict[str, Any]:
        """Summarize a transcript with the configured LLM."""
        return await _guarded(
            "summarize", lambda: backend.summarize(text=text, mode=mode, prompt=prompt, provider=provider, model=model))

    @server.tool(description="List available ASR models with aliases, plus the backends/ONNX providers and the "
                             "currently active selection (same payload as REST `GET /v1/models`).")
    async def list_models() -> dict[str, Any]:
        """Return the ASR model registry and the active selection."""
        return await _guarded("list_models", lambda: asyncio.to_thread(backend.models))

    @server.tool(description="List LLM providers usable by `summarize`: which CLI tools are installed (version, path) "
                             "and whether an API key is configured, with the default model.")
    async def list_llm_providers() -> dict[str, Any]:
        """Return LLM provider availability."""
        return await _guarded("list_llm_providers", lambda: asyncio.to_thread(backend.llm_providers))

    @server.tool(description="Server health: version, runtime, ASR model state, active jobs vs. capacity, and the "
                             "size/concurrency limits (max file MB, max inline MB, max concurrent jobs).")
    async def server_status() -> dict[str, Any]:
        """Return version, runtime, ASR health, busy counters and limits."""
        return await _guarded("server_status", lambda: asyncio.to_thread(backend.status))

    # ---------- resources ----------

    @server.resource("gigaam://models", name="models", title="ASR models", mime_type="application/json",
                     description="JSON with the ASR model registry (same data as the `list_models` tool).")
    async def models_resource() -> str:
        return _json(await asyncio.to_thread(backend.models))

    @server.resource("gigaam://status", name="status", title="Server status", mime_type="application/json",
                     description="JSON with version, runtime, ASR health, busy counters and limits "
                                 "(same data as the `server_status` tool).")
    async def status_resource() -> str:
        return _json(await asyncio.to_thread(backend.status))

    # ---------- prompts ----------

    @server.prompt(title="Meeting notes", description="Turn a meeting transcript into structured minutes: decisions, "
                                                      "action items with owners and deadlines, open questions.")
    def meeting_notes(
        transcript: Annotated[str, Field(description="The meeting transcript (output of `transcribe`).")],
        language: Annotated[str, Field(description="Language of the notes: `ru` (default) or `en`.")] = "ru",
    ) -> list[UserMessage]:
        if (language or "ru").strip().lower().startswith("ru"):
            text = MEETING_NOTES_RU.format(transcript=transcript)
        else:
            text = MEETING_NOTES_EN.format(transcript=transcript)
        return [UserMessage(text)]

    @server.prompt(title="Subtitles review", description="Review SRT subtitles for phrase breaks and suggest fixes.")
    def subtitles_review(
        srt: Annotated[str, Field(description="Subtitles in SRT format (output of `transcribe` with `format=\"srt\"`).")],
    ) -> list[UserMessage]:
        return [UserMessage(SUBTITLES_REVIEW_RU.format(srt=srt.strip()))]

    return server


def _resolve_model(model: str | None) -> str:
    try:
        return mcp_backend.resolve_model(model)
    except BackendError as exc:
        raise _tool_error(exc, "transcribe") from exc


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ==================== ТЕКСТЫ ПРОМПТОВ ====================

MEETING_NOTES_RU = """\
Ниже — расшифровка встречи. Составь по ней протокол на русском языке в формате Markdown:

1. **Кратко о встрече** — 2–3 предложения: тема, участники (если названы), итог.
2. **Решения** — что решили; каждое решение одной строкой.
3. **Задачи** — таблица: задача, ответственный, срок. Если ответственный или срок не названы, \
пиши «не назначен» — ничего не выдумывай.
4. **Открытые вопросы** — что осталось без ответа или отложено.
5. **Следующие шаги** — что произойдёт дальше и когда.

Опирайся только на текст расшифровки; распознавание могло исказить имена и термины — \
если фрагмент неоднозначен, отметь это в скобках.

Расшифровка:

{transcript}
"""

MEETING_NOTES_EN = """\
Below is a meeting transcript. Write meeting minutes in English, in Markdown:

1. **Overview** — 2–3 sentences: topic, participants (if named), outcome.
2. **Decisions** — what was decided, one line each.
3. **Action items** — a table: task, owner, deadline. If the owner or deadline was not stated, \
write "unassigned" — do not invent anything.
4. **Open questions** — what was left unanswered or postponed.
5. **Next steps** — what happens next and when.

Rely only on the transcript; speech recognition may have distorted names and terms — \
flag ambiguous fragments in brackets.

Transcript:

{transcript}
"""

SUBTITLES_REVIEW_RU = """\
Ниже — субтитры в формате SRT, полученные автоматическим распознаванием речи. Проверь их \
и предложи правки:

- **Разрывы фраз**: найди места, где предложение разорвано между соседними блоками посреди \
смысловой единицы (после предлога, союза, внутри устойчивого выражения) или где в один блок \
попали два разных предложения. Предложи, как перераспределить текст между блоками; тайминги \
блоков сохраняй, объединяй или дели их только если без этого не обойтись.
- **Длина**: блоки длиннее ~2 строк по 40 символов или короче 1 секунды отметь отдельно.
- **Пунктуация и регистр**: расставь недостающие знаки препинания и заглавные буквы, не меняя слов.
- **Сомнительные слова**: имена, термины и числа, которые могли быть распознаны неверно, \
выпиши списком с вариантами.

Ответ дай в двух частях: (1) список найденных проблем с номерами блоков, (2) исправленный SRT \
целиком, с теми же номерами и таймкодами.

Субтитры:

{srt}
"""
