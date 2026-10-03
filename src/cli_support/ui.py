"""Вывод CLI: логгер с цветными метками, баннер и таблица результатов (вынесено из cli.py).

Как и interactive.py, функции получают `console` явно и не зависят от
глобалей cli.py.
"""
from __future__ import annotations

import os

from rich import box
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table


class CLILogger:
    """Логгер для CLI с красивым выводом"""

    def __init__(self, console: Console, verbose: bool = False):
        self.console = console
        self.verbose = verbose
        self.file_logger = None

    def set_file_logger(self, logger):
        """Устанавливает файловый логгер"""
        self.file_logger = logger

    def info(self, message: str):
        """Информационное сообщение"""
        self.console.print(f"[cyan]ℹ[/cyan] {message}")
        if self.file_logger:
            self.file_logger.info(message)

    def success(self, message: str):
        """Успешное сообщение"""
        self.console.print(f"[green]✓[/green] {message}")
        if self.file_logger:
            self.file_logger.info(message)

    def warning(self, message: str):
        """Предупреждение"""
        self.console.print(f"[yellow]⚠[/yellow] {message}")
        if self.file_logger:
            self.file_logger.warning(message)

    def error(self, message: str):
        """Ошибка"""
        self.console.print(f"[red]✗[/red] {message}")
        if self.file_logger:
            self.file_logger.error(message)

    def debug(self, message: str):
        """Отладочное сообщение"""
        if self.verbose:
            self.console.print(f"[dim]{message}[/dim]")
        if self.file_logger:
            self.file_logger.debug(message)


def print_banner(console: Console):
    """Выводит красивый баннер приложения"""
    banner = """
    ╔═══════════════════════════════════════════════════════════╗
    ║                                                           ║
    ║              [bold cyan]GigaAM v3 Transcriber[/bold cyan]                 ║
    ║                                                           ║
    ║        [dim]Продвинутая транскрибация русской речи[/dim]         ║
    ║                 [dim]Powered by Sber AI[/dim]                    ║
    ║                                                           ║
    ╚═══════════════════════════════════════════════════════════╝
    """
    console.print(banner)


def display_results(console: Console, results: list[dict]):
    """
    Отображает результаты обработки в виде таблицы

    Args:
        console: куда печатать
        results: список результатов
    """
    console.print("\n")

    # Создаем таблицу
    table = Table(
        title="📊 Результаты обработки",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan"
    )

    table.add_column("№", style="dim", width=4, justify="right")
    table.add_column("Файл", style="cyan")
    table.add_column("Статус", justify="center")
    table.add_column("Время", justify="right")
    table.add_column("Длительность", justify="right")

    total_time = 0
    success_count = 0

    for i, result in enumerate(results, 1):
        filename = os.path.basename(result['file_path'])

        # Сокращаем длинные имена
        if len(filename) > 40:
            filename = filename[:37] + "..."

        status = "[green]✓ Успех[/green]" if result['success'] else "[red]✗ Ошибка[/red]"

        processing_time = f"{result['total_time']:.1f}с"

        duration = result.get('media_duration', 0)
        duration_str = f"{int(duration//60)}:{int(duration%60):02d}" if duration > 0 else "-"

        table.add_row(
            str(i),
            filename,
            status,
            processing_time,
            duration_str
        )

        total_time += result['total_time']
        if result['success']:
            success_count += 1

    console.print(table)

    # Почему не удалось: причина от процессора (result['error']), первая строка.
    # escape — в тексте ошибок бывают [скобки] ([Errno 2] …), а не разметка rich.
    for result in results:
        reason = (result.get('error') or "").strip()
        if not result['success'] and reason:
            name = os.path.basename(result['file_path'])
            console.print(f"[red]✗[/red] {escape(name)}: {escape(reason.splitlines()[0])}")

    # Итоговая статистика
    summary = Panel(
        f"[bold green]Успешно:[/bold green] {success_count}/{len(results)} файлов\n"
        f"[bold cyan]Общее время:[/bold cyan] {total_time:.1f}с ({total_time/60:.1f} мин)",
        title="📈 Итого",
        border_style="green"
    )
    console.print("\n")
    console.print(summary)
