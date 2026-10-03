"""Задачи веб-панели: записи, журналы, индекс на диске и tombstone-ы удалённых.

`registry` — единственный экземпляр WebTaskStore. Записи живут в
`registry.tasks` и переживают перезапуск через `.tasks_index.json` в
results_dir; журналы (`registry.logs`) — только в памяти. Задачу, удалённую
посреди обработки, нельзя убрать с диска сразу: executor ещё пишет в её
каталог. Её id попадает в `registry.deleted` (и `.deleted_tasks.json`), а
финальную уборку делает фоновая задача на выходе или следующий старт.

Пути берутся из `state` в момент вызова (тесты подменяют `state.results_dir`).
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Final

from src.services import task_store, transcription_service
from src.utils.atomic_json import load_json, save_json_atomic
from web.state import WebState, state

TASK_RECOVERY_MESSAGE: Final[str] = "Сервер перезапустился во время обработки задачи"
ACTIVE_TASK_STATUSES: Final[frozenset[str]] = frozenset({'pending', 'downloading', 'processing'})
ALL_TASK_STATUSES: Final[frozenset[str]] = frozenset({'pending', 'downloading', 'processing', 'completed', 'failed'})


class WebTaskStore:
    def __init__(self, web_state: WebState):
        self.state = web_state
        self.tasks: dict[str, dict] = {}
        # Очередь логов для SSE (task_id -> list of log lines)
        self.logs: dict[str, list[str]] = {}
        self.deleted: set[str] = set()

    # ---------- запись и чтение ----------

    def persist(self) -> None:
        save_json_atomic(str(self.state.tasks_index_path), self.tasks)

    def register(
        self,
        task_id: str,
        filename: str,
        file_size: int,
        user: str,
        asr_selection: transcription_service.AsrSelection | None = None,
    ) -> dict:
        asr_fields = asr_selection.as_dict() if asr_selection is not None else {}
        self.tasks[task_id] = task_store.new_task_record(
            task_id, filename, file_size, message="В очереди",
            extra={
                "stage": "",
                "output_formats": [],
                "enable_diarization": False,
                "diarization_backend": "pyannote",
                "num_speakers": None,
                "user": user,
                **asr_fields,
            },
        )
        self.logs[task_id] = []
        self.persist()
        return self.tasks[task_id]

    def log(self, task_id: str, message: str) -> None:
        """Добавляет сообщение в лог задачи."""
        if task_id in self.logs:
            self.logs[task_id].append(message)

    def user_task(self, task_id: str, user: str) -> dict | None:
        """Задача пользователя; чужая и несуществующая неразличимы (None)."""
        task = self.tasks.get(task_id)
        if task is None or task.get('user') != user:
            return None
        return task

    @staticmethod
    def visible_copy(task: dict) -> dict:
        """Запись для клиента: без result_files — там абсолютные пути сервера."""
        task_copy = dict(task)
        task_copy.pop('result_files', None)
        return task_copy

    def result_dir(self, task_id: str) -> Path:
        return self.state.results_dir / task_id

    # ---------- удаление и tombstone-ы ----------

    def persist_deleted(self) -> None:
        save_json_atomic(str(self.state.deleted_tasks_path), sorted(self.deleted))

    def restore_deleted(self) -> None:
        raw_deleted = load_json(str(self.state.deleted_tasks_path), [])
        if isinstance(raw_deleted, list):
            self.deleted.update(task_id for task_id in raw_deleted if isinstance(task_id, str))

    def remove_files(self, task_id: str, filename: str | None = None) -> None:
        upload_dir = self.state.upload_dir
        for upload_path in upload_dir.glob(f"{task_id}_*"):
            if upload_path.is_dir():
                # `{task_id}_download` — загрузка по URL, оборванная падением сервера
                shutil.rmtree(upload_path, ignore_errors=True)
            elif upload_path.is_file():
                try:
                    upload_path.unlink()
                except OSError:
                    pass

        if filename:
            exact_upload = upload_dir / f"{task_id}_{filename}"
            if exact_upload.exists():
                try:
                    exact_upload.unlink()
                except OSError:
                    pass

        result_dir = self.result_dir(task_id)
        if result_dir.exists():
            shutil.rmtree(result_dir, ignore_errors=True)

    def delete_data(self, task_id: str, task: dict) -> None:
        if task.get('status') in ACTIVE_TASK_STATUSES:
            self.deleted.add(task_id)
            self.persist_deleted()
        filename = task.get('filename')
        self.remove_files(task_id, filename if isinstance(filename, str) else None)

    def forget(self, task_id: str) -> None:
        """Убирает задачу из памяти (индекс сохраняет вызывающий)."""
        self.tasks.pop(task_id, None)
        self.logs.pop(task_id, None)

    def finalize_deleted(self, task_id: str, filename: str | None = None) -> None:
        self.remove_files(task_id, filename)
        self.deleted.discard(task_id)
        self.persist_deleted()

    def cleanup_tombstones(self) -> None:
        if not self.deleted:
            return

        index_path = str(self.state.tasks_index_path)
        raw_index = load_json(index_path, {})
        index_changed = False
        if isinstance(raw_index, dict):
            for task_id in list(self.deleted):
                if task_id in raw_index:
                    raw_index.pop(task_id, None)
                    index_changed = True
        if index_changed:
            save_json_atomic(index_path, raw_index)

        for task_id in list(self.deleted):
            self.remove_files(task_id)
            self.forget(task_id)
            self.deleted.discard(task_id)
        self.persist_deleted()

    # ---------- восстановление при старте ----------

    def _restore_completed_from_meta(self, task_dir: Path) -> dict | None:
        meta = load_json(str(task_dir / "meta.json"), None)
        if not isinstance(meta, dict):
            return None

        filename = meta.get('filename')
        user = meta.get('user')
        if not isinstance(filename, str):
            return None
        if not isinstance(user, str) or not user:
            user = self.state.username

        task_id = task_dir.name
        created_at = meta.get('created_at')
        if not isinstance(created_at, str):
            created_at = datetime.fromtimestamp(task_dir.stat().st_mtime).isoformat()
        started_at = meta.get('started_at', created_at)
        completed_at = meta.get('completed_at', started_at)
        if not isinstance(started_at, str):
            started_at = created_at
        if not isinstance(completed_at, str):
            completed_at = started_at

        task = {
            'task_id': task_id,
            'status': 'completed',
            'created_at': created_at,
            'started_at': started_at,
            'completed_at': completed_at,
            'progress': 100,
            'stage_progress': 1.0,
            'processed_seconds': meta.get('media_duration') if isinstance(meta.get('media_duration'), (int, float)) else None,
            'total_seconds': meta.get('media_duration') if isinstance(meta.get('media_duration'), (int, float)) else None,
            'progress_indeterminate': False,
            'filename': filename,
            'file_size': meta.get('file_size', 0),
            'message': 'Задача восстановлена из результатов при запуске Web GUI',
            'stage': 'Готово',
            'output_formats': meta.get('output_formats') if isinstance(meta.get('output_formats'), list) else ['txt', 'txt_timecodes'],
            'enable_diarization': meta.get('enable_diarization', False),
            'diarization_backend': meta.get('diarization_backend', 'pyannote'),
            'num_speakers': meta.get('num_speakers'),
            'subtitle_options': (
                dict(meta['subtitle_options'])
                if isinstance(meta.get('subtitle_options'), dict)
                else {
                    'sentence_split': True,
                    'max_line_count': 2,
                    'max_line_width': 64,
                }
            ),
            'asr_backend': meta.get('asr_backend'),
            'asr_model': meta.get('asr_model'),
            'onnx_provider': meta.get('onnx_provider'),
            'asr_diagnostics': meta.get('asr_diagnostics'),
            'user': user,
        }
        if isinstance(meta.get('processing_time'), (int, float)):
            task['processing_time'] = meta['processing_time']
        if isinstance(meta.get('media_duration'), (int, float)):
            task['media_duration'] = meta['media_duration']
        return task

    def restore_from_index(self) -> bool:
        raw_index = load_json(str(self.state.tasks_index_path), {})
        if not isinstance(raw_index, dict):
            return False

        restored_any = False
        changed = False
        now_iso = datetime.now().isoformat()

        for task_id, raw_task in raw_index.items():
            if not isinstance(task_id, str) or not isinstance(raw_task, dict):
                continue

            task = dict(raw_task)
            task['task_id'] = task_id

            user = task.get('user')
            if not isinstance(user, str) or not user:
                task['user'] = self.state.username
                changed = True

            if task.get('status') in ACTIVE_TASK_STATUSES:
                task['status'] = 'failed'
                task['completed_at'] = now_iso
                task['stage'] = 'Ошибка'
                task['message'] = TASK_RECOVERY_MESSAGE
                task['error'] = TASK_RECOVERY_MESSAGE
                changed = True

            task.setdefault('stage_progress', None)
            task.setdefault('processed_seconds', None)
            task.setdefault('total_seconds', None)
            task.setdefault('progress_indeterminate', False)

            self.tasks[task_id] = task
            self.logs[task_id] = []
            restored_any = True

        if changed:
            self.persist()

        return restored_any

    def restore_from_results(self) -> bool:
        restored_any = False
        results_dir = self.state.results_dir
        if not results_dir.exists():
            return False

        for task_dir in results_dir.iterdir():
            if not task_dir.is_dir():
                continue
            task = self._restore_completed_from_meta(task_dir)
            if task is None:
                continue
            task_id = task['task_id']
            if task_id in self.tasks:
                continue
            self.tasks[task_id] = task
            self.logs[task_id] = []
            restored_any = True

        if restored_any:
            self.persist()

        return restored_any

    def restore(self) -> None:
        self.restore_deleted()
        self.cleanup_tombstones()
        self.restore_from_index()
        self.restore_from_results()


registry = WebTaskStore(state)
