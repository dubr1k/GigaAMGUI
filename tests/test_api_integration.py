"""HTTP-уровневые тесты исправлений безопасности API (Phase 0.1, 0.3, 0.4).

Модель подменяется заглушкой, чтобы lifespan не грузил реальную GigaAM.
"""

import importlib
import json
from types import SimpleNamespace

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
    requested_backend = "auto"
    requested_model = "v3_e2e_rnnt"
    requested_provider = "auto"

    def load_model(self, logger=None):
        return True

    def is_loaded(self):
        return True

    def diagnostics(self):
        return {
            "requested_backend": "auto",
            "active_backend": "mlx",
            "fallback_reason": None,
            "model": "aystream/GigaAM-v3-e2e-rnnt-mlx",
            "device": "mps",
            "cache_root": None,
            "repo": "repo/test",
            "loader_loaded": True,
            "error": None,
        }


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
    assert "asr" in r.json()
    assert r.json()["asr"]["active_backend"] == "mlx"


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


def test_asr_options_expose_all_selectable_backends(client):
    r = client.get("/api/v1/asr/options", headers={"X-API-Key": VALID_KEY})

    assert r.status_code == 200
    assert set(r.json()["backends"]) == {"auto", "onnx", "mlx", "pytorch"}
    assert "multilingual_large_ctc" in r.json()["models"]
    assert "coreml" in r.json()["onnx_providers"]


def test_invalid_asr_backend_is_rejected_before_upload(client):
    r = client.post(
        "/api/v1/transcribe?asr_backend=whisper",
        headers={"X-API-Key": VALID_KEY},
        files={"file": ("a.mp3", b"x", "audio/mpeg")},
    )

    assert r.status_code == 400
    assert "backend" in r.json()["detail"]


def test_transcribe_api_exposes_diarization_query_parameters(client):
    schema = client.get("/openapi.json").json()
    parameters = {
        item["name"]
        for item in schema["paths"]["/api/v1/transcribe"]["post"]["parameters"]
    }

    assert {"enable_diarization", "diarization_backend", "num_speakers"} <= parameters


def test_api_rejects_fixed_speaker_count_for_sortformer(client):
    response = client.post(
        "/api/v1/transcribe?enable_diarization=true&diarization_backend=sortformer&num_speakers=2",
        headers={"X-API-Key": VALID_KEY},
        files={"file": ("a.mp3", b"x", "audio/mpeg")},
    )

    assert response.status_code == 400
    assert "автоматически" in response.json()["detail"]


def test_restore_preserves_asr_and_diarization_metadata(tmp_path, monkeypatch):
    task_id = "b" * 32
    task_dir = tmp_path / task_id
    task_dir.mkdir()
    (task_dir / "meta.json").write_text(
        json.dumps({
            "filename": "meeting.wav",
            "asr_backend": "onnx",
            "asr_model": "multilingual_ctc",
            "onnx_provider": "coreml",
            "asr_diagnostics": {"active_backend": "onnx"},
            "enable_diarization": True,
            "diarization_backend": "onnx",
            "num_speakers": 2,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(api, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(api, "logger", SimpleNamespace(info=lambda _message: None))
    api.tasks_storage.clear()

    api.restore_tasks_from_results()

    restored = api.tasks_storage[task_id]
    assert restored["asr_backend"] == "onnx"
    assert restored["asr_model"] == "multilingual_ctc"
    assert restored["onnx_provider"] == "coreml"
    assert restored["asr_diagnostics"] == {"active_backend": "onnx"}
    assert restored["enable_diarization"] is True
    assert restored["diarization_backend"] == "onnx"
    assert restored["num_speakers"] == 2
