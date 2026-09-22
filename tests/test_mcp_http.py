"""MCP transports: the key guard on /mcp (api.py and the web panel) and the `python -m src.mcp_server` entry point.

The model is a fake; Streamable HTTP is exercised through TestClient with raw JSON-RPC.
"""
import importlib
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from fastapi.testclient import TestClient  # noqa: E402

from src.services.api_keys import KeyStore  # noqa: E402
from tests.test_api_openai import VALID_KEY, _FakeModelLoader  # noqa: E402

api = importlib.import_module("api")

ROOT = Path(__file__).resolve().parent.parent
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
BEARER = {"Authorization": f"Bearer {VALID_KEY}"}
INITIALIZE = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
               "clientInfo": {"name": "pytest", "version": "0"}},
}
TOOLS_LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
TEST_LOGGER = logging.getLogger("test_mcp_http")  # setup_logger() would write under the real config dir


def _rpc_result(response) -> dict:
    """Streamable HTTP answers either with JSON or with an SSE stream (`data: {...}`)."""
    assert response.status_code == 200, response.text
    if response.headers["content-type"].startswith("application/json"):
        return response.json()["result"]
    for line in response.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:"):])["result"]
    raise AssertionError(f"no SSE data in {response.text!r}")


# ==================== api.py ====================

@pytest.fixture
def api_client(monkeypatch):
    monkeypatch.setattr(api, "ModelLoader", _FakeModelLoader)
    monkeypatch.setattr(api, "HF_TOKEN", "hf_dummytoken")
    monkeypatch.setattr(api, "VALID_API_KEY_HASHES", {api._hash_key(VALID_KEY)})
    monkeypatch.setattr(api, "load_api_keys", lambda: None)
    monkeypatch.setattr(api.limiter, "enabled", False)
    with TestClient(api.app) as c:
        yield c


def test_api_mcp_without_key_is_401_envelope(api_client):
    r = api_client.post("/mcp", headers=MCP_HEADERS, json=INITIALIZE)
    assert r.status_code == 401
    assert r.json() == {"error": {"message": "Missing API key. Send 'Authorization: Bearer <key>' or 'X-API-Key: <key>'.",
                                  "type": "authentication_error", "param": None, "code": "invalid_api_key"}}


def test_api_mcp_wrong_key_is_401_envelope(api_client):
    r = api_client.post("/mcp", headers={**MCP_HEADERS, "Authorization": "Bearer gam_nope"}, json=INITIALIZE)
    assert r.status_code == 401
    assert r.json()["error"] == {"message": "Incorrect API key provided.", "type": "authentication_error",
                                 "param": None, "code": "invalid_api_key"}


def test_api_mcp_options_passes_the_guard(api_client):
    # CORS preflight carries no Authorization header; the guard must not turn it into a 401
    assert api_client.options("/mcp").status_code != 401


def test_api_mcp_initialize_with_bearer(api_client):
    result = _rpc_result(api_client.post("/mcp", headers={**MCP_HEADERS, **BEARER}, json=INITIALIZE))
    assert result["serverInfo"]["name"] == "GigaAM"
    assert result["serverInfo"]["version"] == api.__version__


def test_api_mcp_tools_list_with_x_api_key(api_client):
    result = _rpc_result(api_client.post("/mcp", headers={**MCP_HEADERS, "X-API-Key": VALID_KEY}, json=TOOLS_LIST))
    assert "transcribe" in {tool["name"] for tool in result["tools"]}


def test_api_mcp_before_lifespan_is_503_envelope(monkeypatch):
    monkeypatch.setattr(api, "VALID_API_KEY_HASHES", {api._hash_key(VALID_KEY)})
    # No `with`: the lifespan never ran, so the backend was never built
    r = TestClient(api.app).post("/mcp", headers={**MCP_HEADERS, **BEARER}, json=INITIALIZE)
    assert r.status_code == 503
    assert r.json()["error"]["type"] == "server_error"


def test_api_mcp_survives_two_lifespans(monkeypatch):
    # TestClient enters the lifespan once per `with`; the SDK session manager can only run once per instance
    monkeypatch.setattr(api, "ModelLoader", _FakeModelLoader)
    monkeypatch.setattr(api, "VALID_API_KEY_HASHES", {api._hash_key(VALID_KEY)})
    monkeypatch.setattr(api, "load_api_keys", lambda: None)
    monkeypatch.setattr(api.limiter, "enabled", False)
    for _ in range(2):
        with TestClient(api.app) as c:
            assert _rpc_result(c.post("/mcp", headers={**MCP_HEADERS, **BEARER}, json=INITIALIZE))["serverInfo"]


def test_api_upload_guard_does_not_cover_mcp(api_client):
    # /v1/audio/ answers 401 from _UploadGuard; /mcp must answer from _KeyGuard with the same envelope
    r = api_client.post("/mcp", headers=MCP_HEADERS, json=INITIALIZE)
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_api_key"


# ==================== web/web_app.py ====================

os.environ.setdefault("WEB_SECRET", "x" * 32)
os.environ.setdefault("WEB_USERNAME", "test-user")
os.environ.setdefault("WEB_PASSWORD", "test-password")
web_app = importlib.import_module("web.web_app")

WEB_KEY = "gam_web_test"


class _WebFakeLoader(_FakeModelLoader):
    device = "cpu"  # the web lifespan prints the device


