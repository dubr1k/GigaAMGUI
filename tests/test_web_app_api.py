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

from fastapi.testclient import TestClient  # noqa: E402

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


def _login(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"username": web_app.WEB_USERNAME, "password": web_app.WEB_PASSWORD})
    assert response.status_code == 200, response.text


@pytest.fixture
def anon_client(web_dirs):
    # raise_server_exceptions=False: необработанное исключение должно выглядеть как в проде — HTTP 500
    with TestClient(web_app.app, base_url="https://testserver", raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def client(anon_client):
    _login(anon_client)
    return anon_client


def _completed_task(results_dir, task_id: str, *, user: str | None = None, formats=("txt",)) -> dict:
    """Завершённая задача с файлом результата `voice.txt`."""
    task_dir = results_dir / task_id
    task_dir.mkdir()
    (task_dir / "voice.txt").write_text("привет", encoding="utf-8")
    task = {
        "task_id": task_id, "status": "completed", "created_at": "2026-01-01T00:00:00",
        "progress": 100, "filename": "voice.mp3", "file_size": 10, "message": "ok", "stage": "Готово",
        "output_formats": list(formats), "user": user or web_app.WEB_USERNAME,
        "result_files": [{"name": "voice.txt", "path": str(task_dir / "voice.txt"), "size": 12, "format": "txt"}],
    }
    web_app.tasks_storage[task_id] = task
    return task


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


# ==================== форматы вывода ====================


def test_upload_rejects_unknown_output_format_before_saving(client, web_dirs):
    upload_dir, _ = web_dirs
    response = client.post(
        "/api/upload",
        files={"files": ("voice.wav", b"RIFF", "audio/wav")},
        data={"output_formats": "txt,docx"},
    )
    assert response.status_code == 400
    assert "docx" in response.json()["detail"]
    assert list(upload_dir.iterdir()) == []
    assert web_app.tasks_storage == {}


def test_upload_rejects_batch_with_unsupported_file_before_saving_any(client, web_dirs, fake_processor):
    # Раньше первые файлы успевали сохраниться и уйти в обработку, а на k-м приходил 400
    upload_dir, _ = web_dirs
    response = client.post(
        "/api/upload",
        files=[
            ("files", ("first.wav", b"RIFF", "audio/wav")),
            ("files", ("second.mp3", b"ID3", "audio/mpeg")),
            ("files", ("notes.exe", b"MZ", "application/octet-stream")),
        ],
        data={"output_formats": "txt"},
    )
    assert response.status_code == 400
    assert "notes.exe" in response.json()["detail"]
    assert list(upload_dir.iterdir()) == []
    assert web_app.tasks_storage == {}
    assert fake_processor.calls == []


def test_upload_failure_on_a_later_file_starts_nothing(web_dirs, monkeypatch):
    # Файл k не записался (413, диск) — уже сохранённые 1..k-1 удаляются, ни одна задача не стартует
    upload_dir, _ = web_dirs
    started = []

    async def fake_save_upload(file, _request):
        if file.filename == "big.wav":
            raise web_app.HTTPException(status_code=413, detail="too large")
        path = upload_dir / f"id-{file.filename}"
        path.write_bytes(b"RIFF")
        return f"id-{file.filename}", path, file.filename, 4

    async def fake_process(*args):
        started.append(args)

    monkeypatch.setattr(web_app, "_save_upload", fake_save_upload)
    monkeypatch.setattr(web_app, "process_transcription", fake_process)
    monkeypatch.setattr(web_app, "model_loader", _FakeLoader())

    class _File:
        def __init__(self, filename):
            self.filename = filename

    async def scenario():
        with pytest.raises(web_app.HTTPException) as info:
            await web_app.upload_files(
                request=None, files=[_File("a.wav"), _File("big.wav")], output_formats="txt",
                enable_diarization=False, diarization_backend="pyannote", num_speakers="",
                asr_backend="", asr_model="", onnx_provider="", subtitle_sentence_split=True,
                subtitle_max_lines=2, subtitle_max_width=64, user="alice",
            )
        await asyncio.sleep(0)
        return info.value

    error = asyncio.run(scenario())
    assert error.status_code == 413
    assert list(upload_dir.iterdir()) == []
    assert web_app.tasks_storage == {}
    assert started == []


def test_download_url_rejects_unknown_output_format(client):
    response = client.post("/api/download-url", data={"url": "https://example.com/v", "output_formats": "pdf"})
    assert response.status_code == 400
    assert web_app.tasks_storage == {}


def test_unknown_format_download_is_404_not_500(client, web_dirs):
    _, results_dir = web_dirs
    _completed_task(results_dir, "done1")
    response = client.get("/api/tasks/done1/download", params={"format": "bogus"})
    assert response.status_code == 404
    assert client.get("/api/tasks/done1/download", params={"format": "txt"}).status_code == 200


def test_result_skips_unknown_formats_persisted_by_older_versions(client, web_dirs):
    # До проверки форматов в индекс могла попасть задача с мусорным форматом — она не должна отдавать 500 навсегда
    _, results_dir = web_dirs
    _completed_task(results_dir, "done2", formats=("txt", "bogus"))
    response = client.get("/api/tasks/done2/result")
    assert response.status_code == 200
    assert [item["format"] for item in response.json()["result_files"]] == ["txt"]


# ==================== SSE прогресса ====================


def test_progress_feed_starts_with_snapshot_without_history(web_dirs):
    web_app.tasks_storage["mine"] = {"task_id": "mine", "status": "completed", "progress": 100,
                                     "filename": "a.wav", "message": "ok", "user": "alice"}
    web_app.tasks_storage["theirs"] = {"task_id": "theirs", "status": "processing", "progress": 10,
                                       "filename": "b.wav", "message": "", "user": "bob"}
    web_app.log_queues["mine"] = ["старая строка 1", "старая строка 2"]
    web_app.log_queues["theirs"] = ["чужая"]
    feed = web_app.ProgressFeed("alice")

    first = feed.next_payload()
    # Снимок: состояние своих задач, без журнала — клиент не повторяет историю на каждом подключении
    assert first["snapshot"] is True
    assert set(first["tasks"]) == {"mine"}
    assert first["logs"] == {}

    assert feed.next_payload() is None  # ничего не изменилось

    web_app.log_queues["mine"].append("новая строка")
    web_app.tasks_storage["mine"]["message"] = "перезапуск"
    delta = feed.next_payload()
    assert "snapshot" not in delta
    assert set(delta["tasks"]) == {"mine"}
    assert delta["logs"] == {"mine": ["новая строка"]}


def test_progress_feed_sends_snapshot_even_without_tasks(web_dirs):
    # Без задач первое сообщение всё равно уходит: иначе клиент счёл бы снимком первое настоящее событие
    feed = web_app.ProgressFeed("alice")
    assert feed.next_payload() == {"snapshot": True, "tasks": {}, "logs": {}}
    web_app._register_task("fresh", "c.wav", 1, "alice")
    web_app._task_log("fresh", "первая строка")
    delta = feed.next_payload()
    assert set(delta["tasks"]) == {"fresh"}
    assert delta["logs"] == {"fresh": ["первая строка"]}


# ==================== видимость задач ====================


def test_single_task_does_not_expose_server_paths(client, web_dirs):
    _, results_dir = web_dirs
    _completed_task(results_dir, "done3")
    response = client.get("/api/tasks/done3")
    assert response.status_code == 200
    assert response.json()["task_id"] == "done3"
    assert str(results_dir) not in response.text
    # тот же вид, что у списка задач
    listed = client.get("/api/tasks").json()["tasks"]
    assert listed == [response.json()]


def test_single_task_of_another_user_is_404(client, web_dirs):
    _, results_dir = web_dirs
    _completed_task(results_dir, "bob1", user="bob")
    assert client.get("/api/tasks/bob1").status_code == 404
