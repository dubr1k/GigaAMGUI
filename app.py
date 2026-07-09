"""
Точка входа GUI приложения GigaAM v3 Transcriber (PyQt6).

Порядок запуска важен: сначала выбирается и активируется нужная сборка PyTorch
(CPU / CUDA 12.4 / CUDA 12.8), и только ПОСЛЕ этого импортируются модули,
которые тянут torch. Иначе torch загрузился бы раньше подстановки sys.path.
"""

import json
import os
import sys
import time
import warnings
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - macOS build uses fcntl
    fcntl = None


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


def main():
    """Главная функция запуска приложения."""
    early_lock = _try_acquire_instance_lock()
    if early_lock is None:
        _queue_open_request(_argv_open_paths(sys.argv))
        sys.exit(0)

    warnings.filterwarnings("ignore", category=UserWarning, module="pyannote")
    warnings.filterwarnings("ignore", category=UserWarning, module="speechbrain")
    warnings.filterwarnings("ignore", category=UserWarning, module="torchaudio")
    warnings.filterwarnings("ignore", message=".*torchaudio.*deprecated.*")
    warnings.filterwarnings("ignore", message=".*speechbrain.pretrained.*deprecated.*")

    from PyQt6.QtGui import QGuiApplication
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication

    if QApplication.instance() is None:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    app = QApplication.instance() or QApplication(sys.argv)
    app._gigaam_instance_lock_file = early_lock

    # torch НЕ вшит в портативную сборку — выбираем и при необходимости качаем.
    # Если torch уже доступен (dev-окружение или сборка с вшитым torch) — пропускаем.
    if not _torch_is_available():
        from src.utils import runtime_manager as rm
        from src.gui.device_dialog import ensure_device_ready

        variant = ensure_device_ready()
        if not variant:
            sys.exit(0)
        rm.activate(variant)

    from src.utils.pyannote_patch import apply_pyannote_patch
    apply_pyannote_patch()

    from src.gui import run_qt_app
    run_qt_app(app=app)


if __name__ == "__main__":
    main()
