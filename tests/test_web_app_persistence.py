import asyncio
import importlib
import os

import pytest
from fastapi import HTTPException

try:
    from fastapi.testclient import TestClient
    _HAS_CLIENT = True
except Exception:  # pragma: no cover
    _HAS_CLIENT = False

os.environ.setdefault("WEB_SECRET", "x" * 32)
os.environ.setdefault("WEB_USERNAME", "test-user")
os.environ.setdefault("WEB_PASSWORD", "test-password")

web_app = importlib.import_module("web.web_app")


@pytest.fixture
def web_state(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    results_dir = tmp_path / "results"
    upload_dir.mkdir()
    results_dir.mkdir()

    monkeypatch.setattr(web_app, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(web_app, "RESULTS_DIR", results_dir)
    monkeypatch.setattr(web_app, "TASKS_INDEX_PATH", results_dir / ".tasks_index.json")
    monkeypatch.setattr(web_app, "DELETED_TASKS_PATH", results_dir / ".deleted_tasks.json")
    web_app.tasks_storage.clear()
    web_app.log_queues.clear()
    web_app.deleted_task_ids.clear()

    yield upload_dir, results_dir

    web_app.tasks_storage.clear()
    web_app.log_queues.clear()
    web_app.deleted_task_ids.clear()


def _task(task_id: str, user: str, status: str = "completed") -> dict:
    return {
        "task_id": task_id,
        "status": status,
        "created_at": "2026-01-01T00:00:00",
        "started_at": None,
        "completed_at": None,
        "progress": 100 if status == "completed" else 5,
        "filename": f"{task_id}.mp3",
        "file_size": 10,
        "message": "ok",
        "stage": "Готово" if status == "completed" else "Подготовка...",
        "output_formats": ["txt"],
        "enable_diarization": False,
        "diarization_backend": "pyannote",
        "num_speakers": None,
        "user": user,
    }


def test_register_task_persists_authenticated_user(web_state):
    # Given: пустое серверное хранилище Web GUI.
    # When: задача регистрируется для конкретного пользователя.
    web_app._register_task("task-alice", "voice.mp3", 128, "alice")

    # Then: JSON-индекс содержит владельца задачи и переживёт закрытие вкладки.
    index = web_app.load_json(str(web_app.TASKS_INDEX_PATH), {})
    assert index["task-alice"]["user"] == "alice"
    assert index["task-alice"]["filename"] == "voice.mp3"
    assert index["task-alice"]["diarization_backend"] == "pyannote"


def test_web_frontend_posts_selected_diarization_backend():
    html = (web_app.STATIC_DIR / "index.html").read_text(encoding="utf-8")
    javascript = (web_app.STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'option value="sortformer"' in html
    assert "formData.append('diarization_backend', diarBackend)" in javascript


def test_restore_marks_active_task_failed_and_preserves_user(web_state):
    # Given: сервер был остановлен, пока пользовательская задача была в обработке.
    web_app.save_json_atomic(str(web_app.TASKS_INDEX_PATH), {"task-alice": _task("task-alice", "alice", "processing")})

    # When: Web GUI восстанавливает индекс задач при старте.
    restored = web_app._restore_tasks_from_index()

    # Then: задача остаётся привязанной к пользователю и становится завершённой ошибкой рестарта.
    assert restored is True
    assert web_app.tasks_storage["task-alice"]["user"] == "alice"
    assert web_app.tasks_storage["task-alice"]["status"] == "failed"
    assert web_app.tasks_storage["task-alice"]["message"] == web_app.TASK_RECOVERY_MESSAGE


def test_legacy_meta_restore_uses_configured_single_user(web_state):
    _, results_dir = web_state
    task_dir = results_dir / "legacy-task"
    task_dir.mkdir()
    web_app.save_json_atomic(str(task_dir / "meta.json"), {
        "task_id": "legacy-task",
        "filename": "legacy.mp3",
        "created_at": "2026-01-01T00:00:00",
        "completed_at": "2026-01-01T00:01:00",
        "output_formats": ["txt"],
    })

    # When: старые результаты без поля user восстанавливаются из meta.json.
    restored = web_app._restore_tasks_from_results()

    # Then: в однопользовательском Web GUI история остаётся видимой текущему пользователю.
    assert restored is True
    assert web_app.tasks_storage["legacy-task"]["user"] == web_app.WEB_USERNAME
    assert web_app.tasks_storage["legacy-task"]["status"] == "completed"


def test_restore_merges_index_and_result_metadata(web_state):
    _, results_dir = web_state
    web_app.save_json_atomic(str(web_app.TASKS_INDEX_PATH), {"indexed-task": _task("indexed-task", "alice")})

    task_dir = results_dir / "meta-only-task"
    task_dir.mkdir()
    web_app.save_json_atomic(str(task_dir / "meta.json"), {
        "task_id": "meta-only-task",
        "filename": "meta-only.mp3",
        "created_at": "2026-01-01T00:00:00",
        "completed_at": "2026-01-01T00:01:00",
        "output_formats": ["txt"],
        "user": "alice",
    })

    # When: startup restore sees both an index and a result directory missing from that index.
    web_app._restore_persisted_tasks()

    # Then: both task sources are visible instead of choosing only one source.
    assert set(web_app.tasks_storage) == {"indexed-task", "meta-only-task"}
    assert web_app.tasks_storage["meta-only-task"]["user"] == "alice"


def test_user_cannot_access_other_users_task(web_state):
    # Given: задача принадлежит другому пользователю.
    web_app.tasks_storage["task-bob"] = _task("task-bob", "bob")

    # When / Then: прямой доступ текущего пользователя маскируется под 404.
    with pytest.raises(HTTPException) as exc:
        web_app._user_task_or_404("task-bob", "alice")
    assert exc.value.status_code == 404


def test_delete_all_removes_only_current_user_data(web_state):
    upload_dir, results_dir = web_state
    web_app.tasks_storage["task-alice"] = _task("task-alice", "alice")
    web_app.tasks_storage["task-bob"] = _task("task-bob", "bob")
    web_app.log_queues["task-alice"] = ["alice log"]
    web_app.log_queues["task-bob"] = ["bob log"]

    (upload_dir / "task-alice_task-alice.mp3").write_bytes(b"alice")
    (upload_dir / "task-bob_task-bob.mp3").write_bytes(b"bob")
    (results_dir / "task-alice").mkdir()
    (results_dir / "task-bob").mkdir()

    # When: пользователь очищает все свои данные.
    response = asyncio.run(web_app.delete_all_tasks(status_filter="all", user="alice"))

    # Then: удалены только его задачи, файлы и логи; чужая история сохранена.
    assert response == {"ok": True, "removed": 1}
    assert "task-alice" not in web_app.tasks_storage
    assert "task-bob" in web_app.tasks_storage
    assert not (upload_dir / "task-alice_task-alice.mp3").exists()
    assert (upload_dir / "task-bob_task-bob.mp3").exists()
    assert not (results_dir / "task-alice").exists()
    assert (results_dir / "task-bob").exists()
    assert "task-alice" not in web_app.log_queues
    assert web_app.load_json(str(web_app.TASKS_INDEX_PATH), {}) == {"task-bob": web_app.tasks_storage["task-bob"]}


def test_active_delete_all_leaves_persistent_tombstone_until_worker_cleanup(web_state):
    upload_dir, results_dir = web_state
    web_app.tasks_storage["task-alice"] = _task("task-alice", "alice", "processing")
    (upload_dir / "task-alice_task-alice.mp3").write_bytes(b"alice")
    (results_dir / "task-alice").mkdir()

    # When: пользователь удаляет все данные, пока задача ещё может выполняться в executor.
    response = asyncio.run(web_app.delete_all_tasks(status_filter="all", user="alice"))

    # Then: задача удалена из видимой истории, а tombstone сохранён до выхода фонового worker.
    assert response == {"ok": True, "removed": 1}
    assert "task-alice" not in web_app.tasks_storage
    assert web_app.load_json(str(web_app.DELETED_TASKS_PATH), []) == ["task-alice"]
    assert not (upload_dir / "task-alice_task-alice.mp3").exists()
    assert not (results_dir / "task-alice").exists()

    # When: фоновый worker завершает обработку и делает финальную уборку.
    web_app._finalize_deleted_task("task-alice", "task-alice.mp3")

    # Then: tombstone очищен, поэтому список удалённых задач не растёт бесконечно.
    assert web_app.load_json(str(web_app.DELETED_TASKS_PATH), []) == []


def test_startup_tombstone_cleanup_prevents_deleted_task_restore(web_state):
    upload_dir, results_dir = web_state
    web_app.save_json_atomic(str(web_app.TASKS_INDEX_PATH), {"task-alice": _task("task-alice", "alice", "processing")})
    web_app.save_json_atomic(str(web_app.DELETED_TASKS_PATH), ["task-alice"])
    (upload_dir / "task-alice_task-alice.mp3").write_bytes(b"alice")
    (results_dir / "task-alice").mkdir()

    # When: сервер стартует после падения между delete-all и выходом worker.
    web_app._restore_persisted_tasks()

    # Then: tombstone удаляет файлы и не даёт задаче вернуться из старого индекса.
    assert "task-alice" not in web_app.tasks_storage
    assert web_app.load_json(str(web_app.TASKS_INDEX_PATH), {}) == {}
    assert web_app.load_json(str(web_app.DELETED_TASKS_PATH), []) == []
    assert not (upload_dir / "task-alice_task-alice.mp3").exists()
    assert not (results_dir / "task-alice").exists()


@pytest.mark.skipif(not _HAS_CLIENT, reason="нужен fastapi TestClient")
def test_health_includes_asr_and_runtime(web_state, monkeypatch):
    class _FakeModelLoader:
        device = "cpu"

        def load_model(self, logger=None):
            return True

        def is_loaded(self):
            return True

        def diagnostics(self):
            return {
                "requested_backend": "pytorch",
                "active_backend": "pytorch",
                "fallback_reason": None,
                "model": "e2e_rnnt",
                "device": "cpu",
                "repo": None,
                "cache_root": None,
                "loader_loaded": True,
                "error": None,
            }

    monkeypatch.setattr(web_app, "ModelLoader", _FakeModelLoader)
    monkeypatch.setattr(web_app, "HF_TOKEN", "")

    with TestClient(web_app.app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        payload = response.json()
        assert payload["asr"]["requested_backend"] == "pytorch"
        assert payload["asr"]["active_backend"] == "pytorch"
        assert payload["runtime"]["platform"]
        assert payload["runtime"]["machine"]
