"""Проверки конфигурации сборки macOS + MLX."""

from pathlib import Path

SPEC_PATH = Path("gigaam_app_mac.spec")
REQUIREMENTS_PATH = Path("requirements-macos-mlx.txt")
HOOK_PATH = Path("pyinstaller_hooks/hook-gigaam_mlx.py")


def test_macos_mlx_requirements_pinned_to_known_commit():
    text = REQUIREMENTS_PATH.read_text(encoding="utf-8")
    assert "20276ddd6173d636b37c6c6e13b4ee8f7b94d1ac" in text
    assert "gigaam-mlx" in text


def test_spec_includes_mlx_packages():
    text = SPEC_PATH.read_text(encoding="utf-8")
    assert "\"mlx\"" in text
    assert "\"gigaam_mlx\"" in text
    assert "CFBundleShortVersionString" in text


def test_hook_exists_and_collects_gigaam_mlx():
    text = HOOK_PATH.read_text(encoding="utf-8")
    assert "collect_all(\"gigaam_mlx\")" in text
    assert "sentencepiece" in text


def test_build_script_prefers_active_conda_environment():
    text = Path("build_exe_mac.sh").read_text(encoding="utf-8")
    assert "CONDA_PREFIX" in text
    assert 'PYTHON="$CONDA_PREFIX/bin/python"' in text


def test_build_script_calls_verifier_if_bundle_present():
    text = Path("build_exe_mac.sh").read_text(encoding="utf-8")
    verifier = Path("scripts/verify_macos_bundle.py").read_text(encoding="utf-8")
    assert "scripts/verify_macos_bundle.py dist/GigaAMTranscriber.app" in text
    assert "--asr-runtime-smoke" in verifier
    assert "--upgrade" not in text  # версия pyinstaller фиксируется вне команды сборки
