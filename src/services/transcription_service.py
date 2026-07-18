"""Единая фабрика TranscriptionProcessor.

Все четыре поверхности (api/web/cli/gui) собирали процессор вручную одинаковым
конструктором. Фабрика убирает это дублирование, не меняя сигнатуру процессора:
управление форматами/диаризацией остаётся за каждой поверхностью через process_file.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from src.core.asr.models import validate_asr_model
from src.core.asr.types import validate_backend_name
from src.core.processor import TranscriptionProcessor

ONNX_PROVIDERS = ("auto", "cpu", "cuda", "tensorrt", "coreml", "directml")


@dataclass(frozen=True)
class AsrSelection:
    """Нормализованный ASR runtime для одной задачи."""

    backend: str
    model: str
    onnx_provider: str

    def as_dict(self) -> dict[str, str]:
        return {
            "asr_backend": self.backend,
            "asr_model": self.model,
            "onnx_provider": self.onnx_provider,
        }


def normalize_asr_selection(
    default_loader,
    *,
    backend: str | None = None,
    model: str | None = None,
    onnx_provider: str | None = None,
) -> AsrSelection:
    """Заполнить пустые поля настройками серверного loader и проверить значения."""

    selected_backend = validate_backend_name(backend or default_loader.requested_backend)
    selected_model = validate_asr_model(model or default_loader.requested_model)
    selected_provider = (onnx_provider or default_loader.requested_provider).strip().lower()
    if selected_provider not in ONNX_PROVIDERS:
        raise ValueError(f"Unsupported ONNX provider: {selected_provider}")
    return AsrSelection(selected_backend, selected_model, selected_provider)


def acquire_request_model_loader(
    default_loader,
    selection: AsrSelection,
    *,
    loader_factory,
) -> tuple[object, bool]:
    """Переиспользовать default loader либо создать изолированный loader задачи."""

    matches_default = (
        selection.backend == default_loader.requested_backend
        and selection.model == default_loader.requested_model
        and selection.onnx_provider == default_loader.requested_provider
    )
    if matches_default:
        return default_loader, False
    return (
        loader_factory(
            requested_backend=selection.backend,
            model_name=selection.model,
            model_revision=selection.model,
            onnx_provider=selection.onnx_provider,
        ),
        True,
    )


def build_processor(
    model_loader,
    stats_manager,
    *,
    logger: Callable | None = None,
    progress_callback: Callable | None = None,
) -> TranscriptionProcessor:
    return TranscriptionProcessor(
        model_loader,
        stats_manager,
        logger=logger,
        progress_callback=progress_callback,
    )
