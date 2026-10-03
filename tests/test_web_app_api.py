"""Веб-панель (web/web_app.py): фоновые задачи и HTTP-контракт эндпоинтов.

Модель, процессор и загрузчик — подделки: ни железа, ни сети. HTTP-тесты идут
через TestClient на https://testserver, чтобы браузерная Secure-cookie сессии
отправлялась так же, как в проде за TLS.
"""
import asyncio
import importlib
import os
from pathlib import Path

import pytest
import yt_dlp

pytest.importorskip("fastapi")

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

os.environ.setdefault("WEB_SECRET", "x" * 32)
os.environ.setdefault("WEB_USERNAME", "test-user")
os.environ.setdefault("WEB_PASSWORD", "test-password")

web_app = importlib.import_module("web.web_app")
from src.core.subtitles import SubtitleOptions  # noqa: E402
from src.services import transcription_service  # noqa: E402
from web import auth, jobs  # noqa: E402
from web.routes import llm as llm_routes  # noqa: E402
from web.routes import transcribe as transcribe_routes  # noqa: E402
from web.state import state, validated_login_rate_limit  # noqa: E402
from web.task_registry import registry  # noqa: E402


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
    monkeypatch.setattr(state, "upload_dir", upload_dir)
    monkeypatch.setattr(state, "results_dir", results_dir)
    monkeypatch.setattr(state, "api_keys_file", tmp_path / ".api_keys")
    monkeypatch.setattr(state, "loader_factory", _FakeLoader)
    monkeypatch.setattr(state, "hf_token", "")
    auth.limiter.reset()  # лимит входа — в памяти процесса, общий для всех тестов
    registry.tasks.clear()
    registry.logs.clear()
    registry.deleted.clear()
    yield upload_dir, results_dir
    registry.tasks.clear()
    registry.logs.clear()
    registry.deleted.clear()


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
    monkeypatch.setattr(transcription_service, "build_processor", lambda *a, **kw: _Processor(*a, **kw))
    return _Processor


def _login(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"username": state.username, "password": state.password})
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
        "output_formats": list(formats), "user": user or state.username,
        "result_files": [{"name": "voice.txt", "path": str(task_dir / "voice.txt"), "size": 12, "format": "txt"}],
    }
    registry.tasks[task_id] = task
    return task


def _run_processing(task_id: str, file_path, filename: str):
    async def scenario():
        state.processing_semaphore = asyncio.Semaphore(1)
        await jobs.process_transcription(task_id, file_path, filename, ["txt"], False, "pyannote", None)

    asyncio.run(scenario())


# ==================== фоновая обработка ====================


def test_failed_file_shows_processor_reason(web_dirs, fake_processor, monkeypatch):
    upload_dir, _ = web_dirs
    monkeypatch.setattr(state, "model_loader", _FakeLoader())
    source = upload_dir / "t1_voice.wav"
    source.write_bytes(b"RIFF")
    registry.register("t1", "voice.wav", 4, "alice")
    fake_processor.result = {"success": False, "error": "boom"}

    _run_processing("t1", source, "voice.wav")

    task = registry.tasks["t1"]
    assert task["status"] == "failed"
    assert task["message"] == "boom"


def test_failed_file_without_reason_keeps_generic_message(web_dirs, fake_processor, monkeypatch):
    upload_dir, _ = web_dirs
    monkeypatch.setattr(state, "model_loader", _FakeLoader())
    source = upload_dir / "t2_voice.wav"
    source.write_bytes(b"RIFF")
    registry.register("t2", "voice.wav", 4, "alice")
    fake_processor.result = {"success": False}

    _run_processing("t2", source, "voice.wav")

    assert registry.tasks["t2"]["message"] == "Обработка не удалась"


# ==================== /health ====================


def test_app_reports_release_version(anon_client):
    from src import __version__

    assert web_app.app.version == __version__
    assert anon_client.get("/health").json()["version"] == __version__


def test_public_health_has_no_server_paths(anon_client):
    response = anon_client.get("/health")
    assert response.status_code == 200
    asr = response.json()["asr"]
    assert asr["active_backend"] == "pytorch"
    assert "cache_root" not in asr and "repo" not in asr
    assert "/srv/secret/models" not in response.text


# ==================== вход ====================


