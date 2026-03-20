#!/bin/bash
# Быстрый запуск GUI приложения (путь проекта — по расположению скрипта)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR" || exit 1

# Сначала пробуем venv в проекте
if [ -d "$PROJECT_DIR/venv/bin" ]; then
    source "$PROJECT_DIR/venv/bin/activate"
# Иначе — conda окружение gigam (путь по conda info --base или типичные места)
else
    for conda_base in "$(conda info --base 2>/dev/null)" /data/miniconda3 "$HOME/miniconda3" "$HOME/anaconda3"; do
        [ -z "$conda_base" ] && continue
        if [ -f "$conda_base/etc/profile.d/conda.sh" ]; then
            source "$conda_base/etc/profile.d/conda.sh"
            conda activate gigam 2>/dev/null && break
        fi
    done
fi

exec python app.py
