#!/bin/sh
set -eu

data_root="${GIGAAM_DATA_DIR:-/data}"
export GIGAAM_DATA_DIR="$data_root"
export GIGAAM_RUNTIME_DIR="${GIGAAM_RUNTIME_DIR:-$data_root/runtimes}"
export GIGAAM_PYTORCH_MODEL_DIR="${GIGAAM_PYTORCH_MODEL_DIR:-$data_root/models/gigaam}"
export HF_HOME="${HF_HOME:-$data_root/models/huggingface}"
export HUGGINGFACE_HUB_CACHE="${HUGGINGFACE_HUB_CACHE:-$HF_HOME/hub}"
export TORCH_HOME="${TORCH_HOME:-$data_root/models/torch}"
export NEMO_HOME="${NEMO_HOME:-$data_root/models/nemo}"
export ONNX_MODEL_DIR="${ONNX_MODEL_DIR:-$data_root/models/onnx}"
export GIGAAM_DEEPFILTER_DIR="${GIGAAM_DEEPFILTER_DIR:-$data_root/models/deepfilter}"
export HOME="${GIGAAM_HOME:-$data_root/runtime-home}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-/tmp/cache}"

# Относительные UPLOAD_DIR/RESULTS_DIR web-приложение считает от корня проекта.
app_root="${GIGAAM_APP_DIR:-/app}"
app_path() {
    case "$1" in
        /*) printf '%s\n' "$1" ;;
        *) printf '%s/%s\n' "$app_root" "$1" ;;
    esac
}
upload_dir="$(app_path "${UPLOAD_DIR:-uploads}")"
results_dir="$(app_path "${RESULTS_DIR:-results}")"
logs_dir="$app_root/logs"

# Docker создаёт новый bind mount как root:root. Перед запуском приложения
# создаём только известные writable-каталоги и отдаём их приложению без
# рекурсивного chown уже скачанных моделей.
if [ "$(id -u)" = "0" ]; then
    mkdir -p \
        "$data_root" \
        "$GIGAAM_RUNTIME_DIR" \
        "$GIGAAM_PYTORCH_MODEL_DIR" \
        "$HF_HOME" \
        "$HUGGINGFACE_HUB_CACHE" \
        "$TORCH_HOME" \
        "$NEMO_HOME" \
        "$ONNX_MODEL_DIR" \
        "$GIGAAM_DEEPFILTER_DIR" \
        "$HOME" \
        "$XDG_CACHE_HOME"
    chown gigaam:gigaam \
        "$data_root" \
        "$GIGAAM_RUNTIME_DIR" \
        "$GIGAAM_PYTORCH_MODEL_DIR" \
        "$HF_HOME" \
        "$HUGGINGFACE_HUB_CACHE" \
        "$TORCH_HOME" \
        "$NEMO_HOME" \
        "$ONNX_MODEL_DIR" \
        "$GIGAAM_DEEPFILTER_DIR" \
        "$HOME" \
        "$XDG_CACHE_HOME"
    # Каталоги задач compose монтирует с хоста (./uploads, ./results, ./logs).
    # Если на хосте их не было, Docker создаёт их root:root поверх подготовленных
    # в образе, и приложение под uid 1000 не может сохранить ни одной загрузки.
    # Права — только самим каталогам: старые результаты внутри не трогаем.
    mkdir -p "$upload_dir" "$results_dir" "$logs_dir"
    chown gigaam:gigaam "$upload_dir" "$results_dir" "$logs_dir"
    # gosu may replace HOME with the value from /etc/passwd. Pass it again
    # after switching users so libraries never fall back to read-only /home.
    exec gosu gigaam env HOME="$HOME" "$@"
fi

exec "$@"
