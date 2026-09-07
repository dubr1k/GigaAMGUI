#!/bin/bash
# Сборка .app для macOS x86_64 (Intel) — ONNX-цепочка без torch и mlx.
# Результат: dist/GigaAMTranscriber.app
# Запуск: bash packaging/build_exe_mac_x86_64.sh
#
# Зачем отдельный скрипт, а не ветка в build_exe_mac.sh: у arm64-сборки весь
# preflight про torch/mlx/gigaam/NeMo, которых здесь не должно быть в принципе.
# Под macOS x86_64 колёса PyTorch закончились на 2.2.2 при требовании
# torch>=2.6.0, поэтому Intel-вариант везёт только ONNX (issue #45).

set -e

# Скрипт лежит в packaging/ — работаем из корня проекта.
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "[ERROR] build_exe_mac_x86_64.sh предназначен только для macOS."
    exit 1
fi

echo "============================================================"
echo " GigaAM Transcriber — Сборка macOS .app (x86_64 / ONNX)"
echo "============================================================"
echo ""

# ── Найти Python ──────────────────────────────────────────────────────────────
PYTHON="${GIGAAM_BUILD_PYTHON:-}"
if [ -n "$PYTHON" ] && [ ! -x "$PYTHON" ]; then
    echo "[ERROR] GIGAAM_BUILD_PYTHON не указывает на исполняемый Python: $PYTHON"
    exit 1
fi
if [ -z "$PYTHON" ] && [ -n "${CONDA_PREFIX:-}" ] && [ -x "$CONDA_PREFIX/bin/python" ]; then
    PYTHON="$CONDA_PREFIX/bin/python"
fi
for py in .venv/bin/python python3 python; do
    if [ -z "$PYTHON" ] && command -v "$py" &>/dev/null; then
        PYTHON="$py"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "[ERROR] Python не найден. Установи через Homebrew: brew install python"
    exit 1
fi

$PYTHON --version

# Интерпретатор обязан быть x86_64: под arm64-питоном PyInstaller соберёт
# arm64-бинарь независимо от target_arch, и подмена вскроется только у
# пользователя на Intel-маке.
PY_ARCH=$($PYTHON -c 'import platform; print(platform.machine())')
if [ "$PY_ARCH" != "x86_64" ]; then
    echo "[ERROR] Нужен x86_64-интерпретатор Python, найден: $PY_ARCH"
    echo "        На Apple Silicon: arch -x86_64 /usr/local/bin/python3 ..."
    exit 1
fi
echo "[OK] Python x86_64"

# ── Проверить зависимости ─────────────────────────────────────────────────────
if ! $PYTHON -c "import onnxruntime, onnx_asr, PyQt6, soundfile, librosa" 2>/dev/null; then
    echo "[ERROR] ONNX-зависимости не найдены. Установи:"
    echo "  $PYTHON -m pip install -r requirements-macos-x86_64.txt"
    exit 1
fi
echo "[OK] зависимости ONNX и GUI найдены"

# Без этих пакетов collect_live_capture_deps() молча собирал бандл без
# live-захвата, и вкладка Live падала уже у пользователя (issue #47).
if ! $PYTHON -c "import sounddevice, AVFoundation, ScreenCaptureKit" 2>/dev/null; then
    echo "[ERROR] Зависимости live-захвата не найдены:"
    echo "  $PYTHON -m pip install -r requirements-live-macos.txt"
    exit 1
fi
echo "[OK] зависимости live-захвата найдены"

# torch/mlx в окружении сборки означают, что PyInstaller подтянет их следом.
if $PYTHON -c "import torch" 2>/dev/null || $PYTHON -c "import mlx" 2>/dev/null; then
    echo "[ERROR] В окружении сборки есть torch или mlx — Intel-бандл должен быть без них."
    exit 1
fi
echo "[OK] torch и mlx отсутствуют, как и требуется"

echo ""
echo "[1/4] Проверка PyInstaller..."
$PYTHON -m PyInstaller --version >/dev/null
echo "[OK] PyInstaller найден"

echo ""
echo "[2/4] Очистка предыдущей сборки..."
rm -rf "dist/GigaAMTranscriber.app" "build/gigaam_app_mac_x86_64" "build/GigaAMTranscriber" "dist/GigaAMTranscriber"

echo ""
echo "[3/4] Сборка .app (может занять 5-20 минут)..."
export PYTHONPATH="$(pwd)/pyinstaller_hooks${PYTHONPATH:+:$PYTHONPATH}"
$PYTHON -m PyInstaller packaging/gigaam_app_mac_x86_64.spec --noconfirm

echo ""
echo "[4/4] Проверка итогового .app..."
$PYTHON scripts/verify_macos_bundle.py --profile x86_64-onnx dist/GigaAMTranscriber.app

echo ""
echo "============================================================"
echo " СБОРКА УСПЕШНА!"
echo " .app находится в: dist/GigaAMTranscriber.app"
echo ""
echo " Требуется macOS 13 и новее (ограничение колёс onnxruntime x86_64)."
echo " Распознавание и диаризация идут через ONNX: torch на Intel-маке недоступен."
echo "============================================================"
