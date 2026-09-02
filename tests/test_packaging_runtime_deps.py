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


def test_portable_ci_replaces_cpu_ort_with_gpu_distribution_on_windows_linux():
    text = Path(".github/workflows/build.yml").read_text(encoding="utf-8")

    assert "Install GPU ONNX Runtime (Windows/Linux)" in text
    assert "runner.os != 'macOS'" in text
    uninstall_at = text.find("pip uninstall -y onnxruntime onnxruntime-gpu")
    install_at = text.find("pip install --no-cache-dir -r requirements-onnx-gpu.txt")
    verify_at = text.find("onnxruntime.preload_dlls")
    assert -1 not in {uninstall_at, install_at, verify_at}
    assert uninstall_at < install_at < verify_at


def test_portable_ci_keeps_cpu_ort_on_macos():
    text = Path(".github/workflows/build.yml").read_text(encoding="utf-8")

    assert "Verify macOS CoreML ONNX Runtime" in text
    assert "CoreMLExecutionProvider" in text


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


def test_docker_installs_pinned_torch_once_before_dependency_resolution():
    """Раньше torch приезжал дважды: как зависимость и принудительной переустановкой."""
    text = Path("Dockerfile").read_text(encoding="utf-8")

    torch_at = text.find("torch==2.6.0 torchaudio==2.6.0 torchvision==0.21.0")
    gigaam_at = text.find("git clone https://github.com/salute-developers/GigaAM.git")
    requirements_at = text.find("pip install --no-cache-dir -r /tmp/req.txt")

    assert torch_at != -1
    assert gigaam_at != -1
    assert requirements_at != -1
    assert torch_at < gigaam_at
    assert torch_at < requirements_at
    assert text.count("torch==2.6.0 torchaudio==2.6.0 torchvision==0.21.0") == 1
    assert "--force-reinstall" not in text


def test_docker_asserts_pinned_torch_survives_later_installs():
    """Единственная установка работает только пока её никто не перебивает."""
    text = Path("Dockerfile").read_text(encoding="utf-8")

    assert "assert torch.__version__ == '2.6.0+cu124'" in text
    assert "assert torchaudio.__version__ == '2.6.0+cu124'" in text
    assert "assert torchvision.__version__ == '0.21.0+cu124'" in text


def test_all_specs_use_shared_runtime_dependency_contract():
    for spec in PACKAGING_DIR.glob("*.spec"):
        text = spec.read_text(encoding="utf-8")
        if "pyannote.audio" not in text:
            continue

        assert "collect_pure_runtime_deps" in text, spec
        assert "runtime_h" in text, spec


def test_all_desktop_specs_include_platform_live_capture_dependencies():
    for spec in PACKAGING_DIR.glob("gigaam_app*.spec"):
        text = spec.read_text(encoding="utf-8-sig")

        assert "collect_live_capture_deps" in text, spec
        assert "live_d" in text and "live_b" in text and "live_h" in text, spec



def test_windows_portable_build_installs_live_capture_runtime():
    workflow = Path(".github/workflows/build.yml").read_text(encoding="utf-8")

    assert "Install Windows live capture runtime" in workflow
    assert "requirements-live-windows.txt" in workflow
    assert "runner.os == 'Windows'" in workflow


def test_every_platform_has_a_live_capture_requirements_file():
    """Тексты ошибок и README ссылаются на все три файла — их нельзя не иметь (issue #47)."""
    assert "pyaudiowpatch" in Path("requirements-live-windows.txt").read_text(encoding="utf-8").casefold()

    for path in ("requirements-live-linux.txt", "requirements-live-macos.txt"):
        assert any(line.startswith("sounddevice==") for line in _requirement_lines(path)), path

    macos = _requirement_lines("requirements-live-macos.txt")
    # _load_macos_system_api() импортирует AVFoundation и ScreenCaptureKit;
    # CoreMedia/Foundation приезжают зависимостями AVFoundation.
    assert any(line.startswith("pyobjc-framework-AVFoundation==") for line in macos)
    assert any(line.startswith("pyobjc-framework-ScreenCaptureKit==") for line in macos)


def test_portable_build_installs_live_capture_runtime_on_every_platform():
    """CI ставил live-runtime только под Windows — Linux/macOS уезжали без захвата (issue #47)."""
    workflow = Path(".github/workflows/build.yml").read_text(encoding="utf-8")

    for name, requirements, condition in (
        ("Install Windows live capture runtime", "requirements-live-windows.txt", "runner.os == 'Windows'"),
        ("Install macOS live capture runtime", "requirements-live-macos.txt", "runner.os == 'macOS'"),
        ("Install Linux live capture runtime", "requirements-live-linux.txt", "runner.os == 'Linux'"),
    ):
        step_at = workflow.find(name)
        assert step_at != -1, name
        step = workflow[step_at : step_at + 240]
        assert condition in step, name
        assert requirements in step, name

    # Полная macOS .app собирается отдельным job'ом со своим списком установки.
    full_app_at = workflow.find("build-macos-full:")
    assert "requirements-live-macos.txt" in workflow[full_app_at:]


def test_linux_portable_build_bundles_portaudio_for_sounddevice():
    """Linux-колесо sounddevice не содержит PortAudio: библиотеку вшиваем сами (issue #47)."""
    workflow = Path(".github/workflows/build.yml").read_text(encoding="utf-8")
    common = COMMON_SPEC.read_text(encoding="utf-8")
    runtime = Path("src/live/capture/linux.py").read_text(encoding="utf-8")

    assert "libportaudio2" in workflow
    assert "collect_linux_portaudio" in common
    # Путь вшитой копии обязан совпадать с тем, что ищет рантайм.
    assert 'BUNDLED_PORTAUDIO_RELPATH = "_portaudio/libportaudio.so.2"' in common
    assert '_BUNDLED_PORTAUDIO_RELPATH = "_portaudio/libportaudio.so.2"' in runtime


def test_macos_specs_collect_sounddevice_companion_packages():
    """libportaudio.dylib лежит в _sounddevice_data — collect_all('sounddevice') его не видит."""
    common = COMMON_SPEC.read_text(encoding="utf-8")

    assert '"_sounddevice"' in common
    assert '"_sounddevice_data"' in common


def test_builds_gate_on_live_capture_actually_being_bundled():
    """Молчаливый `[skip]` не должен доезжать до релиза — как и в issue #19."""
    selfcheck = Path("src/selfcheck.py").read_text(encoding="utf-8")
    verifier = Path("scripts/verify_macos_bundle.py").read_text(encoding="utf-8")

    assert "run_live_capture_check" in selfcheck
    assert "--live-capture-smoke" in verifier


def test_tagged_build_workflow_publishes_matching_release_notes_after_assets():
    workflow = Path(".github/workflows/build.yml").read_text(encoding="utf-8")

    assert "publish-release:" in workflow
    assert "needs: [build, build-macos-full]" in workflow
    assert "RELEASE_NOTES_${VERSION}.md" in workflow
    assert "body_path:" in workflow

def test_all_specs_remain_valid_python_after_shared_contract_changes():
    for spec in PACKAGING_DIR.glob("*.spec"):
        ast.parse(spec.read_text(encoding="utf-8-sig"), filename=str(spec))
