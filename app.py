"""
Точка входа GUI приложения GigaAM v3 Transcriber (PyQt6).

Порядок запуска важен: сначала выбирается и активируется нужная сборка PyTorch
(CPU / CUDA 12.4 / CUDA 12.8), и только ПОСЛЕ этого импортируются модули,
которые тянут torch. Иначе torch загрузился бы раньше подстановки sys.path.
"""

import json
import os
import platform
import sys
import time
import warnings
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - macOS build uses fcntl
    fcntl = None


from src.config import ASR_BACKEND, HF_TOKEN


def _user_config_dir() -> Path:
    override = os.environ.get("GIGAAM_CONFIG_DIR")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "GigaAMTranscriber"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "GigaAMTranscriber"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "GigaAMTranscriber"


def _argv_open_paths(argv: list[str]) -> list[str]:
    paths = []
    for arg in argv[1:]:
        if arg.startswith("-psn_"):
            continue
        path = os.path.abspath(os.path.expanduser(arg))
        if os.path.exists(path):
            paths.append(path)
    return paths


def _try_acquire_instance_lock():
    lock_path = _user_config_dir() / "instance.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("w", encoding="utf-8")
    if fcntl is None:
        return lock_file
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        return None
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    return lock_file


def _queue_open_request(paths: list[str]) -> None:
    queue_path = _user_config_dir() / "open_requests.jsonl"
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"paths": paths, "pid": os.getpid(), "time": time.time()}
    with queue_path.open("a", encoding="utf-8") as queue:
        queue.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _torch_is_available() -> bool:
    """True, если torch уже можно импортировать без загрузки (вшит/установлен)."""
    import importlib.util
    try:
        return importlib.util.find_spec("torch") is not None
    except (ImportError, ValueError):
        return False


def _is_mlx_available() -> bool:
    if not (sys.platform == "darwin" and platform.machine() == "arm64"):
        return False

    import importlib.util

    return (
        importlib.util.find_spec("mlx") is not None
        and importlib.util.find_spec("gigaam_mlx") is not None
    )


def _boot_requires_torch() -> bool:
    backend = (ASR_BACKEND or "auto").strip().lower()
    if backend == "pytorch":
        return True
    if backend == "mlx":
        return not _is_mlx_available()
    if not (sys.platform == "darwin" and platform.machine() == "arm64"):
        return True
    return not _is_mlx_available()


def run_asr_runtime_smoke() -> dict[str, str]:
    """Exercise bundled MLX native code without downloading model weights."""
    import gigaam_mlx
    import mlx.core as mx

    value = mx.array([1.0, 2.0])
    mx.eval(value)
    return {
        "backend": "mlx",
        "gigaam_mlx": getattr(gigaam_mlx, "__version__", "unknown"),
    }


def run_asr_model_smoke(audio_path: str) -> dict[str, object]:
    """Load cached MLX RNN-T weights and run end-to-end file inference."""
    from gigaam_mlx import load_model, transcribe_file

    model, tokenizer = load_model("rnnt")
    segments = transcribe_file(
        audio_path,
        model=model,
        tokenizer=tokenizer,
        model_type="rnnt",
        verbose=False,
    )
    return {"backend": "mlx", "model": "rnnt", "segments": len(segments)}


def main():
    """Главная функция запуска приложения."""
    if "--asr-runtime-smoke" in sys.argv:
        print(json.dumps(run_asr_runtime_smoke(), ensure_ascii=False, sort_keys=True))
        return
    if "--asr-model-smoke" in sys.argv:
        index = sys.argv.index("--asr-model-smoke")
        if index + 1 >= len(sys.argv):
            raise SystemExit("--asr-model-smoke requires an audio path")
        print(json.dumps(run_asr_model_smoke(sys.argv[index + 1]), ensure_ascii=False, sort_keys=True))
        return

    early_lock = _try_acquire_instance_lock()
    if early_lock is None:
        _queue_open_request(_argv_open_paths(sys.argv))
        sys.exit(0)

    warnings.filterwarnings("ignore", category=UserWarning, module="pyannote")
    warnings.filterwarnings("ignore", category=UserWarning, module="speechbrain")
    warnings.filterwarnings("ignore", category=UserWarning, module="torchaudio")
    warnings.filterwarnings("ignore", message=".*torchaudio.*deprecated.*")
    warnings.filterwarnings("ignore", message=".*speechbrain.pretrained.*deprecated.*")

    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QGuiApplication
    from PyQt6.QtWidgets import QApplication

    if QApplication.instance() is None:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    app = QApplication.instance() or QApplication(sys.argv)
    app._gigaam_instance_lock_file = early_lock

    # Если выбранный backend требует PyTorch, и он ещё не установлен — предлагаем выбрать runtime.
    if _boot_requires_torch() and not _torch_is_available():
        from src.gui.device_dialog import ensure_device_ready
        from src.utils import runtime_manager as rm

        variant = ensure_device_ready()
        if not variant:
            sys.exit(0)
        rm.activate(variant)

    if HF_TOKEN and str(HF_TOKEN).startswith("hf_"):
        from src.utils.pyannote_patch import apply_pyannote_patch
        apply_pyannote_patch()

    from src.gui import run_qt_app
    run_qt_app(app=app)


if __name__ == "__main__":
    main()
