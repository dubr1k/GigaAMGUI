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


def test_default_requirements_pin_onnx_asr_without_conflicting_ort_packages():
    text = Path("requirements.txt").read_text(encoding="utf-8")
    assert "onnx-asr==0.12.0" in text
    assert "onnxruntime==1.23.2" in text
    assert "onnxruntime-gpu" not in text


def _requirement_lines(path: str) -> list[str]:
    text = Path(path).read_text(encoding="utf-8")
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_gpu_onnx_requirements_select_only_gpu_distribution():
    requirements = _requirement_lines("requirements-onnx-gpu.txt")

    assert "onnxruntime-gpu==1.23.2" in requirements
    assert not any(line.startswith("onnxruntime==") for line in requirements)


def test_gpu_onnx_requirements_warn_about_cpu_wheel_reinstall():
    """Пин gigaam на CPU-колесо возвращает CPU-провайдер молча — это должно быть в файле."""
    text = Path("requirements-onnx-gpu.txt").read_text(encoding="utf-8")

    assert "gigaam" in text
    assert "get_available_providers" in text


def test_docker_removes_cpu_ort_before_installing_gpu_distribution():
    """gigaam тянет onnxruntime==1.23.* сам, поэтому фильтра requirements мало."""
    text = Path("Dockerfile").read_text(encoding="utf-8")

    uninstall_at = text.find("pip uninstall -y onnxruntime")
    install_at = text.find("pip install --no-cache-dir onnxruntime-gpu")

    assert uninstall_at != -1
    assert install_at != -1
    assert uninstall_at < install_at


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


def test_active_specs_collect_onnx_asr_code_data_and_runtime_binaries():
    common = COMMON_SPEC.read_text(encoding="utf-8")
    assert "collect_onnx_runtime_deps" in common
    assert '("onnx_asr", "onnxruntime")' in common
    assert "collect_all(package)" in common

    for spec in ACTIVE_SPECS:
        text = spec.read_text(encoding="utf-8")
        assert "collect_onnx_runtime_deps" in text, spec
        assert "onnx_d" in text and "onnx_b" in text and "onnx_h" in text, spec


def test_docker_replaces_cpu_ort_with_gpu_distribution():
    text = Path("Dockerfile").read_text(encoding="utf-8")
    assert "onnxruntime" in text
    assert "onnxruntime-gpu==1.23.2" in text


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
