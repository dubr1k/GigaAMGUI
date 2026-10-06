"""
Единый источник правды для имён выходных файлов транскрибации.

И процессор (который пишет файлы), и API (который их читает/отдаёт) должны
использовать эти функции, чтобы соглашение об именах не дублировалось и не
рассинхронизировалось (раньше суффикс _timecodes и срез [:-10] хардкодились
в 4+ местах).
"""

import os
import unicodedata
from collections import defaultdict
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


def normalized_output_stem(path: str | os.PathLike[str]) -> str:
    """Normalize a media stem for collision checks across filesystems/locales."""
    stem = Path(path).stem
    decomposed = unicodedata.normalize("NFKD", stem)
    without_marks = "".join(char for char in decomposed if not unicodedata.combining(char))
    return unicodedata.normalize("NFKC", without_marks).casefold()


def find_output_collisions(
    input_paths: list[str] | tuple[str, ...],
    output_dir: str | os.PathLike[str] | None,
) -> list[list[str]]:
    """Return groups that would write the same deterministic result names."""
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    shared_dir = os.path.abspath(os.fspath(output_dir)) if output_dir else None
    for raw_path in input_paths:
        path = os.path.abspath(os.fspath(raw_path))
        target_dir = shared_dir or os.path.dirname(path)
        key = (os.path.normcase(target_dir), normalized_output_stem(path))
        groups[key].append(os.fspath(raw_path))
    return [paths for paths in groups.values() if len(paths) > 1]


def find_result_file(result_dir, stem: str, fmt: str) -> Path | None:
    """Возвращает путь к существующему файлу результата нужного формата либо None.

    Имя детерминировано, поэтому достаточно прямой проверки существования —
    это корректно работает и с кириллицей в именах.
    """
    candidate = Path(result_dir) / output_filename(stem, fmt)
    return candidate if candidate.is_file() else None
