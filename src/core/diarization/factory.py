"""Lazy diarization backend selection."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def create_diarization_backend(
    backend: str,
    *,
    hf_token: str | None = None,
    device: str = "auto",
    provider: str = "auto",
    model_dir: str | None = None,
    legacy_factory: Callable[..., Any] | None = None,
    onnx_factory: Callable[..., Any] | None = None,
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
    if selected not in {"pyannote", "sortformer"}:
        raise ValueError(f"Неизвестный backend диаризации: {backend!r}")
    if legacy_factory is None:
        from ...utils.diarization import get_diarization_manager

        legacy_factory = get_diarization_manager
    return legacy_factory(backend=selected, hf_token=hf_token, device=device)
