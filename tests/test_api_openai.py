"""OpenAI-compatible API: auth, models, error envelope. Model and processor are fakes."""
import importlib
import json
from pathlib import Path

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


# ---------- auth / envelope edge cases (Task 3 review follow-ups) ----------


def test_auth_header_edge_cases(client):
    assert client.get("/v1/models", headers={"Authorization": f"bearer {VALID_KEY}"}).status_code == 200
    r = client.get("/v1/models", headers={"Authorization": "Bearer "})
    assert r.status_code == 401 and _error(r)["code"] == "invalid_api_key"
    r = client.get("/v1/models", headers={"Authorization": "Basic dXNlcjpwYXNz", "X-API-Key": VALID_KEY})
    assert r.status_code == 200


def test_method_not_allowed_uses_envelope(client):
    r = client.get("/v1/audio/transcriptions", headers=BEARER)
    assert r.status_code == 405 and _error(r)["type"] == "invalid_request_error"


def test_unhandled_exception_uses_envelope(monkeypatch):
    monkeypatch.setattr(api, "ModelLoader", _FakeModelLoader)
    monkeypatch.setattr(api, "VALID_API_KEY_HASHES", {api._hash_key(VALID_KEY)})
    monkeypatch.setattr(api, "load_api_keys", lambda: None)
    monkeypatch.setattr(api.limiter, "enabled", False)
    monkeypatch.setattr(api, "API_DEBUG", False)
    monkeypatch.setattr(api, "models_payload", lambda: (_ for _ in ()).throw(RuntimeError("secret detail")))
    with TestClient(api.app, raise_server_exceptions=False) as c:
        r = c.get("/v1/models", headers=BEARER)
    assert r.status_code == 500
    err = _error(r)
    assert err["type"] == "server_error" and err["code"] == "internal_error"
    assert err["message"] == "Internal server error." and "secret" not in err["message"]


def test_rate_limit_envelope_carries_retry_after(monkeypatch):
    monkeypatch.setattr(api, "ModelLoader", _FakeModelLoader)
    monkeypatch.setattr(api, "VALID_API_KEY_HASHES", {api._hash_key(VALID_KEY)})
    monkeypatch.setattr(api, "load_api_keys", lambda: None)
    monkeypatch.setattr(api.limiter, "enabled", True)
    api.limiter.reset()
    with TestClient(api.app) as c:
        last = None
        for _ in range(11):
            # Валидный multipart, но неизвестная модель: лимитер срабатывает до тяжёлой работы
            last = c.post("/v1/audio/transcriptions", headers=BEARER,
                          files={"file": ("a.wav", b"RIFF", "audio/wav")}, data={"model": "gpt-9"})
            if last.status_code == 429:
                break
    api.limiter.reset()
    assert last is not None and last.status_code == 429
    err = _error(last)
    assert err["type"] == "rate_limit_error" and err["code"] == "rate_limit_exceeded"
    assert "retry-after" in last.headers


# ---------- POST /v1/audio/transcriptions ----------

UTTS = [
    {"transcription": "Привет,", "boundaries": (0.0, 1.5), "speaker": "SPEAKER_01"},
    {"transcription": "как дела?", "boundaries": (1.5, 3.25), "speaker": "SPEAKER_00",
     "words": [{"text": "как", "start": 1.5, "end": 2.0}, {"text": "дела?", "start": 2.0, "end": 3.25}]},
]


class _FakeProcessor:
    calls: list = []

    def __init__(self, *_, **kw):
        self.progress_callback = kw.get("progress_callback")

    def process_file(self, filepath, output_dir, *_a, **kw):
        _FakeProcessor.calls.append({"filepath": filepath, "output_dir": output_dir, **kw})
        if self.progress_callback:
            self.progress_callback("transcription", 0.5, total_seconds=3.25, processed_seconds=1.6)
        if kw.get("_fail"):
            return {"success": False}
        return {"success": True, "media_duration": 3.25, "total_time": 0.1, "utterances": list(UTTS),
                "diarization": {"requested": bool(kw.get("enable_diarization")), "applied": bool(kw.get("enable_diarization"))}}


@pytest.fixture
def fake_processor(monkeypatch):
    _FakeProcessor.calls = []
    monkeypatch.setattr(api.transcription_service, "build_processor", lambda *a, **kw: _FakeProcessor(*a, **kw))
    return _FakeProcessor


def _post(client, data=None, **kw):
    payload = {"model": "whisper-1"}
    payload.update(data or {})
    return client.post("/v1/audio/transcriptions", headers=BEARER,
                       files={"file": ("speech.wav", b"RIFF....", "audio/wav")}, data=payload, **kw)


def test_transcription_json_default(client, fake_processor):
    r = _post(client)
    assert r.status_code == 200, r.text
    assert r.json() == {"text": "Привет, как дела?", "usage": {"type": "duration", "seconds": 4}}
    call = fake_processor.calls[0]
    assert call["output_formats"] == [] and call["enable_diarization"] is False
    assert call["filepath"].endswith("speech.wav")


def test_transcription_cleans_temp_dir(client, fake_processor):
    _post(client)
    assert not Path(fake_processor.calls[0]["output_dir"]).exists()


def test_transcription_text_srt_vtt(client, fake_processor):
    r = _post(client, {"response_format": "text"})
    assert r.headers["content-type"].startswith("text/plain") and r.text == "Привет, как дела?"
    r = _post(client, {"response_format": "srt"})
    assert r.headers["content-type"].startswith("application/x-subrip") and r.text.startswith("1\n")
    r = _post(client, {"response_format": "vtt"})
    assert r.headers["content-type"].startswith("text/vtt") and r.text.startswith("WEBVTT")


