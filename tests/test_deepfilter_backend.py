from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import numpy as np

from src.utils.deepfilter_backend import (
    DeepFilterAsset,
    DeepFilterBinaryManager,
    DeepFilterNetBinaryBackend,
    _asset_for,
)
from tests.test_audio_preprocessing import _write_pcm16


def test_asset_selection_covers_supported_release_targets():
    assert _asset_for("linux", "x86_64").filename.endswith("linux-musl")
    assert _asset_for("linux", "arm64").filename.endswith("linux-gnu")
    assert _asset_for("darwin", "arm64").filename.endswith("apple-darwin")
    assert _asset_for("win32", "AMD64").filename.endswith("windows-msvc.exe")
    assert _asset_for("win32", "arm64") is None


def test_binary_manager_downloads_atomically_and_verifies_checksum(monkeypatch, tmp_path):
    payload = b"verified-deepfilter-binary"
    asset = DeepFilterAsset("test-binary", hashlib.sha256(payload).hexdigest())
    manager = DeepFilterBinaryManager(cache_dir=tmp_path, asset=asset)
    downloads: list[str] = []

    def fake_download(url: str, target: Path):
        downloads.append(url)
        target.write_bytes(payload)

    monkeypatch.setattr(manager, "_download", fake_download)

    first = Path(manager.ensure())
    second = Path(manager.ensure())

    assert first == second
    assert first.read_bytes() == payload
    assert len(downloads) == 1
    assert not list(tmp_path.glob("*.download"))


def test_binary_manager_reports_download_bytes_and_honors_cancellation(monkeypatch, tmp_path):
    payload = b"binary"
    asset = DeepFilterAsset("deep-filter", hashlib.sha256(payload).hexdigest())
    manager = DeepFilterBinaryManager(cache_dir=tmp_path, asset=asset)
    progress = []

    def fake_download(url, target, *, progress_callback=None, cancel_check=None):
        assert not cancel_check()
        target.write_bytes(payload)
        progress_callback(len(payload), len(payload))

    monkeypatch.setattr(DeepFilterBinaryManager, "_download", staticmethod(fake_download))

    path = manager.ensure(
        progress_callback=lambda completed, total: progress.append((completed, total)),
        cancel_check=lambda: False,
    )

    assert path == str(tmp_path / asset.filename)
    assert progress == [(len(payload), len(payload))]


def test_binary_manager_replaces_symlink_in_executable_cache(monkeypatch, tmp_path):
    payload = b"verified-deepfilter-binary"
    asset = DeepFilterAsset("test-binary", hashlib.sha256(payload).hexdigest())
    outside = tmp_path / "outside"
    outside.write_bytes(payload)
    target = tmp_path / asset.filename
    try:
        target.symlink_to(outside)
    except OSError:
        return  # Windows CI can deny unprivileged symlink creation.
    manager = DeepFilterBinaryManager(cache_dir=tmp_path, asset=asset)
    monkeypatch.setattr(manager, "_download", lambda _url, path: path.write_bytes(payload))

    installed = Path(manager.ensure())

    assert installed == target
    assert not installed.is_symlink()
    assert installed.read_bytes() == payload
    assert outside.read_bytes() == payload


def test_binary_manager_rejects_checksum_mismatch(monkeypatch, tmp_path):
    asset = DeepFilterAsset("test-binary", hashlib.sha256(b"expected").hexdigest())
    manager = DeepFilterBinaryManager(cache_dir=tmp_path, asset=asset)
    monkeypatch.setattr(manager, "_download", lambda _url, target: target.write_bytes(b"tampered"))

    try:
        manager.ensure()
    except RuntimeError as exc:
        assert "checksum" in str(exc).lower()
    else:
        raise AssertionError("checksum mismatch must fail closed")

    assert not (tmp_path / asset.filename).exists()
    assert not list(tmp_path.glob("*.download"))


