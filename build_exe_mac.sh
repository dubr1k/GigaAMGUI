#!/bin/bash
# Сборка .app для macOS (GigaAM v3 Transcriber)
# Результат: dist/GigaAMTranscriber.app
# Запуск: bash build_exe_mac.sh

set -e

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "[ERROR] build_exe_mac.sh предназначен только для macOS."
    exit 1
fi

if [[ "$(uname -m)" != "arm64" ]]; then
    echo "[ERROR] Требуется Apple Silicon (arm64) для текущего пайплайна."
    exit 1
fi

echo "============================================================"
echo " GigaAM Transcriber — Сборка macOS .app"
echo "============================================================"
echo ""

# ── Найти Python ──────────────────────────────────────────────────────────────
PYTHON=""
if [ -n "${CONDA_PREFIX:-}" ] && [ -x "$CONDA_PREFIX/bin/python" ]; then
    PYTHON="$CONDA_PREFIX/bin/python"
fi
for py in .venv/bin/python python3 python; do
    if [ -z "$PYTHON" ] && command -v "$py" &>/dev/null; then
        PYTHON="$py"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "[ERROR] Python не найден. Установи через Homebrew:"
    echo "  brew install python"
    exit 1
fi

$PYTHON --version
echo ""

# ── Проверить gigaam ──────────────────────────────────────────────────────────
if ! $PYTHON -c "import gigaam; import torch; import torchaudio; import PyQt6; import mlx; import gigaam_mlx" 2>/dev/null; then
    echo "[ERROR] Пакет gigaam не найден. Установи зависимости:"
    echo "  python3 -m venv .venv"
    echo "  .venv/bin/python -m pip install -r requirements.txt"
    echo "  .venv/bin/python -m pip install git+https://github.com/salute-developers/GigaAM.git@0a3f1036d93287d5ef226911ec795bde8ef05d57#egg=gigaam --no-build-isolation"
    echo "  .venv/bin/python -m pip install git+https://github.com/aystream/gigaam-mlx.git@20276ddd6173d636b37c6c6e13b4ee8f7b94d1ac#egg=gigaam-mlx"
    exit 1
fi
echo "[OK] зависимости GUI найдены"

# ── PyInstaller ───────────────────────────────────────────────────────────────
echo ""
echo "[1/3] Проверка PyInstaller..."
$PYTHON -m PyInstaller --version >/dev/null
echo "[OK] PyInstaller найден"

# ── Очистка ───────────────────────────────────────────────────────────────────
echo ""
echo "[2/3] Очистка предыдущей сборки..."
rm -rf "dist/GigaAMTranscriber.app" "build/gigaam_app_mac" "build/GigaAMTranscriber" "dist/GigaAMTranscriber"

# ── Сборка ────────────────────────────────────────────────────────────────────
echo ""
echo "[3/3] Сборка .app (может занять 5-20 минут)..."
echo ""
export PYTHONPATH="$(pwd)/pyinstaller_hooks${PYTHONPATH:+:$PYTHONPATH}"
$PYTHON -m PyInstaller gigaam_app_mac.spec --noconfirm

echo ""
echo "[4/4] Проверка итогового .app..."
$PYTHON scripts/verify_macos_bundle.py dist/GigaAMTranscriber.app

echo ""
echo "============================================================"
echo " СБОРКА УСПЕШНА!"
echo " .app находится в: dist/GigaAMTranscriber.app"
echo ""
echo " При первом запуске скачается модель GigaAM (~1-2 GB)"
echo " Токен не нужен — модель публичная."
echo "============================================================"
