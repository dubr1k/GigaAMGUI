"""Интерактивный выбор файлов и директории для CLI (вынесено из cli.py).

Функции принимают `console`/`style` явно, чтобы не зависеть от module-глобалей cli.py.
Поведение сохранено 1:1.
"""
from __future__ import annotations

import os
from pathlib import Path

import questionary

from src.config import SUPPORTED_FORMATS


def get_supported_files(directory: str) -> list[str]:
    """Список поддерживаемых медиа-файлов в директории."""
    extensions = SUPPORTED_FORMATS[1].split()
    supported_files = []

    for ext in extensions:
        ext_clean = ext.replace('*', '')
        supported_files.extend(Path(directory).glob(f"*{ext_clean}"))

    return [str(f) for f in sorted(supported_files)]


def select_files_interactive(console, style) -> list[str]:
    """Интерактивный выбор файлов (директория целиком или отдельные файлы)."""
    mode = questionary.select(
        "Как вы хотите выбрать файлы?",
        choices=[
            "📁 Выбрать директорию (обработать все файлы)",
            "📄 Указать отдельные файлы",
            "❌ Отмена"
        ],
        style=style
    ).ask()

    if not mode or "Отмена" in mode:
        return []

    if "директорию" in mode:
        directory = questionary.path(
            "Введите путь к директории:",
            style=style
        ).ask()

        if not directory or not os.path.isdir(directory):
            console.print("[red]Директория не найдена![/red]")
            return []

        files = get_supported_files(directory)

        if not files:
            console.print("[yellow]В директории не найдено поддерживаемых файлов[/yellow]")
            return []

        console.print(f"\n[cyan]Найдено файлов: {len(files)}[/cyan]")

        for i, f in enumerate(files[:10], 1):
            console.print(f"  {i}. {os.path.basename(f)}")

        if len(files) > 10:
            console.print(f"  ... и еще {len(files) - 10} файлов")

        confirm = questionary.confirm(
            f"\nОбработать все {len(files)} файлов?",
            default=True,
            style=style
        ).ask()

        return files if confirm else []

    else:
        files = []

        while True:
            filepath = questionary.path(
                f"Файл {len(files) + 1} (Enter для завершения):",
                style=style
            ).ask()

            if not filepath:
                break

            if not os.path.isfile(filepath):
                console.print("[red]Файл не найден![/red]")
                continue

            files.append(filepath)
            console.print(f"[green]✓[/green] Добавлен: {os.path.basename(filepath)}")

            if not questionary.confirm("Добавить еще файл?", default=False, style=style).ask():
                break

        return files


def select_output_directory(default: str | None, style) -> str:
    """Выбор директории для сохранения результатов."""
    if default:
        use_default = questionary.confirm(
            "Сохранить результаты в ту же директорию?",
            default=True,
            style=style
        ).ask()

        if use_default:
            return default

    output_dir = questionary.path(
        "Директория для сохранения результатов:",
        style=style
    ).ask()

    if not output_dir:
        output_dir = default or os.getcwd()

    os.makedirs(output_dir, exist_ok=True)

    return output_dir
