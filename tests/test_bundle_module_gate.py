"""Гейты сборки против «пустого» gigaam в бандле.

Каталог ``site-packages/gigaam/`` из одних конфликтных копий Syncthing
импортировался как namespace-пакет и затенял editable-установку: ``import
gigaam`` проходил, хук PyInstaller собирал пустышку, проверка каталогов в
верификаторе её пропускала, и локальные сборки Liquid уезжали без
PyTorch-модели. Тесты держат все три звена.
"""

from __future__ import annotations

import importlib.machinery
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "pyinstaller_hooks" / "hook-gigaam.py"


def _run_hook(monkeypatch, spec, collected=None):
    hooks = types.ModuleType("PyInstaller.utils.hooks")
    hooks.collect_all = lambda name: collected or ([], [], [])
    hooks.collect_submodules = lambda name: [f"{name}.model"]
    for name in ("PyInstaller", "PyInstaller.utils"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "PyInstaller.utils.hooks", hooks)
    monkeypatch.setattr("importlib.util.find_spec", lambda name: spec)
    namespace: dict = {"__name__": "hook-gigaam"}
    exec(compile(HOOK.read_text(encoding="utf-8"), str(HOOK), "exec"), namespace)
    return namespace


def test_hook_rejects_namespace_gigaam(monkeypatch, tmp_path):
    spec = importlib.machinery.ModuleSpec("gigaam", None, is_package=True)
    spec.submodule_search_locations = [str(tmp_path / "gigaam")]
    with pytest.raises(SystemExit, match="namespace"):
        _run_hook(monkeypatch, spec)


def test_hook_rejects_package_without_load_model(monkeypatch, tmp_path):
    init = tmp_path / "gigaam" / "__init__.py"
    init.parent.mkdir()
    init.write_text("VERSION = 1\n", encoding="utf-8")
    spec = importlib.machinery.ModuleSpec("gigaam", None, origin=str(init), is_package=True)
    with pytest.raises(SystemExit, match="load_model"):
        _run_hook(monkeypatch, spec)


def test_hook_collects_real_package_without_sync_conflicts(monkeypatch, tmp_path):
    init = tmp_path / "gigaam" / "__init__.py"
    init.parent.mkdir()
    init.write_text("def load_model(name):\n    return name\n", encoding="utf-8")
    spec = importlib.machinery.ModuleSpec("gigaam", None, origin=str(init), is_package=True)
    datas = [
        (str(tmp_path / "gigaam" / "config.yaml"), "gigaam"),
        (str(tmp_path / "gigaam" / "model.sync-conflict-20260906-162521-3LI4UIX.py"), "gigaam"),
    ]
    namespace = _run_hook(monkeypatch, spec, collected=(datas, [], []))
    assert namespace["datas"] == [datas[0]]
    assert "gigaam.model" in namespace["hiddenimports"]


def test_verifier_reports_modules_missing_from_the_archive(monkeypatch, tmp_path, capsys):
    from scripts import verify_macos_bundle as verifier

    monkeypatch.setattr(verifier, "frozen_modules", lambda exe: {"gigaam", "gigaam_mlx.model", "onnx_asr"})
    profile = verifier.PROFILES["arm64-mlx"]
    assert verifier.check_required_modules(tmp_path / "GigaAMWorker", profile.required_modules) == 1
    assert "gigaam.model" in capsys.readouterr().out


def test_verifier_accepts_complete_archive(monkeypatch, tmp_path):
    from scripts import verify_macos_bundle as verifier

    profile = verifier.PROFILES["arm64-mlx"]
    monkeypatch.setattr(verifier, "frozen_modules", lambda exe: set(profile.required_modules))
    assert verifier.check_required_modules(tmp_path / "GigaAMWorker", profile.required_modules) == 0


def test_verifier_fails_closed_when_archive_is_unreadable(monkeypatch, tmp_path):
    from scripts import verify_macos_bundle as verifier

    def broken(exe):
        raise ValueError("not a PyInstaller executable")

    monkeypatch.setattr(verifier, "frozen_modules", broken)
    assert verifier.check_required_modules(tmp_path / "x", ("gigaam.model",)) == 1


def test_arm64_profile_requires_the_pytorch_model_code():
    from scripts import verify_macos_bundle as verifier

    assert "gigaam.model" in verifier.PROFILES["arm64-mlx"].required_modules


def test_ci_checks_worker_archive_and_mac_preflight_imports_load_model():
    workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
    assert 'verify_macos_bundle.py --modules-only --profile arm64-mlx "$WORKER"' in workflow
    script = (ROOT / "packaging" / "build_exe_mac.sh").read_text(encoding="utf-8")
    assert "from gigaam import load_model" in script