def test_login_attempts_are_rate_limited(anon_client):
    limit = int(state.login_rate_limit.split("/")[0])
    bad = {"username": state.username, "password": "wrong"}
    statuses = [anon_client.post("/api/auth/login", json=bad).status_code for _ in range(limit)]
    assert set(statuses) == {401}
    blocked = anon_client.post("/api/auth/login", json=bad)
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers
    assert blocked.json()["detail"]
    # Перебор пароля дальше не проверяется — даже верный пароль ждёт окна
    good = {"username": state.username, "password": state.password}
    assert anon_client.post("/api/auth/login", json=good).status_code == 429


def test_login_rate_limit_setting_is_validated():
    assert validated_login_rate_limit("") == "10/minute"
    assert validated_login_rate_limit("3/minute;20/hour") == "3/minute;20/hour"
    with pytest.raises(ValueError):
        validated_login_rate_limit("ten per minute")


# ==================== тело запроса до авторизации ====================


def _asgi_request(method: str, path: str, headers: dict[str, str], body: bytes = b"") -> tuple[int, int]:
    """Запрос прямо в ASGI-приложение; возвращает (статус, сколько раз читали тело)."""
    reads = 0
    sent = []

    async def receive():
        nonlocal reads
        reads += 1
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
        "scheme": "https", "path": path, "raw_path": path.encode(), "root_path": "", "query_string": b"",
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()],
        "server": ("testserver", 443), "client": ("127.0.0.1", 50000),
    }
    asyncio.run(web_app.app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    return status, reads


_MULTIPART = (
    b"--b\r\nContent-Disposition: form-data; name=\"files\"; filename=\"a.wav\"\r\n"
    b"Content-Type: audio/wav\r\n\r\nRIFF\r\n--b--\r\n"
)


@pytest.mark.parametrize("path", ["/api/upload", "/api/llm/process", "/api/download-url"])
def test_unauthenticated_body_is_rejected_before_it_is_read(web_dirs, path):
    # FastAPI разбирает multipart (и спулит файлы в /tmp) раньше Depends(require_auth):
    # без гарда любой мог залить гигабайты, получив 401 только после записи
    status, reads = _asgi_request("POST", path, {
        "content-type": "multipart/form-data; boundary=b", "content-length": str(len(_MULTIPART)),
    }, _MULTIPART)
    assert status == 401
    assert reads == 0


def test_invalid_token_is_rejected_before_body(web_dirs):
    status, reads = _asgi_request("POST", "/api/upload", {
        "content-type": "multipart/form-data; boundary=b", "content-length": str(len(_MULTIPART)),
        "cookie": "gigaam_token=forged", "authorization": "Bearer nope",
    }, _MULTIPART)
    assert (status, reads) == (401, 0)


def test_oversized_upload_is_rejected_by_headers(web_dirs):
    token = auth.create_token(state.username)
    status, reads = _asgi_request("POST", "/api/upload", {
        "content-type": "multipart/form-data; boundary=b",
        "content-length": str(state.max_file_size + 64 * 1024 * 1024),
        "authorization": f"Bearer {token}",
    })
    assert (status, reads) == (413, 0)


def test_login_and_authenticated_upload_still_pass_the_guard(client, web_dirs, fake_processor):
    # client уже вошёл через POST /api/auth/login (тело без авторизации — исключение гарда)
    response = client.post("/api/upload", files={"files": ("voice.wav", b"RIFF", "audio/wav")},
                           data={"output_formats": "txt"})
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1


# ==================== CSRF: источник изменяющих запросов ====================


def _upload(client, headers=None):
    return client.post("/api/upload", files={"files": ("voice.wav", b"RIFF", "audio/wav")},
                       data={"output_formats": "txt"}, headers=headers or {})


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"},
    {"Referer": "https://evil.example/page"},
    {"Origin": "null"},
    {"Origin": "https://testserver.evil.example"},
    {"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"},
    {"Sec-Fetch-Site": "same-site", "Origin": "https://other.testserver"},
    {"Sec-Fetch-Site": "cross-site"},
], ids=["origin", "referer", "null-origin", "suffix-host", "fetch-cross-site", "fetch-same-site", "fetch-only"])
def test_cookie_request_from_foreign_origin_is_rejected(client, web_dirs, fake_processor, headers):
    response = _upload(client, headers)
    assert response.status_code == 403
    assert registry.tasks == {}


