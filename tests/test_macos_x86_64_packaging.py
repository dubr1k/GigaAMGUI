"""Проверки Intel-сборки macOS (issue #45): ONNX-цепочка без torch и mlx.

Под macOS x86_64 колёса PyTorch закончились на 2.2.2 при требовании
torch>=2.6.0, поэтому Intel-вариант собирается отдельным спеком и обязан
оставаться торч-независимым — и в списке зависимостей, и в графе импортов.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

SPEC_PATH = Path("packaging/gigaam_app_mac_x86_64.spec")
ARM64_SPEC_PATH = Path("packaging/gigaam_app_mac.spec")
REQUIREMENTS_PATH = Path("requirements-macos-x86_64.txt")
BUILD_SCRIPT_PATH = Path("packaging/build_exe_mac_x86_64.sh")
VERIFIER_PATH = Path("scripts/verify_macos_bundle.py")
WORKFLOW_PATH = Path(".github/workflows/build.yml")
SPEC_COMMON_PATH = Path("packaging/_spec_common.py")

#: Всё, что тянет torch. Присутствие любого из них в Intel-сборке означает либо
#: неразрешимый набор зависимостей, либо бинарники не той архитектуры.
TORCH_CHAIN = (
    "torch",
    "torchaudio",
    "torchvision",
    "mlx",
    "gigaam",
    "gigaam_mlx",
    "pyannote",
    "transformers",
    "lightning",
    "pytorch_lightning",
    "speechbrain",
    "accelerate",
    "nemo",
)


def test_requirements_carry_onnx_chain_without_torch():
    lines = [
        line.split("#", 1)[0].strip()
        for line in REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines()
    ]
    requirements = [line for line in lines if line]

    names = {
        line.split("==")[0].split(">=")[0].split("[")[0].strip().lower()
        for line in requirements
    }
    assert "onnx-asr" in names
    assert "onnxruntime" in names
    assert "pyqt6" in names
    for banned in TORCH_CHAIN:
        assert banned not in names, f"{banned} не должен приезжать в Intel-сборку"

    # numba 0.67 и llvmlite 0.49 перестали выпускать колёса под macOS x86_64.
    # Без пинов pip берёт свежие версии и уходит собирать llvmlite из исходников
    # под полный LLVM-тулчейн — сборка падает на CMake `Could not find LLVM`.
    assert "numba" in names and "llvmlite" in names


def test_ci_refuses_to_build_intel_dependencies_from_source():
    job = WORKFLOW_PATH.read_text(encoding="utf-8").split("build-macos-intel:")[1]
    job = job.split("\n  test-tui:")[0]

    assert "--only-binary=:all: -r requirements-macos-x86_64.txt" in job
    assert "--only-binary=:all: -r requirements-live-macos.txt" in job
    # Набор для бандла намеренно не везёт pytest (в спеке он в excludes), но шаг
    # с тестами упаковки без него не запускается — ставим явно.
    assert '"pytest==9.0.1"' in job
    assert job.index('pip install --only-binary=:all: "pytest') < job.index("python -m pytest")


def test_spec_targets_x86_64_and_excludes_torch_chain():
    text = SPEC_PATH.read_text(encoding="utf-8")
    assert 'target_arch="x86_64"' in text
    # PIL/asteroid_filterbanks нужны только рантайм-torchvision и pyannote,
    # которых здесь нет: их сбор уронил бы спек на отсутствующем пакете.
    imports = text.split("from _spec_common import")[1].split("\n")[0]
    assert "collect_pure_runtime_deps" not in imports
    assert "collect_onnx_runtime_deps()" in text
    assert "collect_live_capture_deps()" in text
    for banned in ("torch", "mlx", "pyannote", "transformers", "gigaam"):
        assert f'    "{banned}",' in text.split("excluded_modules = [")[1]


def test_spec_declares_macos_13_minimum_for_onnxruntime_wheels():
    text = SPEC_PATH.read_text(encoding="utf-8")
    assert '"LSMinimumSystemVersion": "13.0"' in text


def test_both_macos_specs_share_one_version_source():
    # Иначе релиз ловил бы разъехавшуюся версию уже после сборки: CI сверяет
    # CFBundleShortVersionString с тегом отдельно для каждого бандла.
    assert 'APP_VERSION = "' in SPEC_COMMON_PATH.read_text(encoding="utf-8")
    for spec in (SPEC_PATH, ARM64_SPEC_PATH):
        text = spec.read_text(encoding="utf-8")
        assert "APP_VERSION" in text.split("from _spec_common import")[1].split("\n")[0]
        assert '"CFBundleShortVersionString": APP_VERSION,' in text
        assert '"CFBundleVersion": APP_VERSION,' in text


def test_build_script_refuses_arm64_interpreter_and_torch_environment():
    text = BUILD_SCRIPT_PATH.read_text(encoding="utf-8")
    # arm64-питон дал бы arm64-бинарь независимо от target_arch.
    assert 'if [ "$PY_ARCH" != "x86_64" ]' in text
    assert 'import torch' in text and 'import mlx' in text
    assert "scripts/verify_macos_bundle.py --profile x86_64-onnx" in text
    assert "requirements-macos-x86_64.txt" in text


def test_verifier_profile_forbids_torch_and_checks_onnx_runtime():
    text = VERIFIER_PATH.read_text(encoding="utf-8")
    assert '"x86_64-onnx": BundleProfile(' in text
    assert 'arch="x86_64"' in text
    assert 'forbidden_packages=("torch", "mlx", "gigaam_mlx", "pyannote")' in text
    assert 'runtime_smoke=("--onnx-runtime-smoke", \'"backend": "onnx"\')' in text
    assert "--onnx-runtime-smoke" in Path("app.py").read_text(encoding="utf-8")
    # Диаризацию на Intel закрывает офлайн-смок на привезённых моделях, а не
    # сетевая догрузка посреди сборки.
    assert "--offline-models-smoke" in WORKFLOW_PATH.read_text(encoding="utf-8")


def test_ci_replaces_the_committed_arm64_ffmpeg_on_intel():
    """`bin/ffmpeg` в репозитории — arm64, на Intel он не запускается.

    Первый прогон job-а упал именно здесь: шаг arm64-сборки `./bin/ffmpeg
    -version` на Intel-раннере не выполняется в принципе, а протащенный дальше
    arm64-бинарь сломал бы конвертацию уже у пользователя.
    """
    job = WORKFLOW_PATH.read_text(encoding="utf-8").split("build-macos-intel:")[1]
    job = job.split("\n  test-tui:")[0]

    assert "Provision bundled ffmpeg (macOS x86_64)" in job
    assert "for tool in ffmpeg ffprobe" in job
    # Проверка архитектуры прямо в шаге: иначе подмена всплыла бы только в
    # verify_macos_bundle.py, уже после двадцати минут сборки.
    assert 'TOOL_ARCH=$(lipo -archs "bin/$tool")' in job
    assert 'test "$TOOL_ARCH" = "x86_64"' in job


def test_ci_builds_and_publishes_intel_offline_bundle():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "build-macos-intel:" in text
    # macos-13 выведен из эксплуатации, macos-15-intel живёт до августа 2027.
    assert "runs-on: macos-15-intel" in text
    assert "bash packaging/build_exe_mac_x86_64.sh" in text
    assert "requirements-macos-x86_64.txt" in text
    assert "GigaAMTranscriber-macos-x86_64-app-offline-" in text
    assert "--offline-models-smoke" in text
    assert 'lipo -archs' in text
    assert "needs: [build, build-macos-full, build-macos-intel, build-macos-swift]" in text


def test_onnx_pipeline_imports_without_the_torch_chain():
    """Граф импортов ONNX-пути и GUI не должен трогать torch.

    Достаточно одного `import torch` на уровне модуля в src/gui или src/core —
    и Intel-сборка перестанет стартовать. На arm64-машине это не видно ничем,
    кроме такого гейта: torch тут есть и импорт проходит молча.
    """
    script = textwrap.dedent(
        f"""
        import sys

        BANNED = {TORCH_CHAIN!r}

        class Blocker:
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] in BANNED:
                    raise ImportError(f"blocked for the Intel bundle: {{name}}")
                return None

        sys.meta_path.insert(0, Blocker())

        for module in (
            "src.config",
            "src.core.asr.onnx_backend",
            "src.core.asr.onnx_vad",
            "src.core.diarization.onnx_backend",
            "src.core.diarization.sortformer_onnx",
            "src.core.diarization.factory",
            "src.core.processor",
            "src.core.model_loader",
            "src.live.asr",
            "src.gui.app_qt",
        ):
            __import__(module)
        print("ok")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env={"QT_QPA_PLATFORM": "offscreen", "PATH": "/usr/bin:/bin", "HOME": str(Path.home())},
        timeout=300,
    )
    assert result.returncode == 0, (result.stdout + result.stderr)[-4000:]
