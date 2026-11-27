"""Atomic, isolated Community-1 environment preparation."""

import json
import subprocess
import runpy
import shutil
import sys
from pathlib import Path

import pytest

from src.core.diarization import community_runtime
from src.core.model_preparation import PreparationCancelled, PreparationState


def test_runtime_install_publishes_only_completed_venv(tmp_path, monkeypatch):
    monkeypatch.setenv("GIGAAM_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("GIGAAM_COMMUNITY_PYTHON", raising=False)
    monkeypatch.setattr(community_runtime, "_bootstrap_python", lambda _cancel: "/test/python")
    calls = []

    class Installer:
        def __init__(self, command, **kwargs):
            assert not (tmp_path / "community-1" / "current.json").exists()
            assert "HF_TOKEN" not in kwargs["env"]
            calls.append((command, kwargs))
            python = community_runtime._venv_python(Path(command[-1]))
            python.parent.mkdir(parents=True)
            python.touch()

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(community_runtime.subprocess, "Popen", Installer)
    events = []
    result = community_runtime.ensure_runtime(lambda state, **kwargs: events.append(state), lambda: False)
    marker = json.loads((tmp_path / "community-1" / "current.json").read_text())

    assert result == community_runtime._venv_python(tmp_path / "community-1" / "versions" / marker["directory"])
    assert result.is_file()
    assert community_runtime.runtime_python() == result
    assert community_runtime.ensure_runtime(lambda *_args, **_kwargs: None, lambda: False) == result
    assert len(calls) == 1
    assert events == [PreparationState.DOWNLOADING]


def test_cancelled_install_terminates_child_and_leaves_no_published_venv(tmp_path, monkeypatch):
    monkeypatch.setenv("GIGAAM_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("GIGAAM_COMMUNITY_PYTHON", raising=False)
    monkeypatch.setattr(community_runtime, "_bootstrap_python", lambda _cancel: "/test/python")
    state = {"cancel": False, "terminated": False, "target": None}

    class Installer:
        def __init__(self, command, **_kwargs):
            state["target"] = Path(command[-1])

        def wait(self, timeout=None):
            state["cancel"] = True
            raise subprocess.TimeoutExpired("installer", timeout)

        def poll(self):
            return -15 if state["terminated"] else None

    def terminate(_process):
        state["terminated"] = True

    monkeypatch.setattr(community_runtime.subprocess, "Popen", Installer)
    monkeypatch.setattr(community_runtime, "_terminate_install", terminate)
    with pytest.raises(PreparationCancelled):
        community_runtime.ensure_runtime(lambda *_args, **_kwargs: None, lambda: state["cancel"])

    assert state["terminated"]
    assert not state["target"].exists()
    assert not (tmp_path / "community-1" / "current.json").exists()


def test_missing_override_never_replaces_user_environment(tmp_path, monkeypatch):
    override = tmp_path / "custom" / "python"
    monkeypatch.setenv("GIGAAM_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("GIGAAM_COMMUNITY_PYTHON", str(override))
    with pytest.raises(RuntimeError, match="GIGAAM_COMMUNITY_PYTHON"):
        community_runtime.ensure_runtime(lambda *_args, **_kwargs: None, lambda: False)
    assert not override.exists()
    assert not (tmp_path / "community-1" / "current.json").exists()


def test_missing_bootstrap_python_is_reported_without_creating_a_venv(tmp_path, monkeypatch):
    monkeypatch.setenv("GIGAAM_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("GIGAAM_COMMUNITY_PYTHON", raising=False)
    monkeypatch.setattr(community_runtime, "_bootstrap_python", lambda _cancel: None)
    with pytest.raises(RuntimeError, match="Python 3.10–3.13"):
        community_runtime.ensure_runtime(lambda *_args, **_kwargs: None, lambda: False)
    assert not (tmp_path / "community-1" / "current.json").exists()
    assert not (tmp_path / "community-1" / "versions").exists()

def test_intel_macos_does_not_attempt_unsupported_runtime_install(tmp_path, monkeypatch):
    monkeypatch.setenv("GIGAAM_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("GIGAAM_COMMUNITY_PYTHON", raising=False)
    monkeypatch.setattr(community_runtime.sys, "platform", "darwin")
    monkeypatch.setattr(community_runtime.platform, "machine", lambda: "x86_64")
    with pytest.raises(RuntimeError, match="Intel macOS"):
        community_runtime.ensure_runtime(lambda *_args, **_kwargs: None, lambda: False)
    assert not (tmp_path / "community-1").exists()



def test_collected_community_files_form_a_working_worker_layout(tmp_path):
    collector = runpy.run_path("packaging/_spec_common.py")["collect_community_runtime_files"]
    for source, destination in collector(Path.cwd()):
        target = tmp_path / destination
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target / Path(source).name)
    directory = tmp_path / "src/core/diarization"
    result = subprocess.run(
        [sys.executable, str(directory / "community_worker.py")],
        input='{"action":"invalid"}\n', text=True, capture_output=True, check=True,
    )
    assert json.loads(result.stdout)["event"] == "error"
    installer = subprocess.run(
        [sys.executable, str(directory / "install_community_runtime.py"), "--help"],
        text=True, capture_output=True, check=True,
    )
    assert "directory" in installer.stdout
    assert (directory / "requirements-community.txt").is_file()
