"""Сохранение результатов транскрибации в выбранных форматах."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from ..utils.atomic_json import write_text_atomic
from ..utils.output_naming import output_path

# Рендер формата: текст файла или None — «этот файл не создаётся».
Renderer = Callable[[], "str | None"]


def resolve_output_formats(
    output_formats: Iterable[str] | None,
    *,
    diarization_applied: bool,
) -> list[str]:
    """Итоговый список форматов файла.

    ``None`` — значение по умолчанию (TXT). Если диаризация действительно
    сработала, добавляется хотя бы один явно диаризованный файл: обычный TXT
    намеренно без меток спикеров и раньше создавал впечатление, что функция не
    работает. Явный пустой список уважается: MCP и OpenAI-совместимый API
    собирают ответ из ``result['utterances']`` и файлов не ждут.
    """
    if output_formats is None:
        formats = ["txt"]
    else:
        formats = list(output_formats)
        if not formats:
            return []
    if diarization_applied and "txt_diarize" not in formats:
        formats.append("txt_diarize")
    if (
        diarization_applied
        and "txt_timecodes" in formats
        and "txt_diarize_timecodes" not in formats
    ):
        formats.append("txt_diarize_timecodes")
    return formats


@dataclass
class ExportResult:
    saved_files: list[str] = field(default_factory=list)
    # Формат → причина: один сломанный формат не отменяет остальные.
    errors: dict[str, str] = field(default_factory=dict)


def write_outputs(
    output_dir: str,
    stem: str,
    formats: Iterable[str],
    renderers: dict[str, Renderer],
    *,
    on_format_done: Callable[[int, int], None] | None = None,
) -> ExportResult:
    """Отрендерить и атомарно записать каждый формат независимо.

    Раньше исключение в одном формате (рендер SRT, нехватка места) уводило
    весь файл в «не удалось обработать»: остальные форматы не сохранялись, а
    запись открытым на запись файлом оставляла обрезанный результат вместо
    прежнего.
    """
    selected = list(formats)
    total = max(len(selected), 1)
    result = ExportResult()
    for index, fmt in enumerate(selected, start=1):
        renderer = renderers.get(fmt)
        try:
            if renderer is None:
                raise ValueError(f"Неизвестный формат вывода: {fmt}")
            content = renderer()
            if content is not None:
                path = output_path(output_dir, stem, fmt)
                write_text_atomic(path, content)
                result.saved_files.append(path)
        except Exception as exc:  # noqa: BLE001 — причина уходит пользователю
            result.errors[fmt] = f"{type(exc).__name__}: {exc}"
        if on_format_done is not None:
            on_format_done(index, total)
    return result
