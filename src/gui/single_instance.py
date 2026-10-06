"""Один экземпляр GUI: блокировка, очередь «открыть файлы» и разбор argv.

Используется и лаунчером app.py (до выбора torch-runtime), и app_qt, поэтому
модуль импортирует только stdlib и src.config; QtCore — лениво, внутри
функции, которой нужен таймер. Раньше у app.py и app_qt были свои копии
этих функций, и копия app_qt не пропускала значение `--data-dir`: папку
данных она открывала как входные файлы.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ..config import user_config_dir

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None

INSTANCE_LOCK_NAME = "instance.lock"
OPEN_REQUESTS_NAME = "open_requests.jsonl"
_DATA_DIR_FLAG = "--data-dir"


def qt_argv(argv: list[str]) -> list[str]:
    """argv без служебного `--data-dir <папка>`: его не должен видеть парсер Qt."""
    result = [argv[0]] if argv else []
    skip_next = False
    for arg in argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if arg == _DATA_DIR_FLAG:
            skip_next = True
            continue
        if arg.startswith(f"{_DATA_DIR_FLAG}="):
            continue
        result.append(arg)
    return result


def argv_open_paths(argv: list[str]) -> list[str]:
    """Существующие пути из argv, которые нужно открыть в окне."""
    paths = []
    for arg in qt_argv(argv)[1:]:
        # macOS добавляет -psn_<номер> при запуске из Finder на старых системах.
        if arg.startswith("-psn_"):
            continue
        path = os.path.abspath(os.path.expanduser(arg))
        if os.path.exists(path):
            paths.append(path)
    return paths


def instance_lock_path() -> Path:
    return user_config_dir() / INSTANCE_LOCK_NAME


def open_requests_path() -> Path:
    return user_config_dir() / OPEN_REQUESTS_NAME


def try_acquire_instance_lock():
    """Файл блокировки, если этот процесс — первый экземпляр, иначе None."""
    lock_path = instance_lock_path()
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


def queue_open_request(paths: list[str]) -> None:
    """Передать пути уже запущенному экземпляру через очередь в папке настроек."""
    queue_path = open_requests_path()
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"paths": paths, "pid": os.getpid(), "time": time.time()}
    with queue_path.open("a", encoding="utf-8") as queue:
        queue.write(json.dumps(payload, ensure_ascii=False) + "\n")


def install_open_request_poller(window, interval_ms: int = 500):
    """Опрашивать очередь и открывать пришедшие пути в окне."""
    from PyQt6.QtCore import QTimer

    queue_path = open_requests_path()
    timer = QTimer(window)

    def poll_requests():
        if not queue_path.exists():
            return
        try:
            lines = queue_path.read_text(encoding="utf-8").splitlines()
            queue_path.write_text("", encoding="utf-8")
        except OSError as exc:
            window.log(window._t(
                f"Не удалось прочитать очередь открытия файлов: {exc}",
                f"Could not read the open-files queue: {exc}",
            ))
            return
        for line in lines:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            paths = payload.get("paths") or []
            if isinstance(paths, list):
                window.open_paths_from_system(paths, append=True)

    timer.timeout.connect(poll_requests)
    timer.start(interval_ms)
    window._open_request_poller = timer
    QTimer.singleShot(0, poll_requests)
    return timer