def test_binary_manager_honors_executable_cache_override(monkeypatch, tmp_path):
    monkeypatch.setenv("GIGAAM_DEEPFILTER_DIR", str(tmp_path))
    manager = DeepFilterBinaryManager(asset=DeepFilterAsset("binary", "0" * 64))

    assert manager.cache_dir == tmp_path


def test_binary_manager_rejects_untrusted_download_url(tmp_path):
    target = tmp_path / "download"

    try:
        DeepFilterBinaryManager._download("http://example.com/binary", target)
    except RuntimeError as exc:
        assert "trusted GitHub HTTPS" in str(exc)
    else:
        raise AssertionError("untrusted download URL must fail closed")

    assert not target.exists()


class _FakeManager:
    def supported(self):
        return True

    def ensure(self):
        return "/opt/deep-filter"


def test_binary_backend_cleans_workspace_when_nested_directory_creation_fails(
    monkeypatch,
    tmp_path,
):
    canonical = tmp_path / "canonical.wav"
    _write_pcm16(canonical, np.zeros(16000, dtype=np.float32))
    backend = DeepFilterNetBinaryBackend(manager=_FakeManager(), logger=lambda _message: None)
    original_mkdir = Path.mkdir
    calls = 0

    def fail_second_nested_mkdir(path, *args, **kwargs):
        nonlocal calls
        if path.name in {"input", "enhanced"}:
            calls += 1
            if calls == 2:
                raise OSError("simulated filesystem failure")
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_second_nested_mkdir)

    assert backend.process(str(canonical), str(tmp_path)) is None
    assert not list(tmp_path.glob("gigaam_deepfilter_*"))


def test_binary_backend_timeout_falls_back_and_cleans_workspace(monkeypatch, tmp_path):
    canonical = tmp_path / "canonical.wav"
    _write_pcm16(canonical, np.zeros(16000, dtype=np.float32))
    backend = DeepFilterNetBinaryBackend(manager=_FakeManager(), logger=lambda _message: None)
    monkeypatch.setattr(
        backend,
        "_run",
        lambda command, *, timeout: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(command, timeout)
        ),
    )
    monkeypatch.setattr("src.utils.deepfilter_backend._find_ffmpeg", lambda: "ffmpeg")

    assert backend.process(str(canonical), str(tmp_path)) is None
    assert not list(tmp_path.glob("gigaam_deepfilter_*"))
    assert not list(tmp_path.glob("temp_deepfiltered_*.wav"))


def test_binary_backend_compensates_timeline_and_cleans_workspace(monkeypatch, tmp_path):
    canonical = tmp_path / "canonical.wav"
    _write_pcm16(canonical, np.zeros(16000, dtype=np.float32))
    backend = DeepFilterNetBinaryBackend(
        manager=_FakeManager(),
        logger=lambda _message: None,
    )
    commands: list[list[str]] = []

    def fake_run(command: list[str], *, timeout: float):
        commands.append(command)
        if command[0] == "/opt/deep-filter":
            output_dir = Path(command[command.index("--output-dir") + 1])
            _write_pcm16(output_dir / "audio.wav", np.zeros(48000 - 1440, dtype=np.float32), 48000)
        elif "48000" in command:
            _write_pcm16(Path(command[-1]), np.zeros(48000, dtype=np.float32), 48000)
        else:
            _write_pcm16(Path(command[-1]), np.zeros(16000, dtype=np.float32), 16000)
        return type("Result", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(backend, "_run", fake_run)
    monkeypatch.setattr("src.utils.deepfilter_backend._find_ffmpeg", lambda: "ffmpeg")

    output = backend.process(str(canonical), str(tmp_path))

    assert output is not None
    assert Path(output).exists()
    assert len(commands) == 3
    assert commands[1][0] == "/opt/deep-filter"
    assert "-D" in commands[1]
    assert "apad,atrim=duration=1.000000000" in commands[2]
    assert not list(tmp_path.glob("gigaam_deepfilter_*"))
