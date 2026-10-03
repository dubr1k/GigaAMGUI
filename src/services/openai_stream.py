"""SSE для `stream=true` REST API (`api.py`), как у OpenAI Audio API.

Пока идёт транскрибация, клиент получает SSE-комментарии `: progress <stage>
<pct>` (и `: keepalive` раз в 5 с), затем события `transcript.text.delta` по
фразам и `transcript.text.done`; ошибка — событие `error` в конверте OpenAI.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from src.core.progress import coerce_progress
from src.services import transcript_formats
from src.services.openai_errors import openai_error, type_for_status
from src.services.transcription_api import BackendError


def queue_progress(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue, event_or_stage, progress=None) -> None:
    """SSE-комментарий о прогрессе. Процессор шлёт ProgressEvent одним аргументом
    (или (stage, value) — legacy) из executor-потока — переключаемся в loop."""
    snapshot = coerce_progress(event_or_stage, progress)
    percent = snapshot.percent()
    pct = "…" if percent is None else f"{percent}%"
    try:
        loop.call_soon_threadsafe(queue.put_nowait, f": progress {snapshot.stage or 'processing'} {pct}\n\n")
    except RuntimeError:
        pass  # loop закрыт (сервер останавливается) — прогресс уже некому отдавать


def sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def transcription_events(
    run: Callable[[], Awaitable[dict[str, Any]]],
    on_done: Callable[[asyncio.Future], None],
    progress_queue: asyncio.Queue,
    *,
    render: Callable[[dict[str, Any]], tuple[Any, str]],
    verbose: bool,
    logger,
) -> AsyncIterator[str]:
    """Тело ответа `stream=true`.

    Задача запускается здесь, при первом чтении тела; уборка (`on_done`)
    привязана к её завершению, а не к закрытию генератора: при обрыве
    соединения поток executor-а ещё пишет в рабочую директорию.
    """
    task = asyncio.ensure_future(run())
    task.add_done_callback(on_done)
    getter = asyncio.ensure_future(progress_queue.get())
    try:
        while not task.done():
            done, _ = await asyncio.wait({task, getter}, timeout=5.0, return_when=asyncio.FIRST_COMPLETED)
            if getter in done:
                yield getter.result()
                getter = asyncio.ensure_future(progress_queue.get())
            elif not done:
                yield ": keepalive\n\n"
        # Свежий getter мог забрать комментарий, пришедший вместе с завершением
        if getter.done() and not getter.cancelled():
            yield getter.result()
        while not progress_queue.empty():
            yield progress_queue.get_nowait()
        result = task.result()
        utts = result.get("utterances") or []
        # Все дельты, кроме последней, с пробелом на конце — конкатенация равна full_text
        parts = [t for t in (u.get("transcription", "").strip() for u in utts) if t]
        for index, text in enumerate(parts):
            delta = text if index == len(parts) - 1 else text + " "
            yield sse({"type": "transcript.text.delta", "delta": delta})
        done_event = {"type": "transcript.text.done", "text": transcript_formats.full_text(utts),
                      "usage": transcript_formats.usage(result.get("media_duration") or 0.0)}
        if verbose:
            done_event.update({k: v for k, v in render(result)[0].items() if k not in done_event})
        yield sse(done_event)
    except Exception as exc:
        # Ошибку самой задачи уже залогировал on_done; остальное (render) — здесь
        from_task = task.done() and not task.cancelled() and exc is task.exception()
        if logger and not from_task:
            logger.error(f"[api] streamed transcription failed: {exc}", exc_info=True)
        # Клиенту — SSE-событие без внутренностей; у BackendError причина уже клиентская
        if isinstance(exc, BackendError):
            err = openai_error(exc.status, exc.message, type_=type_for_status(exc.status),
                               param=exc.param, code=exc.code)
        else:
            err = openai_error(500, "Transcription failed on the server. See the server log.",
                               type_="server_error", code="processing_failed")
        yield sse({"type": "error", "error": err.payload()["error"]})
    finally:
        getter.cancel()  # и при обрыве соединения клиентом (GeneratorExit)
