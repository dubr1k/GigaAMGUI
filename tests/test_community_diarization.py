"""Community-1 selection, isolated worker protocol, and opt-in real-model smoke."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.core.diarization import community_backend
from src.core.diarization.base import DiarizationBackend
from src.core.diarization.factory import create_diarization_backend
from src.core.diarization.names import normalize_diarization_backend
from src.core.model_preparation import PreparationState


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch, request):
    if request.node.name != "test_community_real_model_offline_smoke":
        for name in ("GIGAAM_COMMUNITY_MODEL_DIR", "GIGAAM_COMMUNITY_PYTHON", "HF_TOKEN"):
            monkeypatch.delenv(name, raising=False)


def test_community_factory_is_distinct_from_legacy_and_onnx_model_dir(tmp_path):
    snapshot = tmp_path / "community"
    manager = create_diarization_backend(
        "pyannote-community",
        hf_token="test-token",
        device="cpu",
        model_dir=str(tmp_path / "onnx"),
        community_model_dir=str(snapshot),
    )
    assert normalize_diarization_backend("pyannote-community") == "pyannote-community"
    assert normalize_diarization_backend(None) == "pyannote"
    assert manager.backend == "pyannote-community"
    assert isinstance(manager, DiarizationBackend)
    assert manager.model_dir == snapshot
    assert manager.hf_token == "test-token"


def test_community_missing_runtime_explains_how_to_install(tmp_path, monkeypatch):
    monkeypatch.setenv("GIGAAM_COMMUNITY_PYTHON", str(tmp_path / "missing-python"))
    monkeypatch.setenv("GIGAAM_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(community_backend, "hf_repo_is_cached", lambda _repo: False)
    manager = create_diarization_backend("pyannote-community", hf_token="test-token", device="cpu")

    with pytest.raises(RuntimeError) as error:
        manager.prepare()

    assert "install_community_runtime.py" in str(error.value)
    assert "GIGAAM_COMMUNITY_PYTHON" in str(error.value)
    assert "test-token" not in str(error.value)


def test_community_process_passes_token_via_stdin_and_reuses_worker(tmp_path, monkeypatch):
    script = tmp_path / "protocol_worker.py"
    script.write_text(
        "import json, os, sys\n"
        "for line in sys.stdin:\n"
        "    request = json.loads(line)\n"
        "    if request['action'] == 'prepare':\n"
        "        assert request['token'] == 'secret-for-test'\n"
        "        assert not os.environ.get('HF_TOKEN')\n"
        "        print(json.dumps({'event': 'result', 'value': None}), flush=True)\n"
        "    else:\n"
        "        assert request['options'] == {'num_speakers': 2}\n"
        "        print(json.dumps({'event': 'progress'}), flush=True)\n"
        "        print(json.dumps({'event': 'result', 'value': [[4, 5, 'B'], [0, 1, 'A'], [2, 3, 'A']]}), flush=True)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HF_TOKEN", "secret-for-test")
    monkeypatch.setenv("GIGAAM_COMMUNITY_PYTHON", sys.executable)
    monkeypatch.setattr(community_backend, "_WORKER", script)
    monkeypatch.setattr(community_backend, "hf_repo_is_cached", lambda _repo: False)
    real_popen = subprocess.Popen
    processes = []

    def start(command, **kwargs):
        assert "secret-for-test" not in " ".join(map(str, command))
        assert "HF_TOKEN" not in kwargs["env"]
        process = real_popen(command, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(community_backend.subprocess, "Popen", start)
    manager = create_diarization_backend("pyannote-community", device="cpu")
    events = []
    progress = []
    try:
        manager.prepare(report=lambda state, **kwargs: events.append((state, kwargs)))
        first = manager.diarize("one.wav", num_speakers=2, progress_callback=lambda *args: progress.append(args))
        second = manager.diarize("two.wav", num_speakers=2)
        assert [(s.start, s.end, s.speaker) for s in first] == [
            (0.0, 1.0, "Спикер №1"),
            (2.0, 3.0, "Спикер №1"),
            (4.0, 5.0, "Спикер №2"),
        ]
        assert second == first
        assert progress == [(None, None, None)]
        assert [event[0] for event in events] == [PreparationState.DOWNLOADING, PreparationState.LOADING]
        assert len(processes) == 1
    finally:
        manager.unload()
    assert processes[0].poll() is not None


def test_offline_snapshot_needs_no_token_and_discard_stops_worker(tmp_path, monkeypatch):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "config.yaml").write_text("pipeline: {}", encoding="utf-8")
    script = tmp_path / "offline_worker.py"
    script.write_text(
        "import json, os, sys\n"
        "request = json.loads(sys.stdin.readline())\n"
        "assert request['token'] is None\n"
        "assert os.path.isfile(os.path.join(request['model_dir'], 'config.yaml'))\n"
        "print(json.dumps({'event': 'result', 'value': None}), flush=True)\n"
        "sys.stdin.read()\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setenv("GIGAAM_COMMUNITY_MODEL_DIR", str(snapshot))
    monkeypatch.setenv("GIGAAM_COMMUNITY_PYTHON", sys.executable)
    monkeypatch.setattr(community_backend, "_WORKER", script)
    manager = create_diarization_backend("pyannote-community", device="cpu")
    manager.prepare()
    process = manager._process
    del manager
    assert process.poll() is not None


def test_cancel_during_model_loading_stops_worker(tmp_path, monkeypatch):
    from src.core.model_preparation import PreparationCancelled

    script = tmp_path / "slow_worker.py"
    script.write_text(
        "import sys, time\n"
        "sys.stdin.readline()\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GIGAAM_COMMUNITY_PYTHON", sys.executable)
    monkeypatch.setattr(community_backend, "_WORKER", script)
    monkeypatch.setattr(community_backend, "hf_repo_is_cached", lambda _repo: True)
    processes = []
    real_popen = subprocess.Popen

    def start(command, **kwargs):
        process = real_popen(command, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(community_backend.subprocess, "Popen", start)
    checks = iter((False, False, True))
    manager = create_diarization_backend("pyannote-community", device="cpu")
    with pytest.raises(PreparationCancelled):
        manager.prepare(cancel_check=lambda: next(checks, True))
    assert processes[0].poll() is not None


def test_community_worker_errors_redact_token_and_explain_missing_runtime(tmp_path, monkeypatch):
    script = tmp_path / "failure_worker.py"
    script.write_text(
        "import json, sys\n"
        "request = json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'event': 'error', 'kind': 'runtime', 'message': 'failed: ' + request['token']}), flush=True)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(community_backend, "_WORKER", script)
    monkeypatch.setattr(community_backend, "hf_repo_is_cached", lambda _repo: True)
    monkeypatch.setenv("GIGAAM_COMMUNITY_PYTHON", sys.executable)
    manager = create_diarization_backend("pyannote-community", hf_token="sensitive-test-token", device="cpu")

    with pytest.raises(RuntimeError) as error:
        manager.prepare()

    assert "sensitive-test-token" not in str(error.value)
    assert "[REDACTED]" in str(error.value)
    assert "install_community_runtime.py" in str(error.value)


@pytest.mark.skipif(
    not (os.getenv("GIGAAM_COMMUNITY_PYTHON") and os.getenv("GIGAAM_COMMUNITY_MODEL_DIR")),
    reason="requires an installed Community-1 runtime and an authorized local snapshot",
)
def test_community_real_model_offline_smoke(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    manager = create_diarization_backend("pyannote-community", device="cpu")
    try:
        manager.prepare()
        segments = manager.diarize(Path("tests/fixtures/liquid_smoke.wav"))
    finally:
        manager.unload()
    assert segments
    assert all(0 <= segment.start < segment.end for segment in segments)