def test_cookie_delete_from_foreign_origin_is_rejected(client, web_dirs):
    _, results_dir = web_dirs
    _completed_task(results_dir, "keep1")
    response = client.delete("/api/tasks", params={"status_filter": "all"}, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert "keep1" in registry.tasks


@pytest.mark.parametrize("headers", [
    {"Origin": "https://testserver"},
    {"Referer": "https://testserver/"},
    {},  # не браузер: curl/скрипт без Origin и Referer
    {"Origin": "https://gigaam.example.com", "Host": "127.0.0.1:8000", "X-Forwarded-Host": "gigaam.example.com"},
    # nginx без `proxy_set_header Host`: Host внутренний, но браузер сам говорит same-origin
    {"Origin": "https://gigaam.example.com", "Host": "127.0.0.1:8001", "Sec-Fetch-Site": "same-origin"},
], ids=["same-origin", "same-referer", "no-headers", "behind-proxy", "proxy-without-host"])
def test_same_origin_cookie_requests_pass(client, web_dirs, fake_processor, headers):
    assert _upload(client, headers).status_code == 200


def test_bearer_requests_skip_origin_check(anon_client, web_dirs, fake_processor):
    # Токен в заголовке браузер сам не подставит — CSRF тут невозможен
    token = auth.create_token(state.username)
    response = _upload(anon_client, {"Authorization": f"Bearer {token}", "Origin": "https://tool.example"})
    assert response.status_code == 200


def test_forged_bearer_does_not_bypass_origin_check(client, web_dirs, fake_processor):
    # Мусорный Bearer при живой cookie не должен отключать проверку источника
    response = _upload(client, {"Authorization": "Bearer junk", "Origin": "https://evil.example"})
    assert response.status_code == 403


def test_trusted_origins_are_accepted(client, web_dirs, fake_processor, monkeypatch):
    monkeypatch.setattr(state, "trusted_origins", ("https://dev.example:5173",))
    assert _upload(client, {"Origin": "https://dev.example:5173"}).status_code == 200


def test_no_credentialed_cors_for_localhost_by_default(anon_client):
    # Раньше http://localhost:8001 получал CORS с credentials — любая страница на этом порту читала API панели
    response = anon_client.options("/api/tasks", headers={
        "Origin": "http://localhost:8001", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in response.headers
    response = anon_client.get("/health", headers={"Origin": "http://localhost:8001"})
    assert "access-control-allow-credentials" not in response.headers


# ==================== LLM: что решает клиент, а что сервер ====================


@pytest.fixture
def llm_env(tmp_path, monkeypatch):
    """Серверные настройки LLM в tmp; run_provider и скан CLI — подделки."""
    from src.services import cli_tools, llm_service

    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "user_settings.json").write_text(
        '{"llm_claude_path": "/opt/server/claude", "llm_claude_args": "--server-arg", "llm_allow_tools": true}',
        encoding="utf-8")
    monkeypatch.setenv("GIGAAM_CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(cli_tools, "scan", lambda overrides=None, *, fresh=False: [])
    captured = {}

    def fake_run_provider(settings, text, prompt, *, provider, strict_empty_cli, **_):
        captured["settings"] = dict(settings)
        captured["provider"] = provider
        return "ответ"

    monkeypatch.setattr(llm_service, "run_provider", fake_run_provider)
    return captured


_HOSTILE_LLM_FORM = {
    "provider": "Claude Code", "api_url": "https://llm.example/v1", "api_key": "sk-client", "model": "sonnet",
    "temperature": "0.5", "claude_path": "/bin/sh", "claude_args": "-c 'touch /tmp/pwned'",
    "other_path": "/bin/sh", "other_args": "-c id", "pi_provider": "anthropic",
    "llm_allow_tools": "true", "summary_enabled": "true", "manual_text": "текст встречи", "export_formats": "txt",
}


def test_llm_process_ignores_client_cli_paths_args_and_tools(client, llm_env):
    # Украденная cookie не должна превращаться в запуск произвольной команды в контейнере
    response = client.post("/api/llm/process", data=_HOSTILE_LLM_FORM)
    assert response.status_code == 200, response.text
    settings = llm_env["settings"]
    assert settings["claude_path"] == "/opt/server/claude"
    assert settings["claude_args"] == "--server-arg"
    assert settings["other_path"] != "/bin/sh" and settings["other_args"] != "-c id"
    assert settings["llm_allow_tools"] is False
    # Поля API-провайдера и модель остаются за клиентом
    assert (settings["api_url"], settings["api_key"], settings["model"], settings["temperature"]) == (
        "https://llm.example/v1", "sk-client", "sonnet", 0.5)
    assert settings["pi_provider"] == "anthropic"


def test_llm_process_honours_client_cli_when_operator_allows(client, llm_env, monkeypatch):
    monkeypatch.setattr(state, "allow_client_llm_cli", True)
    response = client.post("/api/llm/process", data=_HOSTILE_LLM_FORM)
    assert response.status_code == 200, response.text
    settings = llm_env["settings"]
    assert settings["claude_path"] == "/bin/sh" and settings["other_path"] == "/bin/sh"
    assert settings["llm_allow_tools"] is True


def test_llm_calls_are_bounded_by_their_own_semaphore(monkeypatch):
    # Каждый запрос гонял items×modes вызовов CLI/API без всякого ограничения параллельности
    import threading

    from src.services import llm_service

    lock = threading.Lock()
    inflight = {"now": 0, "max": 0}

    def slow_provider(settings, text, prompt, *, provider, strict_empty_cli, **_):
        with lock:
            inflight["now"] += 1
            inflight["max"] = max(inflight["max"], inflight["now"])
        threading.Event().wait(0.05)
        with lock:
            inflight["now"] -= 1
        return "ok"

    monkeypatch.setattr(llm_service, "run_provider", slow_provider)
    monkeypatch.setattr(state, "llm_semaphore", None)  # вернуть после теста

    async def scenario():
        state.llm_semaphore = asyncio.Semaphore(1)
        return await asyncio.gather(*(llm_routes._llm_answer({"provider": "API"}, "t", "p") for _ in range(3)))

    assert asyncio.run(scenario()) == ["ok", "ok", "ok"]
    assert inflight["max"] == 1


def test_llm_transcript_over_the_limit_is_rejected_without_reading_it_all(client, llm_env, monkeypatch):
    monkeypatch.setattr(state, "max_llm_body_size", 100)
    response = client.post(
        "/api/llm/process",
        data={"provider": "API", "summary_enabled": "true", "export_formats": "txt"},
        files={"transcript_files": ("long.txt", "слово ".encode() * 200, "text/plain")},
    )
    assert response.status_code == 413
    assert "settings" not in llm_env  # до LLM дело не дошло


def test_llm_result_does_not_expose_server_paths(client, llm_env, web_dirs):
    response = client.post("/api/llm/process", data={
        "provider": "API", "summary_enabled": "true", "manual_text": "текст", "export_formats": "txt,md"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [f["name"] for f in body["saved_files"]] == ["manual_transcript_llm_summary.txt",
                                                        "manual_transcript_llm_summary.md"]
    assert str(web_dirs[1]) not in response.text and "/llm/" not in response.text
    download = client.get(f"/api/llm/download/{body['job_id']}/manual_transcript_llm_summary.txt")
    assert download.status_code == 200 and download.text == "ответ"


def test_llm_rejects_unknown_export_format(client, llm_env):
    response = client.post("/api/llm/process", data={
        "provider": "API", "summary_enabled": "true", "manual_text": "текст", "export_formats": "txt,pdf"})
    assert response.status_code == 400
    assert "settings" not in llm_env


def test_llm_tool_check_does_not_run_client_path(client, llm_env, monkeypatch):
    from src.services import cli_tools

    probed = []

    def fake_resolve(spec, override=None):
        probed.append(override)
        return cli_tools.ToolStatus(spec.id, spec.name, "missing", None, None, None, spec.install_hint)

    monkeypatch.setattr(cli_tools, "resolve_tool", fake_resolve)
    response = client.post("/api/llm/tools/check", data={"provider": "Claude Code", "path": "/bin/sh"})
    assert response.status_code == 200
    assert probed == ["/opt/server/claude"]  # настроенный на сервере путь, а не присланный


# ==================== фоновые задачи ====================


def test_background_jobs_are_referenced_until_done(web_dirs, monkeypatch):
    # Цикл событий держит на задачи только слабые ссылки: без своей ссылки
    # create_task-задача может быть собрана сборщиком мусора посреди работы
    release = None
    seen = {}

    async def fake_download(*args):
        await release.wait()

    monkeypatch.setattr(jobs, "download_and_process", fake_download)
    monkeypatch.setattr(state, "model_loader", _FakeLoader())

    async def scenario():
        nonlocal release
        release = asyncio.Event()
        await transcribe_routes.download_from_url(
            request=None, user="alice", url="https://example.com/v", output_formats="txt",
            enable_diarization=False, diarization_backend="pyannote", num_speakers="", asr_backend="",
            asr_model="", onnx_provider="", subtitle_sentence_split=True, subtitle_max_lines=2,
            subtitle_max_width=64,
        )
        await asyncio.sleep(0)
        seen["running"] = len(jobs.background_jobs)
        release.set()
        for _ in range(5):
            await asyncio.sleep(0)
        seen["after"] = len(jobs.background_jobs)

    asyncio.run(scenario())
    assert seen == {"running": 1, "after": 0}


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
    assert registry.tasks == {}


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
    assert registry.tasks == {}
    assert fake_processor.calls == []


def test_upload_failure_on_a_later_file_starts_nothing(web_dirs, monkeypatch):
    # Файл k не записался (413, диск) — уже сохранённые 1..k-1 удаляются, ни одна задача не стартует
    upload_dir, _ = web_dirs
    started = []

    async def fake_save_upload(file, _request):
        if file.filename == "big.wav":
            raise HTTPException(status_code=413, detail="too large")
        path = upload_dir / f"id-{file.filename}"
        path.write_bytes(b"RIFF")
        return f"id-{file.filename}", path, file.filename, 4

    async def fake_process(*args):
        started.append(args)

    monkeypatch.setattr(transcribe_routes, "_save_upload", fake_save_upload)
    monkeypatch.setattr(jobs, "process_transcription", fake_process)
    monkeypatch.setattr(state, "model_loader", _FakeLoader())

    class _File:
        def __init__(self, filename):
            self.filename = filename

    async def scenario():
        with pytest.raises(HTTPException) as info:
            await transcribe_routes.upload_files(
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
    assert registry.tasks == {}
    assert started == []


def test_download_url_rejects_unknown_output_format(client):
    response = client.post("/api/download-url", data={"url": "https://example.com/v", "output_formats": "pdf"})
    assert response.status_code == 400
    assert registry.tasks == {}


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


# ==================== загрузка по URL ====================


class _FakeDownloader:
    """MediaDownloader-подделка: пишет в target_dir как yt-dlp; `fail` — упасть, оставив .part."""

    def __init__(self, *, fail: bool = False, name: str = "Song title.m4a"):
        self.fail = fail
        self.name = name
        self.calls = []

    def download(self, url, target_dir, progress_callback=None, **kwargs):
        from src.utils.media_downloader import DownloadResult

        self.calls.append({"url": url, "target_dir": target_dir, **kwargs})
        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)
        if progress_callback:
            progress_callback(50)
        if self.fail:
            (target / f"{self.name}.part").write_bytes(b"partial")
            raise RuntimeError("yt-dlp завершился с кодом 1")
        (target / self.name).write_bytes(b"audio")
        return DownloadResult(files=[str(target / self.name)])


class _FakeYoutubeDL:
    """На случай прямого вызова yt-dlp: ведёт себя как оборванная загрузка, без сети."""

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def download(self, urls):
        template = self.opts["outtmpl"]
        Path(template.replace("%(title)s", "Song").replace("%(ext)s", "m4a.part")).write_bytes(b"partial")
        raise RuntimeError("network down")


def _run_download(task_id: str, url: str = "https://example.invalid/watch?v=1"):
    registry.register(task_id, "watch", 0, "alice")
    registry.tasks[task_id]["status"] = "downloading"
    asyncio.run(jobs.download_and_process(
        task_id, url, ["txt"], False, "pyannote", None,
        transcription_service.AsrSelection("auto", "v3_e2e_rnnt", "auto"),
        SubtitleOptions(),
    ))


def test_url_download_failure_leaves_no_partial_files(web_dirs, monkeypatch):
    upload_dir, _ = web_dirs
    downloader = _FakeDownloader(fail=True)
    monkeypatch.setattr(state, "media_downloader", downloader)
    monkeypatch.setattr(yt_dlp, "YoutubeDL", _FakeYoutubeDL)

    _run_download("dl1")

    assert registry.tasks["dl1"]["status"] == "failed"
    assert [p.name for p in upload_dir.iterdir()] == []


def test_url_download_is_capped_and_handed_to_processing(web_dirs, monkeypatch):
    upload_dir, _ = web_dirs
    downloader = _FakeDownloader()
    monkeypatch.setattr(state, "media_downloader", downloader)
    monkeypatch.setattr(yt_dlp, "YoutubeDL", _FakeYoutubeDL)
    processed = {}

    async def fake_process(task_id, file_path, filename, *args):
        processed.update(task_id=task_id, file_path=file_path, filename=filename, exists=file_path.exists())

    monkeypatch.setattr(jobs, "process_transcription", fake_process)

    _run_download("dl2")

    # Тот же лимит размера, что у загрузки файлом и у MCP
    assert downloader.calls[0]["max_filesize"] == state.max_file_size
    assert processed["filename"] == "Song title.m4a"
    assert processed["file_path"] == upload_dir / "dl2_Song title.m4a" and processed["exists"]
    # Временная папка загрузки убрана; в uploads — только файл задачи в обычном виде
    assert [p.name for p in upload_dir.iterdir()] == ["dl2_Song title.m4a"]
    assert registry.tasks["dl2"]["file_size"] == 5


def test_url_download_with_nothing_downloaded_fails_with_reason(web_dirs, monkeypatch):
    # yt-dlp молча пропускает файл больше max_filesize — пустой результат должен стать понятной ошибкой
    upload_dir, _ = web_dirs

    class _Empty(_FakeDownloader):
        def download(self, url, target_dir, progress_callback=None, **kwargs):
            from src.utils.media_downloader import DownloadResult

            self.calls.append(kwargs)
            Path(target_dir).mkdir(parents=True, exist_ok=True)
            return DownloadResult(files=[])

    monkeypatch.setattr(state, "media_downloader", _Empty())
    monkeypatch.setattr(yt_dlp, "YoutubeDL", _FakeYoutubeDL)

    _run_download("dl3")

    task = registry.tasks["dl3"]
    assert task["status"] == "failed"
    assert "лимит" in task["message"]
    assert list(upload_dir.iterdir()) == []


# ==================== SSE прогресса ====================


def test_progress_feed_starts_with_snapshot_without_history(web_dirs):
    registry.tasks["mine"] = {"task_id": "mine", "status": "completed", "progress": 100,
                                     "filename": "a.wav", "message": "ok", "user": "alice"}
    registry.tasks["theirs"] = {"task_id": "theirs", "status": "processing", "progress": 10,
                                       "filename": "b.wav", "message": "", "user": "bob"}
    registry.logs["mine"] = ["старая строка 1", "старая строка 2"]
    registry.logs["theirs"] = ["чужая"]
    feed = web_app.ProgressFeed("alice")

    first = feed.next_payload()
    # Снимок: состояние своих задач, без журнала — клиент не повторяет историю на каждом подключении
    assert first["snapshot"] is True
    assert set(first["tasks"]) == {"mine"}
    assert first["logs"] == {}

    assert feed.next_payload() is None  # ничего не изменилось

    registry.logs["mine"].append("новая строка")
    registry.tasks["mine"]["message"] = "перезапуск"
    delta = feed.next_payload()
    assert "snapshot" not in delta
    assert set(delta["tasks"]) == {"mine"}
    assert delta["logs"] == {"mine": ["новая строка"]}


def test_progress_feed_sends_snapshot_even_without_tasks(web_dirs):
    # Без задач первое сообщение всё равно уходит: иначе клиент счёл бы снимком первое настоящее событие
    feed = web_app.ProgressFeed("alice")
    assert feed.next_payload() == {"snapshot": True, "tasks": {}, "logs": {}}
    registry.register("fresh", "c.wav", 1, "alice")
    registry.log("fresh", "первая строка")
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
