"""Единая фабрика TranscriptionProcessor.

Все четыре поверхности (api/web/cli/gui) собирали процессор вручную одинаковым
конструктором. Фабрика убирает это дублирование, не меняя сигнатуру процессора:
управление форматами/диаризацией остаётся за каждой поверхностью через process_file.
"""
from __future__ import annotations

from collections.abc import Callable

from src.core.processor import TranscriptionProcessor


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
