#!/usr/bin/env python3
"""
Интерактивный CLI для GigaAM v3 Transcriber
Продвинутый командный интерфейс для Linux и macOS
"""

import os
import sys
import warnings
from pathlib import Path

# Подавляем предупреждения
warnings.filterwarnings("ignore", category=UserWarning, module="pyannote")
warnings.filterwarnings("ignore", category=UserWarning, module="speechbrain")
warnings.filterwarnings("ignore", category=UserWarning, module="torchaudio")

# Применяем патч для pyannote.audio перед импортом
from src.utils.pyannote_patch import apply_pyannote_patch
apply_pyannote_patch()

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from rich import box
from rich.prompt import Prompt, Confirm
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
    TimeRemainingColumn,
    TimeElapsedColumn
)

from src.cli import CLIInterface


def print_banner(console: Console):
    """Выводит красивый баннер приложения"""
    banner = Text()
    banner.append("╔═══════════════════════════════════════════════════════════╗\n", style="bold cyan")
    banner.append("║  ", style="bold cyan")
    banner.append("🎙️  GigaAM v3 Transcriber - CLI Edition", style="bold yellow")
    banner.append("  🎙️  ║\n", style="bold cyan")
    banner.append("╠═══════════════════════════════════════════════════════════╣\n", style="bold cyan")
    banner.append("║  ", style="bold cyan")
    banner.append("Транскрибация аудио/видео → Текст", style="bold white")
    banner.append("                  ║\n", style="bold cyan")
    banner.append("║  ", style="bold cyan")
    banner.append("Powered by Sber GigaAM-v3", style="italic bright_black")
    banner.append("                            ║\n", style="bold cyan")
    banner.append("╚═══════════════════════════════════════════════════════════╝", style="bold cyan")
    
    console.print(banner)
    console.print()


def main():
    """Главная функция CLI приложения"""
    console = Console()
    
    try:
        # Очистка экрана
        console.clear()
        
        # Баннер
        print_banner(console)
        
        # Инициализация CLI интерфейса
        cli = CLIInterface(console)
        
        # Проверка конфигурации
        if not cli.check_configuration():
            console.print("\n[bold red]✗ Ошибка конфигурации![/bold red]")
            console.print("\n[yellow]Инструкция по настройке:[/yellow]")
            console.print("1. Зарегистрируйтесь на https://huggingface.co")
            console.print("2. Создайте токен: https://huggingface.co/settings/tokens")
            console.print("3. Примите условия: https://huggingface.co/pyannote/segmentation-3.0")
            console.print("4. Создайте файл .env в корне проекта")
            console.print("5. Добавьте: HF_TOKEN=ваш_токен_здесь\n")
            
            if not Confirm.ask("[yellow]Продолжить без токена?[/yellow] (работа будет ограничена)"):
                return
        
        # Главный цикл приложения
        cli.run()
        
    except KeyboardInterrupt:
        console.print("\n\n[yellow]⚠️  Работа прервана пользователем[/yellow]")
        sys.exit(0)
    except Exception as e:
        console.print(f"\n[bold red]✗ Критическая ошибка: {str(e)}[/bold red]")
        import traceback
        console.print(f"[dim]{traceback.format_exc()}[/dim]")
        sys.exit(1)


if __name__ == "__main__":
    main()

