"""Задачи пользователя: список, запись, журнал, результат, скачивание, удаление.

Чужая задача неотличима от несуществующей (404). Клиенту отдаётся
`registry.visible_copy` — без абсолютных путей сервера.
"""
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from src.config import OUTPUT_FORMATS
from src.utils.output_naming import find_result_file
from web import auth
from web.task_registry import ALL_TASK_STATUSES, registry

router = APIRouter()


def _user_task_or_404(task_id: str, user: str) -> dict:
    task = registry.user_task(task_id, user)
    if task is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return task


@router.get("/api/tasks")
async def list_tasks(user: str = Depends(auth.require_auth)):
    tasks = [registry.visible_copy(task) for task in registry.tasks.values() if task.get('user') == user]
    tasks.sort(key=lambda x: x.get('created_at') or '', reverse=True)
    return {"total": len(tasks), "tasks": tasks}


@router.get("/api/tasks/{task_id}")
async def get_task(task_id: str, user: str = Depends(auth.require_auth)):
    # Без result_files: там абсолютные пути сервера; файлы отдают /result и /download
    return registry.visible_copy(_user_task_or_404(task_id, user))


@router.get("/api/tasks/{task_id}/logs")
async def get_task_logs(task_id: str, user: str = Depends(auth.require_auth)):
    _user_task_or_404(task_id, user)
    return {"logs": registry.logs.get(task_id, [])}


@router.get("/api/tasks/{task_id}/result")
async def get_task_result(task_id: str, user: str = Depends(auth.require_auth)):
    task = _user_task_or_404(task_id, user)
    if task['status'] != 'completed':
        raise HTTPException(status_code=400, detail=f"Задача не завершена (статус: {task['status']})")

    result_dir = registry.result_dir(task_id)
    result_files = []
    if result_dir.exists():
        stem = Path(task['filename']).stem
        for fmt in task.get('output_formats', ['txt', 'txt_timecodes']):
            if fmt not in OUTPUT_FORMATS:
                continue  # задачи старых версий могли сохранить формат без проверки
            found = find_result_file(result_dir, stem, fmt)
            if found:
                try:
                    async with aiofiles.open(found, encoding='utf-8') as f:
                        content = await f.read()
                    result_files.append({
                        'format': fmt,
                        'name': found.name,
                        'content': content,
                    })
                except Exception:
                    pass

    return {
        'task_id': task_id,
        'filename': task['filename'],
        'result_files': result_files,
        'processing_time': task.get('processing_time'),
        'media_duration': task.get('media_duration'),
    }


@router.get("/api/tasks/{task_id}/download")
async def download_result_file(
    task_id: str,
    format: str = "txt",
    user: str = Depends(auth.require_auth),
):
    task = _user_task_or_404(task_id, user)
    if task['status'] != 'completed':
        raise HTTPException(status_code=400, detail="Задача не завершена")

    result_dir = registry.result_dir(task_id)
    if not result_dir.exists():
        raise HTTPException(status_code=404, detail="Результаты не найдены")

    stem = Path(task['filename']).stem
    found = find_result_file(result_dir, stem, format) if format in OUTPUT_FORMATS else None
    if not found:
        raise HTTPException(status_code=404, detail=f"Файл формата {format} не найден")

    return FileResponse(
        path=str(found),
        filename=found.name,
        media_type="application/octet-stream",
    )


@router.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str, user: str = Depends(auth.require_auth)):
    task = _user_task_or_404(task_id, user)
    if task['status'] == 'processing':
        raise HTTPException(status_code=400, detail="Нельзя удалить задачу в процессе обработки")

    registry.delete_data(task_id, task)

    registry.tasks.pop(task_id, None)
    registry.logs.pop(task_id, None)
    registry.persist()

    return {"ok": True, "message": "Задача удалена"}


@router.delete("/api/tasks")
async def delete_all_tasks(
    status_filter: str = "completed,failed",
    user: str = Depends(auth.require_auth),
):
    statuses = {status.strip() for status in status_filter.split(",") if status.strip()}
    if "all" in statuses:
        statuses = set(ALL_TASK_STATUSES)

    removed = 0
    for tid in list(registry.tasks.keys()):
        task = registry.tasks[tid]
        if task.get('user') != user:
            continue
        if task['status'] in statuses:
            registry.delete_data(tid, task)
            registry.tasks.pop(tid, None)
            registry.logs.pop(tid, None)
            removed += 1

    registry.persist()

    return {"ok": True, "removed": removed}
