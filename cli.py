#!/usr/bin/env python3
"""
GigaAM v3 Transcriber - CLI интерфейс
Продвинутый интерактивный командный интерфейс для транскрибации
"""

import os
import sys
import time
from pathlib import Path
from typing import List, Optional

import click
import questionary
from questionary import Style
from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    BarColumn,
    TextColumn,
    TimeRemainingColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box

# Подавляем предупреждения
import warnings
warnings.filterwarnings("ignore", category=UserWarning)

# Импорты из проекта
from src.utils.pyannote_patch import apply_pyannote_patch
from src.core.model_loader import ModelLoader
from src.core.processor import TranscriptionProcessor
from src.utils.processing_stats import ProcessingStats
from src.utils.logger import setup_logger
from src.config import HF_TOKEN, SUPPORTED_FORMATS

# Применяем патч
apply_pyannote_patch()

# Инициализация
console = Console()

# Стиль для questionary
custom_style = Style([
    ('qmark', 'fg:#673ab7 bold'),           # Вопросительный знак
    ('question', 'bold'),                    # Вопрос
    ('answer', 'fg:#f44336 bold'),          # Ответ
    ('pointer', 'fg:#673ab7 bold'),         # Указатель
    ('highlighted', 'fg:#673ab7 bold'),     # Выделенное
    ('selected', 'fg:#cc5454'),             # Выбранное
    ('separator', 'fg:#cc5454'),            # Разделитель
    ('instruction', ''),                     # Инструкция
    ('text', ''),                           # Текст
])


class CLILogger:
    """Логгер для CLI с красивым выводом"""
    
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.file_logger = None
        
    def set_file_logger(self, logger):
        """Устанавливает файловый логгер"""
        self.file_logger = logger
    
    def info(self, message: str):
        """Информационное сообщение"""
        console.print(f"[cyan]ℹ[/cyan] {message}")
        if self.file_logger:
            self.file_logger.info(message)
    
    def success(self, message: str):
        """Успешное сообщение"""
        console.print(f"[green]✓[/green] {message}")
        if self.file_logger:
            self.file_logger.info(message)
    
    def warning(self, message: str):
        """Предупреждение"""
        console.print(f"[yellow]⚠[/yellow] {message}")
        if self.file_logger:
            self.file_logger.warning(message)
    
    def error(self, message: str):
        """Ошибка"""
        console.print(f"[red]✗[/red] {message}")
        if self.file_logger:
            self.file_logger.error(message)
    
    def debug(self, message: str):
        """Отладочное сообщение"""
        if self.verbose:
            console.print(f"[dim]{message}[/dim]")
        if self.file_logger:
            self.file_logger.debug(message)


def print_banner():
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


def get_supported_files(directory: str) -> List[str]:
    """
    Получает список поддерживаемых медиа-файлов в директории
    
    Args:
        directory: путь к директории
        
    Returns:
        список путей к файлам
    """
    extensions = SUPPORTED_FORMATS[1].split()
    supported_files = []
    
    for ext in extensions:
        ext_clean = ext.replace('*', '')
        supported_files.extend(Path(directory).glob(f"*{ext_clean}"))
    
    return [str(f) for f in sorted(supported_files)]


def select_files_interactive() -> List[str]:
    """
    Интерактивный выбор файлов
    
    Returns:
        список выбранных файлов
    """
    # Выбор режима
    mode = questionary.select(
        "Как вы хотите выбрать файлы?",
        choices=[
            "📁 Выбрать директорию (обработать все файлы)",
            "📄 Указать отдельные файлы",
            "❌ Отмена"
        ],
        style=custom_style
    ).ask()
    
    if not mode or "Отмена" in mode:
        return []
    
    if "директорию" in mode:
        # Выбор директории
        directory = questionary.path(
            "Введите путь к директории:",
            style=custom_style
        ).ask()
        
        if not directory or not os.path.isdir(directory):
            console.print("[red]Директория не найдена![/red]")
            return []
        
        files = get_supported_files(directory)
        
        if not files:
            console.print("[yellow]В директории не найдено поддерживаемых файлов[/yellow]")
            return []
        
        console.print(f"\n[cyan]Найдено файлов: {len(files)}[/cyan]")
        
        # Показываем список
        for i, f in enumerate(files[:10], 1):
            console.print(f"  {i}. {os.path.basename(f)}")
        
        if len(files) > 10:
            console.print(f"  ... и еще {len(files) - 10} файлов")
        
        confirm = questionary.confirm(
            f"\nОбработать все {len(files)} файлов?",
            default=True,
            style=custom_style
        ).ask()
        
        return files if confirm else []
    
    else:
        # Указание отдельных файлов
        files = []
        
        while True:
            filepath = questionary.path(
                f"Файл {len(files) + 1} (Enter для завершения):",
                style=custom_style
            ).ask()
            
            if not filepath:
                break
            
            if not os.path.isfile(filepath):
                console.print("[red]Файл не найден![/red]")
                continue
            
            files.append(filepath)
            console.print(f"[green]✓[/green] Добавлен: {os.path.basename(filepath)}")
            
            if not questionary.confirm("Добавить еще файл?", default=False, style=custom_style).ask():
                break
        
        return files


