#!/bin/bash
# Сборка .app для macOS (GigaAM v3 Transcriber)
# Результат: dist/GigaAMTranscriber.app
# Запуск: bash build_exe_mac.sh

set -e
echo "============================================================"
echo " GigaAM Transcriber — Сборка macOS .app"
echo "============================================================"
echo ""

# ── Найти Python ──────────────────────────────────────────────────────────────
PYTHON=""
for py in python3 python; do
    if command -v "$py" &>/dev/null; then
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
if ! $PYTHON -c "import gigaam" 2>/dev/null; then
    echo "[ERROR] Пакет gigaam не найден. Установи зависимости:"
    echo "  pip install -r requirements.txt"
    echo "  pip install git+https://github.com/salute-developers/GigaAM.git@0a3f1036d93287d5ef226911ec795bde8ef05d57#egg=gigaam --no-build-isolation"
    exit 1
fi
echo "[OK] gigaam найден"

# ── PyInstaller ───────────────────────────────────────────────────────────────
echo ""
echo "[1/3] Установка PyInstaller..."
$PYTHON -m pip install pyinstaller --upgrade -q
echo "[OK] PyInstaller готов"

# ── Очистка ───────────────────────────────────────────────────────────────────
echo ""
echo "[2/3] Очистка предыдущей сборки..."
rm -rf "dist/GigaAMTranscriber.app" "dist/GigaAMTranscriber" "build/GigaAMTranscriber"

# ── Сборка ────────────────────────────────────────────────────────────────────
echo ""
echo "[3/3] Сборка .app (может занять 5-20 минут)..."
echo ""
$PYTHON -m PyInstaller gigaam_app_mac.spec --noconfirm

echo ""
echo "============================================================"
echo " СБОРКА УСПЕШНА!"
echo " .app находится в: dist/GigaAMTranscriber.app"
echo ""
echo " При первом запуске скачается модель GigaAM (~1-2 GB)"
echo " Токен не нужен — модель публичная."
echo "============================================================"
