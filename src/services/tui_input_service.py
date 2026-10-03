"""Лёгкое разрешение файлов TUI: без моделей и блокировки JSONL-команд."""
from __future__ import annotations

import json
import os
import stat
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from src.services.file_policy import is_supported_by_set

# Клиенты (TUI, Liquid) ограничивают одну строку протокола 8 MiB и останавливают
# worker, который её превысил. Ответ с запасом меньше; больше — понятная ошибка.
MAX_REPLY_BYTES = 4 * 1024 * 1024
# Тысячи нечитаемых записей не должны раздувать ответ и журнал клиента.
MAX_REPORTED_ERRORS = 200


def _result(*, cancelled: bool = False) -> dict[str, Any]:
    return {"files": [], "duplicates": [], "errors": [], "cancelled": cancelled}


def _undecodable(text: str) -> bool:
    """Имя из ОС, не являющееся UTF-8 (Linux): Python отдаёт его с суррогатами."""
    try:
        text.encode("utf-8")
        return False
    except UnicodeEncodeError:
        return True


def _display(text: str) -> str:
    """Текст для протокола: недекодируемые байты имени показываются как U+FFFD."""
    if not _undecodable(text):
        return text
    try:
        return text.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
    except UnicodeEncodeError:
        return text.encode("utf-8", "replace").decode("utf-8")


def _error(path: str, code: str, message: str) -> dict[str, str]:
    return {"path": _display(path), "code": code, "message": _display(message)}


def _bounded(result: dict[str, Any]) -> dict[str, Any]:
    """Ответ, который клиент гарантированно примет одной строкой протокола."""
    errors = result["errors"]
    if len(errors) > MAX_REPORTED_ERRORS:
        hidden = len(errors) - MAX_REPORTED_ERRORS
        result["errors"] = [*errors[:MAX_REPORTED_ERRORS],
                            _error("", "more_errors", f"{hidden} more problems are not listed")]
    size = len(json.dumps(result, ensure_ascii=False).encode("utf-8"))
    if size > MAX_REPLY_BYTES:
        count = len(result["files"]) + len(result["duplicates"])
        bounded = _result()
        bounded["errors"] = [
            _error("", "too_many_files", f"Too many files to add at once ({count}); add the folder in parts"),
            *result["errors"],
        ]
        return bounded
    return result


def _input_path(raw: str) -> Path:
    if raw.startswith("file://"):
        uri = urlsplit(raw)
        if uri.netloc not in ("", "localhost") or uri.query or uri.fragment:
            raise ValueError("Only local file URLs without query or fragment are supported")
        raw = unquote(uri.path)
        if os.name == "nt" and len(raw) >= 3 and raw[0] == "/" and raw[2] == ":":
            raw = raw[1:]
    return Path(raw).expanduser().resolve(strict=True)


