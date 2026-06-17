"""
Единый источник правды для имён выходных файлов транскрибации.

И процессор (который пишет файлы), и API (который их читает/отдаёт) должны
использовать эти функции, чтобы соглашение об именах не дублировалось и не
рассинхронизировалось (раньше суффикс _timecodes и срез [:-10] хардкодились
в 4+ местах).
"""

import os
from pathlib import Path

# Ключ формата -> (суффикс имени, расширение)
FORMAT_SUFFIX = {
    'txt':                   ('', 'txt'),
    'txt_timecodes':         ('_timecodes', 'txt'),
    'txt_diarize':           ('_diarize', 'txt'),
    'txt_diarize_timecodes': ('_diarize_timecodes', 'txt'),
    'md':                    ('', 'md'),
    'srt':                   ('', 'srt'),
    'vtt':                   ('', 'vtt'),
}


def output_filename(stem: str, fmt: str) -> str:
    """Имя выходного файла для базового имени stem и формата fmt."""
    if fmt not in FORMAT_SUFFIX:
        raise ValueError(f"Неизвестный формат вывода: {fmt}")
    suffix, ext = FORMAT_SUFFIX[fmt]
    return f"{stem}{suffix}.{ext}"


def output_path(output_dir, stem: str, fmt: str) -> str:
    """Полный путь к выходному файлу."""
    return os.path.join(str(output_dir), output_filename(stem, fmt))


def find_result_file(result_dir, stem: str, fmt: str) -> Path | None:
    """Возвращает путь к существующему файлу результата нужного формата либо None.

    Имя детерминировано, поэтому достаточно прямой проверки существования —
    это корректно работает и с кириллицей в именах.
    """
    candidate = Path(result_dir) / output_filename(stem, fmt)
    return candidate if candidate.is_file() else None