def select_output_directory(default: Optional[str] = None) -> str:
    """
    Выбор директории для сохранения результатов
    
    Args:
        default: директория по умолчанию
        
    Returns:
        путь к директории
    """
    if default:
        use_default = questionary.confirm(
            f"Сохранить результаты в ту же директорию?",
            default=True,
            style=custom_style
        ).ask()
        
        if use_default:
            return default
    
    output_dir = questionary.path(
        "Директория для сохранения результатов:",
        style=custom_style
    ).ask()
    
    if not output_dir:
        output_dir = default or os.getcwd()
    
    # Создаем директорию если не существует
    os.makedirs(output_dir, exist_ok=True)
    
    return output_dir


def process_files_with_progress(
    files: List[str],
    output_dir: str,
    model_loader: ModelLoader,
    stats_manager: ProcessingStats,
    logger: CLILogger
) -> List[dict]:
    """
    Обрабатывает файлы с отображением прогресса
    
    Args:
        files: список файлов
        output_dir: директория для результатов
        model_loader: загрузчик модели
        stats_manager: менеджер статистики
        logger: логгер
        
    Returns:
        список результатов обработки
    """
    results = []
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console
    ) as progress:
        
        # Общий прогресс
        main_task = progress.add_task(
            "[cyan]Обработка файлов...",
            total=len(files)
        )
        
        # Текущий файл
        current_task = progress.add_task(
            "[green]Подготовка...",
            total=100
        )
        
        for i, filepath in enumerate(files):
            filename = os.path.basename(filepath)
            progress.update(
                current_task,
                description=f"[green]{filename}",
                completed=0
            )
            
            # Callback для обновления прогресса
            def progress_callback(stage: str, prog: float):
                if stage == 'conversion':
                    progress.update(current_task, completed=int(prog * 20))
                elif stage == 'transcription':
                    progress.update(current_task, completed=20 + int(prog * 80))
            
            # Процессор
            processor = TranscriptionProcessor(
                model_loader=model_loader,
                stats_manager=stats_manager,
                logger=lambda msg: logger.debug(msg),
                progress_callback=progress_callback
            )
            
            # Обработка
            result = processor.process_file(
                filepath=filepath,
                output_dir=output_dir,
                file_index=i,
                total_files=len(files)
            )
            
            results.append(result)
            
            # Обновляем прогресс
            progress.update(main_task, advance=1)
            progress.update(current_task, completed=100)
            
            # Сохраняем статистику
            if result['success'] and result['media_duration'] > 0:
                stats_manager.add_record(
                    file_size=result['file_size'],
                    media_duration=result['media_duration'],
                    processing_time=result['total_time']
                )
    
    return results


