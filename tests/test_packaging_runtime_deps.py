"""Регрессии полноты PyInstaller-сборок для runtime ML-зависимостей."""

import ast
from pathlib import Path

PACKAGING_DIR = Path("packaging")
COMMON_SPEC = PACKAGING_DIR / "_spec_common.py"
ACTIVE_SPECS = [
    PACKAGING_DIR / "gigaam_app.spec",
    PACKAGING_DIR / "gigaam_app_portable.spec",
    PACKAGING_DIR / "gigaam_app_mac.spec",
]


def test_common_runtime_contract_collects_asteroid_filterbanks():
    text = COMMON_SPEC.read_text(encoding="utf-8")

    assert '"asteroid_filterbanks"' in text
    assert "collect_all(pkg)" in text
    assert "raise RuntimeError" in text


def test_active_specs_collect_pyannote_submodules_without_importing_package():
    for spec in ACTIVE_SPECS:
        text = spec.read_text(encoding="utf-8")

        assert "collect_static_package" in text, spec
        assert "collect_static_package('pyannote.audio')" in text or (
            'package == "pyannote.audio"' in text
        ), spec


def test_all_specs_use_shared_runtime_dependency_contract():
    for spec in PACKAGING_DIR.glob("*.spec"):
        text = spec.read_text(encoding="utf-8")
        if "pyannote.audio" not in text:
            continue

        assert "collect_pure_runtime_deps" in text, spec
        assert "runtime_h" in text, spec


def test_all_specs_remain_valid_python_after_shared_contract_changes():
    for spec in PACKAGING_DIR.glob("*.spec"):
        ast.parse(spec.read_text(encoding="utf-8-sig"), filename=str(spec))
