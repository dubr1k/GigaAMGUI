"""
Headless-проверка целостности сборки: `python app.py --selfcheck`.

Импортирует ВСЮ цепочку, которая ломалась в релизах (torchvision → PIL,
pyannote.audio, torchmetrics, docx). Возвращает 0, если всё импортируется,
иначе 1 с трейсбеком. Запускается в CI на СОБРАННОМ бинаре — так любая
недостающая зависимость валит релиз до публикации.
"""
from __future__ import annotations

import importlib
import traceback

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


def _import_module(name: str) -> None:
    importlib.import_module(name)


def _ensure_torch() -> None:
    """Активирует/ставит CPU-вариант torch, чтобы импорт torchvision прошёл."""
    from src.utils import runtime_manager

    variant = runtime_manager.DEFAULT_VARIANT
    if not runtime_manager.is_installed(variant):
        runtime_manager.install_variant(variant, log_cb=print)
    runtime_manager.activate(variant)


def run_selfcheck(check_torch: bool = True) -> int:
    if check_torch:
        try:
            _ensure_torch()
        except Exception:
            print("SELFCHECK FAIL: torch runtime setup")
            traceback.print_exc()
            return 1
    for name in _CHAIN:
        try:
            _import_module(name)
            print(f"SELFCHECK ok: {name}")
        except Exception as e:
            print(f"SELFCHECK FAIL: {name}: {type(e).__name__}: {e}")
            traceback.print_exc()
            return 1
    print("SELFCHECK PASS")
    return 0