def display_results(results: List[dict]):
    """
    Отображает результаты обработки в виде таблицы
    
    Args:
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
    
    # Итоговая статистика
    summary = Panel(
        f"[bold green]Успешно:[/bold green] {success_count}/{len(results)} файлов\n"
        f"[bold cyan]Общее время:[/bold cyan] {total_time:.1f}с ({total_time/60:.1f} мин)",
        title="📈 Итого",
        border_style="green"
    )
    console.print("\n")
    console.print(summary)


@click.command()
@click.option(
    '--files', '-f',
    multiple=True,
    type=click.Path(exists=True),
    help='Пути к файлам для обработки'
)
@click.option(
    '--directory', '-d',
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    help='Директория с файлами для обработки'
)
@click.option(
    '--output', '-o',
    type=click.Path(file_okay=False, dir_okay=True),
    help='Директория для сохранения результатов'
)
@click.option(
    '--interactive/--no-interactive', '-i/-n',
    default=True,
    help='Интерактивный режим (по умолчанию: включен)'
)
@click.option(
    '--verbose', '-v',
    is_flag=True,
    help='Подробный вывод'
)
def main(files, directory, output, interactive, verbose):
    """
    🎙️ GigaAM v3 Transcriber - CLI
    
    Продвинутая транскрибация аудио и видео файлов на русском языке.
    
    Примеры использования:
    
    \b
    # Интерактивный режим
    python cli.py
    
    \b
    # Обработать конкретные файлы
    python cli.py -f audio1.mp3 -f audio2.wav -o /path/to/output
    
    \b
    # Обработать все файлы в директории
    python cli.py -d /path/to/directory -o /path/to/output
    
    \b
    # Неинтерактивный режим с подробным выводом
    python cli.py -d /path/to/dir -n -v
    """
    
    # Баннер
    print_banner()
    
    # Инициализация логгера
    logger = CLILogger(verbose=verbose)
    
    # Проверка токена
    if not HF_TOKEN or not HF_TOKEN.startswith("hf_"):
        console.print(Panel(
            "[bold red]⚠ ОШИБКА: Не настроен HuggingFace токен![/bold red]\n\n"
            "Для работы приложения требуется токен HuggingFace.\n\n"
            "[cyan]Шаги для настройки:[/cyan]\n"
            "1. Зарегистрируйтесь на https://huggingface.co\n"
            "2. Создайте токен: https://huggingface.co/settings/tokens\n"
            "3. Примите условия: https://huggingface.co/pyannote/segmentation-3.0\n"
            "4. Добавьте токен в файл .env",
            title="❌ Требуется настройка",
            border_style="red"
        ))
        sys.exit(1)
    
    logger.success("HuggingFace токен найден")
    
    # Определяем список файлов
    file_list = []
    
    if directory:
        # Из директории
        file_list = get_supported_files(directory)
        logger.info(f"Найдено {len(file_list)} файлов в директории: {directory}")
    elif files:
        # Из аргументов
        file_list = list(files)
        logger.info(f"Указано файлов: {len(file_list)}")
    elif interactive:
        # Интерактивный выбор
        logger.info("Интерактивный режим выбора файлов")
        file_list = select_files_interactive()
    else:
        logger.error("Не указаны файлы для обработки!")
        logger.info("Используйте --help для справки")
        sys.exit(1)
    
    if not file_list:
        logger.warning("Нет файлов для обработки")
        sys.exit(0)
    
    # Определяем директорию вывода
    if output:
        output_dir = output
    elif interactive and not output:
        default_dir = os.path.dirname(file_list[0]) if file_list else None
        output_dir = select_output_directory(default_dir)
    else:
        output_dir = os.path.dirname(file_list[0]) if file_list else os.getcwd()
    
    logger.info(f"Результаты будут сохранены в: {output_dir}")
    
    # Настройка логирования в файл
    file_logger = setup_logger()
    logger.set_file_logger(file_logger)
    
    # Инициализация компонентов
    logger.info("Загрузка модели GigaAM-v3...")
    
    with console.status("[bold cyan]Загрузка модели...", spinner="dots"):
        model_loader = ModelLoader()
        success = model_loader.load_model(logger=lambda msg: logger.debug(msg))
    
    if not success:
        logger.error("Не удалось загрузить модель!")
        sys.exit(1)
    
    logger.success("Модель успешно загружена")
    
    # Менеджер статистики
    stats_manager = ProcessingStats()
    
    # Подтверждение перед обработкой
    if interactive:
        console.print("\n")
        if not questionary.confirm(
            f"Начать обработку {len(file_list)} файлов?",
            default=True,
            style=custom_style
        ).ask():
            logger.warning("Обработка отменена пользователем")
            sys.exit(0)
    
    console.print("\n")
    logger.info("Начало обработки...")
    
    # Обработка файлов
    start_time = time.time()
    results = process_files_with_progress(
        files=file_list,
        output_dir=output_dir,
        model_loader=model_loader,
        stats_manager=stats_manager,
        logger=logger
    )
    total_time = time.time() - start_time
    
    # Отображение результатов
    display_results(results)
    
    # Финальное сообщение
    success_count = sum(1 for r in results if r['success'])
    
    if success_count == len(results):
        logger.success(f"Все файлы успешно обработаны за {total_time:.1f}с!")
    elif success_count > 0:
        logger.warning(f"Обработано {success_count}/{len(results)} файлов за {total_time:.1f}с")
    else:
        logger.error("Не удалось обработать ни одного файла")
    
    logger.info(f"Результаты сохранены в: {output_dir}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n\n[yellow]⚠ Обработка прервана пользователем[/yellow]")
        sys.exit(0)
    except Exception as e:
        console.print(f"\n\n[red]❌ Критическая ошибка: {str(e)}[/red]")
        import traceback
        traceback.print_exc()
        sys.exit(1)

