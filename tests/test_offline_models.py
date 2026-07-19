"""Офлайн-вариант релиза: поиск моделей рядом со сборкой и контракт CI."""

import os
import sys
from pathlib import Path

import pytest

from src.utils.runtime_manager import bundled_hf_cache_dir

WORKFLOW_PATH = Path(".github/workflows/build.yml")
BUILDER_PATH = Path("scripts/build_offline_models.py")


def _make_cache(root, *, repo: str = "models--istupakov--gigaam-v3-onnx"):
    cache = root / "models" / "hf"
    (cache / "hub" / repo / "snapshots" / "abc").mkdir(parents=True)
    (cache / "hub" / repo / "snapshots" / "abc" / "config.json").write_text("{}")
    return cache


def test_no_bundled_cache_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "executable", str(tmp_path / "GigaAMTranscriber"))

    assert bundled_hf_cache_dir(frozen=True) is None


def test_cache_next_to_executable_is_found(tmp_path, monkeypatch):
    cache = _make_cache(tmp_path)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "GigaAMTranscriber"))

    assert bundled_hf_cache_dir(frozen=True) == cache


def test_cache_next_to_macos_app_bundle_is_found(tmp_path, monkeypatch):
    cache = _make_cache(tmp_path)
    executable = tmp_path / "GigaAMTranscriber.app" / "Contents" / "MacOS" / "GigaAMTranscriber"
    executable.parent.mkdir(parents=True)
    monkeypatch.setattr(sys, "executable", str(executable))

    assert bundled_hf_cache_dir(frozen=True) == cache


def test_cache_inside_macos_resources_is_found(tmp_path, monkeypatch):
    contents = tmp_path / "GigaAMTranscriber.app" / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    cache = _make_cache(contents / "Resources")
    monkeypatch.setattr(sys, "executable", str(contents / "MacOS" / "GigaAMTranscriber"))

    assert bundled_hf_cache_dir(frozen=True) == cache


def test_directory_without_hub_is_ignored(tmp_path, monkeypatch):
    """Пустая папка models/hf не должна перехватывать кэш у рабочего каталога."""
    (tmp_path / "models" / "hf").mkdir(parents=True)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "GigaAMTranscriber"))

    assert bundled_hf_cache_dir(frozen=True) is None


def test_unfrozen_run_looks_at_repository_root(tmp_path, monkeypatch):
    """В обычном запуске из исходников моделей рядом с python быть не должно."""
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))
    _make_cache(tmp_path)

    assert bundled_hf_cache_dir(frozen=False) is None


@pytest.mark.parametrize("repo", ["models--istupakov--silero-vad-onnx", "models--x--y"])
def test_any_cached_repository_activates_the_bundle(tmp_path, monkeypatch, repo):
    cache = _make_cache(tmp_path, repo=repo)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "GigaAMTranscriber"))

    assert bundled_hf_cache_dir(frozen=True) == cache


def test_offline_builder_covers_the_whole_onnx_chain():
    """Офлайн-набор бессмыслен, если хоть одно звено цепочки полезет в сеть."""
    text = BUILDER_PATH.read_text(encoding="utf-8")

    for repo in (
        "models--istupakov--gigaam-v3-onnx",
        "models--istupakov--silero-vad-onnx",
        "models--onnx-community--pyannote-segmentation-3.0",
        "models--wespeaker--wespeaker-voxceleb-resnet34",
    ):
        assert repo in text


def test_offline_builder_skips_blobs():
    """Копирование вместе с blobs удваивает размер: 1.7 ГБ вместо 884 МБ."""
    text = BUILDER_PATH.read_text(encoding="utf-8")

    assert '("refs", "snapshots")' in text
    assert "symlinks=False" in text


def test_ci_publishes_offline_variant_for_every_platform():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")

    for asset in (
        "GigaAMTranscriber-windows-x64-offline.zip",
        "GigaAMTranscriber-macos-arm64-offline.zip",
        "GigaAMTranscriber-linux-x64-offline.zip",
    ):
        assert asset in text

    assert "scripts/build_offline_models.py --output offline/models/hf" in text
    assert "Attach offline bundle to release" in text
    assert "Attach offline full app to release" in text


def test_ci_proves_offline_bundle_needs_no_network():
    """Гейт обязателен: без него «офлайн»-сборка молча ушла бы качать модели."""
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    entrypoint = Path("app.py").read_text(encoding="utf-8")

    assert "--offline-models-smoke" in entrypoint
    assert workflow.count("--offline-models-smoke") >= 2
    assert 'HF_HUB_OFFLINE: "1"' in workflow
    assert "HF_HUB_OFFLINE=1" in workflow


def test_offline_smoke_refuses_a_build_without_bundled_models():
    entrypoint = Path("app.py").read_text(encoding="utf-8")

    assert 'raise RuntimeError("Папка моделей рядом со сборкой не найдена")' in entrypoint
    assert "Офлайн-сборка должна умолчанием выбирать onnx" in entrypoint


def test_ci_gates_offline_bundle_size():
    """2 ГБ — жёсткий предел ассета GitHub Release, за ним публикация падает."""
    text = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert text.count("MAX_RELEASE_ASSET_BYTES") >= 4
    assert "Офлайн-архив слишком велик для GitHub Release" in text


def test_config_points_huggingface_at_the_bundled_cache(tmp_path, monkeypatch):
    """Ради этого всё и делается: офлайн-сборка не должна лезть в сеть."""
    import src.config as config

    cache = _make_cache(tmp_path)
    monkeypatch.delenv("HF_HOME", raising=False)
    monkeypatch.setattr(
        "src.utils.runtime_manager.bundled_hf_cache_dir",
        lambda frozen=None: cache,
    )

    config._setup_huggingface_cache()

    assert Path(os.environ["HF_HOME"]) == cache


def test_offline_bundle_switches_default_backends_to_onnx():
    """auto выбрал бы MLX или PyTorch, которых в офлайн-наборе нет."""
    text = Path("src/config.py").read_text(encoding="utf-8")

    assert '_DEFAULT_ASR_BACKEND = "onnx" if BUNDLED_MODELS_DIR else "auto"' in text
    assert 'os.getenv("ASR_BACKEND", _DEFAULT_ASR_BACKEND)' in text
    assert '"onnx" if BUNDLED_MODELS_DIR else "pyannote"' in text


def test_explicit_huggingface_home_still_wins(tmp_path, monkeypatch):
    import src.config as config

    monkeypatch.setenv("HF_HOME", str(tmp_path / "chosen"))
    monkeypatch.setattr(
        "src.utils.runtime_manager.bundled_hf_cache_dir",
        lambda frozen=None: _make_cache(tmp_path),
    )

    config._setup_huggingface_cache()

    assert os.environ["HF_HOME"] == str(tmp_path / "chosen")