def resolve_paths(paths: list[str], cancelled: Callable[[], bool]) -> dict[str, Any]:
    """Сохраняет порядок входов, сортирует содержимое папок, не следует циклам."""
    # Общий реестр загружается только при запросе; ML-модулей config не импортирует.
    from src.config import MEDIA_EXTENSIONS

    result = _result()
    seen: set[str] = set()

    def problem(path: str, exc: Exception) -> None:
        code = "missing" if isinstance(exc, FileNotFoundError) else "unreadable"
        if isinstance(exc, ValueError):
            code = "invalid_request"
        result["errors"].append(_error(path, code, str(exc)))

    def undecodable(path: str) -> None:
        # Такой путь не передать в JSON без потерь, а значит, и обработать потом.
        result["errors"].append(_error(path, "undecodable", f"File name is not valid UTF-8: {_display(path)}"))

    def accept(path: Path) -> None:
        try:
            canonical = path.resolve(strict=True)
            if not stat.S_ISREG(canonical.stat().st_mode):
                raise ValueError(f"Not a regular file: {path}")
            if not is_supported_by_set(canonical.name, MEDIA_EXTENSIONS):
                result["errors"].append(_error(str(path), "unsupported", f"Unsupported media file: {path}"))
                return
            name = str(canonical)
            if _undecodable(name):
                undecodable(name)
                return
            result["duplicates" if name in seen else "files"].append(name)
            seen.add(name)
        except (OSError, ValueError, RuntimeError) as exc:
            problem(str(path), exc)

    for raw in paths:
        if cancelled():
            return _result(cancelled=True)
        try:
            path = _input_path(raw)
            mode = path.stat().st_mode
        except (OSError, ValueError, RuntimeError) as exc:
            problem(raw, exc)
            continue
        if not stat.S_ISDIR(mode):
            accept(path)
            continue

        pending = [path]
        found: list[Path] = []
        errors_before = len(result["errors"])
        while pending:
            if cancelled():
                return _result(cancelled=True)
            directory = pending.pop()
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if cancelled():
                            return _result(cancelled=True)
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                pending.append(Path(entry.path))
                            elif entry.is_file() and is_supported_by_set(entry.name, MEDIA_EXTENSIONS):
                                if _undecodable(entry.path):
                                    undecodable(entry.path)
                                else:
                                    found.append(Path(entry.path))
                        except OSError as exc:
                            problem(entry.path, exc)
            except OSError as exc:
                problem(str(directory), exc)
        for file in sorted(found, key=lambda item: item.relative_to(path).as_posix()):
            if cancelled():
                return _result(cancelled=True)
            accept(file)
        if not found and len(result["errors"]) == errors_before:
            result["errors"].append(_error(raw, "empty_directory", f"No supported media files in: {raw}"))
    return _result(cancelled=True) if cancelled() else _bounded(result)


class InputResolver:
    """Один обход за раз; команды и отмена остаются доступны в потоке воркера."""

    def __init__(self, emit: Callable[..., None]) -> None:
        self._emit = emit
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tui-inputs")
        self._lock = threading.Lock()
        self._pending: dict[int, threading.Event] = {}
        self._closed = False

    def _reject(self, request_id: Any, message: str) -> None:
        result = _result()
        result["errors"].append(_error("", "invalid_request", message))
        self._emit("inputs_resolved", request_id=request_id, **result)

    def start(self, command: dict[str, Any]) -> None:
        request_id = command.get("request_id")
        paths = command.get("paths")
        if type(request_id) is not int or request_id <= 0:
            self._reject(request_id, "request_id must be a positive integer")
            return
        if not isinstance(paths, list) or not paths or not all(isinstance(path, str) and path.strip() for path in paths):
            self._reject(request_id, "paths must be a non-empty array of non-empty strings")
            return
        with self._lock:
            if self._closed:
                reason = "Input resolver is closed"
            elif request_id in self._pending:
                reason = "request_id is already being resolved"
            else:
                token = threading.Event()
                self._pending[request_id] = token
                self._executor.submit(self._run, request_id, list(paths), token)
                return
        self._reject(request_id, reason)

    def _run(self, request_id: int, paths: list[str], token: threading.Event) -> None:
        try:
            result = resolve_paths(paths, token.is_set)
        except Exception as exc:
            result = _result()
            result["errors"].append(_error("", "unreadable", str(exc)))
        with self._lock:
            if token.is_set():
                result = _result(cancelled=True)
            if self._pending.get(request_id) is token:
                self._pending.pop(request_id)
        try:
            self._emit("inputs_resolved", request_id=request_id, **result)
        except Exception as exc:
            # Исключение в потоке executor'а молча терялось вместе с ответом, и клиент
            # навсегда оставался в «добавлении файлов». Ответ приходит всегда.
            fallback = _result()
            fallback["errors"].append(_error("", "internal", f"Cannot report resolved inputs: {exc}"))
            self._emit("inputs_resolved", request_id=request_id, **fallback)

    def cancel(self, request_id: Any) -> None:
        if type(request_id) is not int:
            return
        with self._lock:
            if token := self._pending.get(request_id):
                token.set()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for token in self._pending.values():
                token.set()
            self._pending.clear()
        self._executor.shutdown(wait=True, cancel_futures=True)
