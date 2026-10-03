"""SSE-поток прогресса задач пользователя: /api/progress."""
import asyncio
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from web import auth
from web.task_registry import registry

router = APIRouter()


class ProgressFeed:
    """Сообщения SSE одного подключения: снимок, затем только изменения.

    Первое сообщение — `{"snapshot": true, "tasks": все задачи пользователя,
    "logs": {}}`, даже если задач нет. Раньше каждое подключение начиналось с
    пустой памяти и первым сообщением отдавало ВСЕ задачи и ВЕСЬ журнал как
    новые события: клиент на каждое (пере)подключение заново писал историю в
    журнал, исторические ошибки и по запросу /api/tasks на каждую завершённую
    задачу. Журнал в снимок не входит — история не повторяется; строки,
    появившиеся после снимка, приходят как обычно.
    """

    def __init__(self, user: str):
        self.user = user
        self.started = False
        self.last: dict[str, dict] = {}
        self.log_lengths: dict[str, int] = {}

    def _current(self) -> dict[str, dict]:
        current = {}
        for tid, task in registry.tasks.items():
            if task.get('user') != self.user:
                continue
            current[tid] = {
                'status': task['status'],
                'progress': task['progress'],
                'stage_progress': task.get('stage_progress'),
                'processed_seconds': task.get('processed_seconds'),
                'total_seconds': task.get('total_seconds'),
                'progress_indeterminate': task.get('progress_indeterminate', False),
                'file_progress': int(task['progress']),
                'stage': task.get('stage', ''),
                'message': task.get('message', ''),
                'filename': task['filename'],
            }
        return current

    def next_payload(self) -> dict | None:
        current = self._current()
        if not self.started:
            self.started = True
            self.last = dict(current)
            self.log_lengths = {tid: len(registry.logs.get(tid, [])) for tid in current}
            return {'snapshot': True, 'tasks': current, 'logs': {}}

        changed = {tid: data for tid, data in current.items() if self.last.get(tid) != data}
        new_logs = {}
        for tid, logs in list(registry.logs.items()):
            task = registry.tasks.get(tid)
            if task is None or task.get('user') != self.user:
                continue
            seen = self.log_lengths.get(tid, 0)
            if len(logs) > seen:
                new_logs[tid] = logs[seen:]
                self.log_lengths[tid] = len(logs)
        self.last.update(current)
        if changed or new_logs:
            return {'tasks': changed, 'logs': new_logs}
        return None


@router.get("/api/progress")
async def progress_stream(request: Request, user: str = Depends(auth.require_auth)):
    """SSE-стрим прогресса всех задач в реальном времени."""

    async def event_generator():
        feed = ProgressFeed(user)
        while True:
            if await request.is_disconnected():
                break
            payload = feed.next_payload()
            if payload is not None:
                yield f"data: {json.dumps(payload)}\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
