import asyncio
import importlib

import pytest

api = importlib.import_module("api")


def test_task_status_exposes_stage_field():
    assert "stage" in api.TaskStatus.model_fields


@pytest.mark.skipif(api.FastAPI is None, reason="FastAPI недоступен")
def test_task_status_includes_progress_metadata_defaults(tmp_path, monkeypatch):
    task_id = "b" * 32
    api.tasks_storage.clear()

    api._register_task(task_id, "audio.wav", 1024)
    api.tasks_storage[task_id].update({
        "stage_progress": 0.37,
        "processed_seconds": 3.5,
        "total_seconds": 10.0,
        "progress_indeterminate": True,
    })

    assert api.tasks_storage[task_id]["progress"] == 0
    assert api.tasks_storage[task_id]["stage_progress"] == 0.37
    assert api.tasks_storage[task_id]["processed_seconds"] == 3.5
    assert api.tasks_storage[task_id]["total_seconds"] == 10.0
    assert api.tasks_storage[task_id]["progress_indeterminate"] is True


@pytest.mark.skipif(api.FastAPI is None, reason="FastAPI недоступен")
def test_process_transcription_persists_progress_metadata_from_events(monkeypatch, tmp_path):
    task_id = "c" * 32
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"content")

    updates = []

    class FakeProcessor:
        def __init__(self, model_loader, stats_manager, logger, progress_callback=None):
            self.progress_callback = progress_callback

        def process_file(
            self,
            filepath,
            output_dir,
            file_index,
            total_files,
            original_filename=None,
            estimated_conversion_ratio=0.05,
            estimated_transcription_ratio=0.95,
            enable_diarization=False,
            num_speakers=None,
            output_formats=None,
        ):
            if self.progress_callback:
                self.progress_callback({
                    "stage": "conversion",
                    "stage_progress": None,
                    "file_progress": 0.35,
                    "processed_seconds": 3.5,
                    "total_seconds": 10.0,
                })
                updates.append(dict(api.tasks_storage[task_id]))

                self.progress_callback(
                    {
                        "stage": "transcription",
                        "stage_progress": 0.6,
                        "file_progress": 0.6,
                        "processed_seconds": 6.0,
                        "total_seconds": 10.0,
                    }
                )
                updates.append(dict(api.tasks_storage[task_id]))

            return {
                "success": True,
                "media_duration": 10.0,
                "file_path": filepath,
                "file_size": 7,
                "total_time": 1.0,
                "conversion_time": 0.2,
                "transcription_time": 0.8,
                "saved_files": [],
            }

    api._register_task(task_id, audio.name, audio.stat().st_size)
    api.tasks_storage[task_id]["progress"] = 0
    api.processing_semaphore = asyncio.Semaphore(1)
    api.model_loader = object()
    api.stats_manager = object()

    class _TestLogger:
        def info(self, *args, **kwargs):
            return None

        def debug(self, *args, **kwargs):
            return None

        def error(self, *args, **kwargs):
            return None

    monkeypatch.setattr(api, "logger", _TestLogger(), raising=True)

    monkeypatch.setattr(api, "TranscriptionProcessor", FakeProcessor)
    monkeypatch.setattr(api, "output_filename", lambda stem, fmt: f"{stem}.{fmt}")

    async def _run():
        await api.process_transcription(
            task_id,
            audio,
            audio.name,
        )

    asyncio.run(_run())

    final_task = api.tasks_storage[task_id]
    assert final_task["status"] == "completed"
    assert final_task["stage_progress"] == 1.0
    assert final_task["progress_indeterminate"] is False
    assert final_task["processed_seconds"] == 10.0
    assert final_task["total_seconds"] == 10.0
    assert final_task["progress"] == 100
    assert updates[0]["progress"] == 35
    assert updates[0]["progress_indeterminate"] is True
    assert updates[1]["progress"] == 60
    assert updates[1]["stage_progress"] == 0.6
    assert updates[1]["processed_seconds"] == 6.0
