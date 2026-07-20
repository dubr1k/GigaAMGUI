"""Lazy diarization backend selection."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from typing import Any


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
):
    selected = str(backend or "pyannote").strip().lower()
    if selected == "onnx":
        if onnx_factory is None:
            from .onnx_backend import OnnxDiarizationBackend

            onnx_factory = OnnxDiarizationBackend
        kwargs = {"provider": provider}
        if model_dir is not None:
            kwargs["model_dir"] = model_dir
        return onnx_factory(**kwargs)

    if selected == "nvidia":
        selected = "sortformer"
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
        return sortformer_onnx_factory(**kwargs)
    if selected not in {"pyannote", "sortformer"}:
        raise ValueError(f"Неизвестный backend диаризации: {backend!r}")
    if legacy_factory is None:
        from ...utils.diarization import get_diarization_manager

        legacy_factory = get_diarization_manager
    return legacy_factory(backend=selected, hf_token=hf_token, device=device)
