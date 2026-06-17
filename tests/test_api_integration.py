"""HTTP-уровневые тесты исправлений безопасности API (Phase 0.1, 0.3, 0.4).

Модель подменяется заглушкой, чтобы lifespan не грузил реальную GigaAM.
"""

import importlib

import pytest

api = importlib.import_module("api")

try:
    from fastapi.testclient import TestClient
    _HAS_CLIENT = True
except Exception:  # pragma: no cover
    _HAS_CLIENT = False

pytestmark = pytest.mark.skipif(not _HAS_CLIENT, reason="нужен fastapi TestClient")

VALID_KEY = "gam_integration_test"


class _FakeModelLoader:
    def load_model(self, logger=None):
        return True

    def is_loaded(self):
        return True


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api, "ModelLoader", _FakeModelLoader)
    monkeypatch.setattr(api, "HF_TOKEN", "hf_dummytoken")
    monkeypatch.setattr(api, "VALID_API_KEY_HASHES", {api._hash_key(VALID_KEY)})
    # Не перетираем хэши при старте lifespan
    monkeypatch.setattr(api, "load_api_keys", lambda: None)
    with TestClient(api.app) as c:
        yield c


def test_health_no_auth(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["model_loaded"] is True


def test_upload_without_key_rejected(client):
    # Отсутствует обязательный заголовок X-API-Key → 422 (валидация заголовка)
    r = client.post("/api/v1/transcribe", files={"file": ("a.mp3", b"x", "audio/mpeg")})
    assert r.status_code in (401, 422)


def test_upload_wrong_key_is_401(client):
    r = client.post(
        "/api/v1/transcribe",
        headers={"X-API-Key": "gam_wrong"},
        files={"file": ("a.mp3", b"x", "audio/mpeg")},
    )
    assert r.status_code == 401


def test_bad_task_id_rejected(client):
    r = client.get("/api/v1/tasks/not-a-valid-id/result", headers={"X-API-Key": VALID_KEY})
    assert r.status_code == 400


def test_unsupported_format_is_400_not_500(client):
    # Регрессия Phase 0.2: 4xx не должен маскироваться под 500
    r = client.post(
        "/api/v1/transcribe",
        headers={"X-API-Key": VALID_KEY},
        files={"file": ("notes.exe", b"x", "application/octet-stream")},
    )
    assert r.status_code == 400


def test_cors_not_wildcard_with_credentials(client):
    # Phase 0.1: не должно быть Access-Control-Allow-Origin: * с credentials
    r = client.get(
        "/health",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert r.headers.get("access-control-allow-origin") != "*"


def test_invalid_format_query_is_422(client):
    # Phase 2.7: format ограничен Literal["txt","timecodes"]
    tid = "a" * 32
    r = client.get(f"/api/v1/tasks/{tid}/download?format=exe", headers={"X-API-Key": VALID_KEY})
    assert r.status_code == 422


def test_invalid_limit_query_is_422(client):
    # Phase 2.7: limit ограничен диапазоном [1, 1000]
    r = client.get("/api/v1/tasks?limit=0", headers={"X-API-Key": VALID_KEY})
    assert r.status_code == 422
