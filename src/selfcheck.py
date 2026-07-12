"""
Headless-проверка целостности сборки: `python app.py --selfcheck`.

Импортирует ВСЮ цепочку, которая ломалась в релизах (torchvision → PIL,
pyannote.audio, torchmetrics, docx). Возвращает 0, если всё импортируется,
иначе 1 с трейсбеком. Запускается в CI на СОБРАННОМ бинаре — так любая
недостающая зависимость валит релиз до публикации.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import traceback
from pathlib import Path

# Порядок важен: torchvision тянет PIL.ImageEnhance (это и был #19),
# torchmetrics тянет torchvision, pyannote тянет всё лестницей.
_CHAIN = [
    "PIL.ImageEnhance",
    "PIL.ImageOps",
    "torchvision",
    "torchvision.transforms",
    "torchmetrics",
    "torchmetrics.functional.image",
    "pyannote.audio",
    "docx",
]


def _log_path() -> Path:
    base = os.environ.get("GIGAAM_RUNTIME_DIR") or tempfile.gettempdir()
    return Path(base) / "selfcheck.log"


_log_lines: list[str] = []


def _emit(msg: str) -> None:
    """Пишет строку в stderr/stdout (если доступны) и в лог-файл.

    На windowed-сборке (console=False) sys.stdout/stderr могут быть None —
    поэтому каждый вывод защищён, а полный лог всегда дублируется в файл,
    чтобы CI мог показать причину падения.
    """
    _log_lines.append(msg)
    for stream in (sys.stderr, sys.stdout):
        try:
            if stream is not None:
                stream.write(msg + "\n")
                stream.flush()
        except Exception:
            pass
    try:
        p = _log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(_log_lines) + "\n", encoding="utf-8")
    except Exception:
        pass


def _import_module(name: str) -> None:
    importlib.import_module(name)


def _ensure_torch() -> None:
    """Активирует/ставит CPU-вариант torch, чтобы импорт torchvision прошёл."""
    from src.utils import runtime_manager

    variant = runtime_manager.DEFAULT_VARIANT
    if not runtime_manager.is_installed(variant):
        # log_cb=_emit (не print): на windowed-сборке sys.stdout может быть None,
        # и голый print во время загрузки torch кинул бы исключение → ложный
        # "torch runtime setup FAIL" на исправном билде.
        runtime_manager.install_variant(variant, log_cb=_emit)
    runtime_manager.activate(variant)


def run_selfcheck(check_torch: bool = True) -> int:
    if check_torch:
        try:
            _ensure_torch()
        except Exception:
            _emit("SELFCHECK FAIL: torch runtime setup")
            _emit(traceback.format_exc())
            return 1
    for name in _CHAIN:
        try:
            _import_module(name)
            _emit(f"SELFCHECK ok: {name}")
        except Exception as e:
            _emit(f"SELFCHECK FAIL: {name}: {type(e).__name__}: {e}")
            _emit(traceback.format_exc())
            return 1
    _emit("SELFCHECK PASS")
    return 0
