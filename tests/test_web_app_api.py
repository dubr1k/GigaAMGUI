"""Веб-панель (web/web_app.py): фоновые задачи и HTTP-контракт эндпоинтов.

Модель, процессор и загрузчик — подделки: ни железа, ни сети. HTTP-тесты идут
через TestClient на https://testserver, чтобы браузерная Secure-cookie сессии
отправлялась так же, как в проде за TLS.
"""
import asyncio
import importlib
import os

import pytest

pytest.importorskip("fastapi")

os.environ.setdefault("WEB_SECRET", "x" * 32)
os.environ.setdefault("WEB_USERNAME", "test-user")
os.environ.setdefault("WEB_PASSWORD", "test-password")

web_app = importlib.import_module("web.web_app")


class _FakeLoader:
    device = "cpu"
    requested_backend = "auto"
    requested_model = "v3_e2e_rnnt"
    requested_provider = "auto"

    def __init__(self, *_, **__):
        pass

    def load_model(self, logger=None):
        return True

    def is_loaded(self):
        return True

    def unload(self):
        pass

    def diagnostics(self):
        return {"requested_backend": "auto", "active_backend": "pytorch", "model": "v3_e2e_rnnt",
                "device": "cpu", "repo": "salute-developers/GigaAM", "cache_root": "/srv/secret/models"}


@pytest.fixture
def web_dirs(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    results_dir = tmp_path / "results"
    upload_dir.mkdir()
    results_dir.mkdir()
    monkeypatch.setattr(web_app, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(web_app, "RESULTS_DIR", results_dir)
    monkeypatch.setattr(web_app, "TASKS_INDEX_PATH", results_dir / ".tasks_index.json")
    monkeypatch.setattr(web_app, "DELETED_TASKS_PATH", results_dir / ".deleted_tasks.json")
    monkeypatch.setattr(web_app, "API_KEYS_FILE", tmp_path / ".api_keys")
    monkeypatch.setattr(web_app, "ModelLoader", _FakeLoader)
    monkeypatch.setattr(web_app, "HF_TOKEN", "")
    web_app.tasks_storage.clear()
    web_app.log_queues.clear()
    web_app.deleted_task_ids.clear()
    yield upload_dir, results_dir
    web_app.tasks_storage.clear()
    web_app.log_queues.clear()
    web_app.deleted_task_ids.clear()


@pytest.fixture
def fake_processor(monkeypatch):
    """Процессор-подделка: результат задаёт тест через `fake_processor.result`."""

    class _Processor:
        result: dict = {"success": True, "saved_files": [], "total_time": 0.1, "media_duration": 1.0}
        calls: list = []

        def __init__(self, *_, **__):
            pass

        def process_file(self, *args, **kwargs):
            _Processor.calls.append((args, kwargs))
            return dict(_Processor.result)

    _Processor.calls = []
    monkeypatch.setattr(web_app.transcription_service, "build_processor", lambda *a, **kw: _Processor(*a, **kw))
    return _Processor


def _run_processing(task_id: str, file_path, filename: str):
    async def scenario():
        web_app.processing_semaphore = asyncio.Semaphore(1)
        await web_app.process_transcription(task_id, file_path, filename, ["txt"], False, "pyannote", None)

    asyncio.run(scenario())


# ==================== фоновая обработка ====================


def test_failed_file_shows_processor_reason(web_dirs, fake_processor, monkeypatch):
    upload_dir, _ = web_dirs
    monkeypatch.setattr(web_app, "model_loader", _FakeLoader())
    source = upload_dir / "t1_voice.wav"
    source.write_bytes(b"RIFF")
    web_app._register_task("t1", "voice.wav", 4, "alice")
    fake_processor.result = {"success": False, "error": "boom"}

    _run_processing("t1", source, "voice.wav")

    task = web_app.tasks_storage["t1"]
    assert task["status"] == "failed"
    assert task["message"] == "boom"


def test_failed_file_without_reason_keeps_generic_message(web_dirs, fake_processor, monkeypatch):
    upload_dir, _ = web_dirs
    monkeypatch.setattr(web_app, "model_loader", _FakeLoader())
    source = upload_dir / "t2_voice.wav"
    source.write_bytes(b"RIFF")
    web_app._register_task("t2", "voice.wav", 4, "alice")
    fake_processor.result = {"success": False}

    _run_processing("t2", source, "voice.wav")

    assert web_app.tasks_storage["t2"]["message"] == "Обработка не удалась"
