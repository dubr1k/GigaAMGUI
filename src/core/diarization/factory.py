"""Lazy diarization backend selection."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from typing import Any

from .names import normalize_diarization_backend


def should_use_sortformer_onnx(
    *,
    platform_name: str | None = None,
    nemo_available: bool | None = None,
) -> bool:
    """Native Windows and installations without NeMo use portable ONNX."""
    if (platform_name or sys.platform) == "win32":
        return True
    if nemo_available is None:
        try:
            nemo_available = importlib.util.find_spec("nemo") is not None
        except (ImportError, AttributeError, ValueError):
            nemo_available = False
    return not nemo_available


def create_diarization_backend(
    backend: str,
    *,
    hf_token: str | None = None,
    device: str = "auto",
    provider: str = "auto",
    model_dir: str | None = None,
    legacy_factory: Callable[..., Any] | None = None,
    onnx_factory: Callable[..., Any] | None = None,
    sortformer_onnx_factory: Callable[..., Any] | None = None,
    platform_name: str | None = None,
    nemo_available: bool | None = None,
    **pyannote_options: Any,
):
    """Создать backend диаризации; тяжёлые зависимости импортируются лениво.

    ``onnx`` и Sortformer-ONNX не тянут torch. pyannote и NeMo Sortformer
    создаются только здесь (бывший ``utils.diarization.get_diarization_manager``
    — теперь его тонкий псевдоним). ``pyannote_options`` (min_speakers,
    max_speakers) уходят в ``DiarizationManager``.
    """
    selected = normalize_diarization_backend(backend)
    if selected == "onnx":
        if onnx_factory is None:
            from .onnx_backend import OnnxDiarizationBackend

            onnx_factory = OnnxDiarizationBackend
        kwargs = {"provider": provider}
        if model_dir is not None:
            kwargs["model_dir"] = model_dir
        return onnx_factory(**kwargs)

    if selected == "sortformer" and should_use_sortformer_onnx(
        platform_name=platform_name,
        nemo_available=nemo_available,
    ):
        if sortformer_onnx_factory is None:
            from .sortformer_onnx import SortformerOnnxDiarizationManager

            sortformer_onnx_factory = SortformerOnnxDiarizationManager
        kwargs = {"provider": provider}
        if model_dir is not None:
            kwargs["model_dir"] = model_dir
        if device != "auto":
            kwargs["device"] = device
        return sortformer_onnx_factory(**kwargs)

    if legacy_factory is not None:
        return legacy_factory(backend=selected, hf_token=hf_token, device=device)
    if selected == "sortformer":
        from .sortformer_nemo import SortformerDiarizationManager

        return SortformerDiarizationManager(device=device)
    from .pyannote_backend import DiarizationManager

    return DiarizationManager(hf_token=hf_token, device=device, **pyannote_options)
