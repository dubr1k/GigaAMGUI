"""OpenAI-compatible API: auth, models, error envelope. Model and processor are fakes."""
import importlib
import json

import pytest

try:
    from fastapi.testclient import TestClient
    _HAS_CLIENT = True
except Exception:  # pragma: no cover
    _HAS_CLIENT = False

pytestmark = pytest.mark.skipif(not _HAS_CLIENT, reason="нужен fastapi TestClient")

api = importlib.import_module("api")
VALID_KEY = "gam_openai_test"


class _FakeModelLoader:
    requested_backend = "auto"
    requested_model = "v3_e2e_rnnt"
    requested_provider = "auto"

    def load_model(self, logger=None):
        return True

    def is_loaded(self):
        return True

    def diagnostics(self):
        return {"requested_backend": "auto", "active_backend": "mlx", "model": "v3_e2e_rnnt", "device": "mps"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api, "ModelLoader", _FakeModelLoader)
    monkeypatch.setattr(api, "HF_TOKEN", "hf_dummytoken")
    monkeypatch.setattr(api, "VALID_API_KEY_HASHES", {api._hash_key(VALID_KEY)})
    monkeypatch.setattr(api, "load_api_keys", lambda: None)
    monkeypatch.setattr(api.limiter, "enabled", False)  # 10/minute would trip across the module
    with TestClient(api.app) as c:
        yield c


BEARER = {"Authorization": f"Bearer {VALID_KEY}"}


def _error(resp):
    body = resp.json()
    assert set(body) == {"error"} and set(body["error"]) == {"message", "type", "param", "code"}
    return body["error"]


def test_health_needs_no_key(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["model_loaded"] is True


def test_root_points_to_docs(client):
    body = client.get("/").json()
    assert body["docs"] == "/docs" and "/v1/audio/transcriptions" in json.dumps(body)


def test_models_require_key_in_openai_envelope(client):
    r = client.get("/v1/models")
    assert r.status_code == 401
    err = _error(r)
    assert err["type"] == "authentication_error" and err["code"] == "invalid_api_key"


def test_models_accept_bearer_and_x_api_key(client):
    assert client.get("/v1/models", headers=BEARER).status_code == 200
    assert client.get("/v1/models", headers={"X-API-Key": VALID_KEY}).status_code == 200
    assert client.get("/v1/models", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_models_list_shape(client):
    body = client.get("/v1/models", headers=BEARER).json()
    assert body["object"] == "list"
    ids = [m["id"] for m in body["data"]]
    assert ids == ["v3_e2e_rnnt", "multilingual_ctc", "multilingual_large_ctc"]
    default = body["data"][0]
    assert default["object"] == "model" and default["owned_by"] == "gigaam" and default["created"] == 0
    assert default["default"] is True and "whisper-1" in default["aliases"]
    assert body["gigaam"]["active"]["active_backend"] == "mlx"
    assert "backends" in body["gigaam"] and "onnx_providers" in body["gigaam"]


def test_model_by_id_and_alias(client):
    assert client.get("/v1/models/whisper-1", headers=BEARER).json()["id"] == "v3_e2e_rnnt"
    r = client.get("/v1/models/nope", headers=BEARER)
    assert r.status_code == 404 and _error(r)["code"] == "model_not_found"


def test_old_routes_are_gone_with_openai_404(client):
    r = client.post("/api/v1/transcribe", headers=BEARER)
    assert r.status_code == 404 and _error(r)["type"] == "invalid_request_error"


def test_translations_rejected(client):
    r = client.post("/v1/audio/translations", headers=BEARER, files={"file": ("a.wav", b"RIFF", "audio/wav")}, data={"model": "whisper-1"})
    assert r.status_code == 400 and _error(r)["code"] == "translation_not_supported"


def test_validation_errors_use_envelope(client):
    r = client.post("/v1/audio/transcriptions", headers=BEARER, data={"model": "whisper-1"})  # no file
    assert r.status_code == 422
    err = _error(r)
    assert err["type"] == "invalid_request_error" and err["param"] == "file"
