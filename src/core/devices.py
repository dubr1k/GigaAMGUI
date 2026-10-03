"""Выбор torch-устройства и очистка его кэша без импорта torch при загрузке модуля.

Модуль импортируется до активации выбранного PyTorch-рантайма, поэтому torch
берётся лениво и только когда действительно нужен (устройство «auto» или
освобождение памяти ускорителя).
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

#: Порядок по умолчанию для моделей рядом с ASR: CUDA, затем Apple MPS.
DEFAULT_ACCELERATORS = ("cuda", "mps")


def _accelerator_available(torch: Any, name: str) -> bool:
    if name == "cuda":
        return bool(torch.cuda.is_available())
    if name == "xpu":
        xpu = getattr(torch, "xpu", None)
        return xpu is not None and bool(xpu.is_available())
    if name == "mps":
        mps = getattr(getattr(torch, "backends", None), "mps", None)
        return mps is not None and bool(mps.is_available())
    return False


def best_torch_device(
    candidates: tuple[str, ...] = DEFAULT_ACCELERATORS,
    *,
    torch_module: ModuleType | Any | None = None,
) -> str:
    """Первое доступное устройство из ``candidates``, иначе ``cpu``.

    Без torch (или со сломанным/фиктивным модулем без нужных атрибутов) —
    ``cpu``: выбор устройства не должен сам становиться причиной сбоя.
    """
    try:
        torch = torch_module if torch_module is not None else __import__("torch")
    except ImportError:
        return "cpu"
    for name in candidates:
        try:
            if _accelerator_available(torch, name):
                return name
        except AttributeError:
            continue
    return "cpu"


def empty_accelerator_cache(device: str | None) -> None:
    """Вернуть драйверу свободную память CUDA/MPS после выгрузки модели."""
    if device not in {"cuda", "mps"}:
        return
    try:
        import torch

        if device == "cuda" and torch.cuda.is_available():
            torch.cuda.empty_cache()
        elif device == "mps" and hasattr(torch, "mps"):
            torch.mps.empty_cache()
    except Exception:
        pass