def test_transcription_verbose_with_words(client, fake_processor):
    r = _post(client, {"response_format": "verbose_json", "timestamp_granularities[]": ["segment", "word"], "language": "ru"})
    body = r.json()
    assert body["task"] == "transcribe" and body["duration"] == 3.25 and len(body["segments"]) == 2
    assert body["words"][0] == {"word": "как", "start": 1.5, "end": 2.0}
    assert "speaker" not in body["segments"][0]


def test_diarized_json_turns_diarization_on(client, fake_processor):
    r = _post(client, {"response_format": "diarized_json"})
    assert r.status_code == 200
    assert [s["speaker"] for s in r.json()["segments"]] == ["A", "B"]
    assert fake_processor.calls[0]["enable_diarization"] is True


def test_diarize_flag_adds_speakers_to_verbose(client, fake_processor):
    r = _post(client, {"response_format": "verbose_json", "diarize": "true", "num_speakers": "2"})
    assert r.json()["segments"][0]["speaker"] == "A"
    assert fake_processor.calls[0]["num_speakers"] == 2


def test_unknown_model_404(client, fake_processor):
    r = _post(client, {"model": "gpt-9"})
    assert r.status_code == 404 and _error(r)["code"] == "model_not_found" and _error(r)["param"] == "model"


def test_unsupported_extension_400(client, fake_processor):
    r = client.post("/v1/audio/transcriptions", headers=BEARER, files={"file": ("x.exe", b"MZ", "application/octet-stream")}, data={"model": "whisper-1"})
    assert r.status_code == 400 and _error(r)["code"] == "unsupported_file" and _error(r)["param"] == "file"


def test_file_too_large_413(client, fake_processor, monkeypatch):
    monkeypatch.setattr(api, "MAX_FILE_SIZE", 4)
    r = _post(client)
    assert r.status_code == 413 and _error(r)["code"] == "file_too_large"


def test_bad_response_format_400(client, fake_processor):
    r = _post(client, {"response_format": "xml"})
    assert r.status_code == 400 and _error(r)["code"] == "unsupported_response_format"


def test_stream_only_json_formats(client, fake_processor):
    r = _post(client, {"stream": "true", "response_format": "srt"})
    assert r.status_code == 400 and _error(r)["code"] == "stream_not_supported"


def test_known_speaker_names_rejected(client, fake_processor):
    r = _post(client, {"known_speaker_names[]": ["Alice"]})
    assert r.status_code == 400 and _error(r)["param"] == "known_speaker_names"


def test_sortformer_with_num_speakers_400(client, fake_processor):
    r = _post(client, {"diarize": "true", "diarization_backend": "sortformer", "num_speakers": "2"})
    assert r.status_code == 400 and _error(r)["param"] == "num_speakers"


def test_bad_granularity_400(client, fake_processor):
    r = _post(client, {"response_format": "verbose_json", "timestamp_granularities[]": ["char"]})
    assert r.status_code == 400 and _error(r)["param"] == "timestamp_granularities"


def test_bad_diarization_backend_400(client, fake_processor):
    r = _post(client, {"diarize": "true", "diarization_backend": "magic"})
    assert r.status_code == 400 and _error(r)["param"] == "diarization_backend"


def test_bad_asr_backend_400(client, fake_processor):
    r = _post(client, {"asr_backend": "cuda-magic"})
    assert r.status_code == 400 and _error(r)["param"] == "asr_backend"


def test_diarization_without_hf_token_503(client, fake_processor, monkeypatch):
    monkeypatch.setattr(api, "HF_TOKEN", "")
    r = _post(client, {"diarize": "true"})
    assert r.status_code == 503 and _error(r)["code"] == "diarization_unavailable"


def test_processor_failure_is_server_error_and_cleans_up(client, fake_processor, monkeypatch):
    def boom(self, *a, **kw):
        _FakeProcessor.calls.append({"output_dir": a[1]})
        raise RuntimeError("ffmpeg exploded")
    monkeypatch.setattr(_FakeProcessor, "process_file", boom)
    r = _post(client)
    assert r.status_code == 500 and _error(r)["type"] == "server_error"
    assert "ffmpeg" not in _error(r)["message"]  # no internals leaked
    assert not Path(fake_processor.calls[0]["output_dir"]).exists()


def _sse_events(text):
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def test_stream_json_events(client, fake_processor):
    r = _post(client, {"stream": "true"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = _sse_events(r.text)
    assert [e["type"] for e in events] == ["transcript.text.delta", "transcript.text.delta", "transcript.text.done"]
    assert events[0]["delta"] == "Привет, " and events[1]["delta"] == "как дела?"
    assert events[-1] == {"type": "transcript.text.done", "text": "Привет, как дела?", "usage": {"type": "duration", "seconds": 4}}
    assert ": progress" in r.text  # keep-alive comment from the progress callback
    assert not Path(fake_processor.calls[0]["output_dir"]).exists()


def test_stream_verbose_done_carries_segments(client, fake_processor):
    r = _post(client, {"stream": "true", "response_format": "verbose_json"})
    done = _sse_events(r.text)[-1]
    assert done["type"] == "transcript.text.done" and len(done["segments"]) == 2 and done["duration"] == 3.25


def test_stream_error_event(client, fake_processor, monkeypatch):
    monkeypatch.setattr(_FakeProcessor, "process_file", lambda self, *a, **kw: (_ for _ in ()).throw(RuntimeError("x")))
    r = _post(client, {"stream": "true"})
    events = _sse_events(r.text)
    assert events[-1]["type"] == "error" and events[-1]["error"]["type"] == "server_error"