@pytest.fixture
def web_dirs(tmp_path, monkeypatch):
    for name in ("uploads", "results"):
        (tmp_path / name).mkdir()
    monkeypatch.setattr(web_app, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(web_app, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(web_app, "TASKS_INDEX_PATH", tmp_path / "results" / ".tasks_index.json")
    monkeypatch.setattr(web_app, "DELETED_TASKS_PATH", tmp_path / "results" / ".deleted_tasks.json")
    monkeypatch.setattr(web_app, "API_KEYS_FILE", tmp_path / ".api_keys")
    monkeypatch.setattr(web_app, "ModelLoader", _WebFakeLoader)
    monkeypatch.setattr(web_app, "HF_TOKEN", "")
    web_app.tasks_storage.clear()
    yield tmp_path
    web_app.tasks_storage.clear()


@pytest.fixture
def web_client(web_dirs):
    store = KeyStore(web_dirs / ".api_keys")
    store.add(WEB_KEY)
    store.save()
    with TestClient(web_app.app) as c:
        yield c


def test_web_mcp_without_key_is_401_envelope(web_client):
    r = web_client.post("/mcp", headers=MCP_HEADERS, json=INITIALIZE)
    assert r.status_code == 401
    assert r.json()["error"] == {"message": "Missing API key. Send 'Authorization: Bearer <key>' or 'X-API-Key: <key>'.",
                                 "type": "authentication_error", "param": None, "code": "invalid_api_key"}


def test_web_mcp_initialize_and_tools_with_key(web_client):
    headers = {**MCP_HEADERS, "Authorization": f"Bearer {WEB_KEY}"}
    assert _rpc_result(web_client.post("/mcp", headers=headers, json=INITIALIZE))["serverInfo"]["name"] == "GigaAM"
    tools = _rpc_result(web_client.post("/mcp", headers=headers, json=TOOLS_LIST))["tools"]
    assert "transcribe" in {tool["name"] for tool in tools}


def test_web_session_auth_untouched_by_mcp(web_client):
    # The web panel keeps its own login; /mcp is the only route behind the key guard
    assert web_client.get("/api/tasks").status_code == 401
    assert web_client.get("/health").status_code == 200


def test_web_first_start_creates_key_file(web_dirs, capsys):
    with TestClient(web_app.app):
        pass
    key_file = web_dirs / ".api_keys"
    assert key_file.exists()
    out = capsys.readouterr().out
    key = next(line.strip() for line in out.splitlines() if line.strip().startswith("gam_"))
    assert KeyStore(key_file).load().verify(key)


# ==================== src/mcp_server.py ====================

mcp_entry = importlib.import_module("src.mcp_server")


def test_entry_point_help_exits_zero():
    proc = subprocess.run([sys.executable, "-m", "src.mcp_server", "--help"], cwd=ROOT,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    for flag in ("--http", "--host", "--port", "--config-dir"):
        assert flag in proc.stdout


def test_parse_args_defaults_to_stdio():
    args = mcp_entry.parse_args([])
    assert args.http is False and args.host == "127.0.0.1" and args.port == 8765 and args.config_dir is None
    args = mcp_entry.parse_args(["--http", "--host", "0.0.0.0", "--port", "9000", "--config-dir", "/tmp/cfg"])
    assert args.http and args.host == "0.0.0.0" and args.port == 9000 and args.config_dir == Path("/tmp/cfg")


def test_build_stdio_backend_loads_model_and_allows_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_entry, "ModelLoader", _FakeModelLoader)
    backend = mcp_entry.build_stdio_backend(config_dir=tmp_path, logger=TEST_LOGGER)
    assert isinstance(backend.model_loader, _FakeModelLoader)
    assert backend.http_mode is False and backend.allow_paths is True
    assert backend.llm_config_dir == tmp_path
    assert backend.semaphore is not None and backend.max_concurrent == mcp_entry.MAX_CONCURRENT_TASKS


def test_build_stdio_backend_fails_loudly_when_model_does_not_load(monkeypatch):
    class _Broken(_FakeModelLoader):
        def load_model(self, logger=None):
            return False

    monkeypatch.setattr(mcp_entry, "ModelLoader", _Broken)
    with pytest.raises(RuntimeError):
        mcp_entry.build_stdio_backend(logger=TEST_LOGGER)


def test_stdout_is_diverted_to_stderr_while_loading():
    # A real process: pytest's capture replaces sys.stdout with a non-fd file, which would hide the point
    code = (
        "import os\n"
        "from src.mcp_server import stdout_to_stderr\n"
        "with stdout_to_stderr():\n"
        "    print('model loading noise')\n"
        "    os.write(1, b'c-level noise\\n')\n"
        "print('wire')\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "wire\n"
    assert "model loading noise" in proc.stderr and "c-level noise" in proc.stderr


def test_http_app_serves_mcp_with_key(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_entry, "ModelLoader", _FakeModelLoader)
    monkeypatch.setattr(mcp_entry, "API_KEYS_FILE", tmp_path / ".api_keys")
    monkeypatch.setenv("GIGAAM_MCP_ALLOW_PATHS", "0")
    store = KeyStore(tmp_path / ".api_keys")
    store.add(WEB_KEY)
    store.save()
    with TestClient(mcp_entry.build_http_app(logger=TEST_LOGGER)) as c:
        assert c.post("/mcp", headers=MCP_HEADERS, json=INITIALIZE).status_code == 401
        headers = {**MCP_HEADERS, "Authorization": f"Bearer {WEB_KEY}"}
        assert _rpc_result(c.post("/mcp", headers=headers, json=INITIALIZE))["serverInfo"]["name"] == "GigaAM"
