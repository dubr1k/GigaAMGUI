#!/usr/bin/env python3
"""
GigaAM v3 Transcriber - CLI интерфейс
Продвинутый интерактивный командный интерфейс для транскрибации
"""

import os
import sys
import time

# Подавляем предупреждения
import warnings

import click
import questionary
from questionary import Style
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

warnings.filterwarnings("ignore", category=UserWarning)

# Импорты из проекта
from src.cli_support import interactive as cli_interactive
from src.config import ASR_BACKEND, AUDIO_PREPROCESSING_MODE, ONNX_PROVIDER, OUTPUT_FORMATS
from src.core.asr.models import ASR_MODELS
from src.core.model_loader import ModelLoader
from src.core.progress import ProgressEvent
from src.services import transcription_service
from src.utils.audio_converter import ffmpeg_available
from src.utils.logger import setup_logger
from src.utils.processing_stats import ProcessingStats
from src.utils.pyannote_patch import apply_pyannote_patch

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


def process_files_with_progress(
    files: list[str],
    output_dir: str,
    model_loader: ModelLoader,
    stats_manager: ProcessingStats,
    logger: CLILogger,
    output_formats: list[str] | None = None,
    enable_diarization: bool = False,
    num_speakers: int | None = None,
    diarization_backend: str = "pyannote",
    audio_preprocessing_mode: str = AUDIO_PREPROCESSING_MODE,
) -> list[dict]:
    """
    Обрабатывает файлы с отображением прогресса

    Args:
        files: список файлов
        output_dir: директория для результатов
        model_loader: загрузчик модели
        stats_manager: менеджер статистики
        logger: логгер
        output_formats: список форматов вывода (txt, md, srt, vtt, ...)
        enable_diarization: включить диаризацию спикеров
        num_speakers: количество спикеров (если известно)

    Returns:
        список результатов обработки
    """
    output_formats = output_formats or ['txt']
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

        stage_names = {
            "preparing": "Подготовка...",
            "conversion": "Конвертация...",
            "preprocessing": "Анализ и подготовка аудио...",
            "transcription": "Распознавание речи...",
            "diarization": "Диаризация...",
            "export": "Экспорт...",
            "finalizing": "Завершение...",
        }

        batch_state = {"completed": 0.0}
        for filepath in files:
            filename = os.path.basename(filepath)
            current_file_progress = 0.0
            progress.update(
                current_task,
                description=f"[green]{filename}",
                completed=0
            )
            def _normalize_progress_event(event_or_stage, prog=None, *, _filename=filename):
                nonlocal current_file_progress
                if isinstance(event_or_stage, ProgressEvent):
                    event = event_or_stage
                    stage = event.stage
                    file_progress = float(event.file_progress)
                    stage_progress = event.stage_progress
                elif isinstance(event_or_stage, dict):
                    stage = event_or_stage.get("stage", "preparing")
                    file_progress = float(event_or_stage.get("file_progress", 0.0) or 0.0)
                    stage_progress = event_or_stage.get("stage_progress")
                else:
                    stage = str(event_or_stage)
                    file_progress = float(prog or 0.0)
                    stage_progress = None

                file_progress = max(0.0, min(file_progress, 1.0))
                current_file_progress = max(current_file_progress, file_progress)
                task_total = 100
                kwargs = {
                    "description": f"[green]{_filename} — {stage_names.get(stage, stage)}",
                    "completed": int(file_progress * 100),
                }
                if stage_progress is not None:
                    kwargs["total"] = task_total
                else:
                    kwargs["total"] = None

                progress.update(current_task, **kwargs)
                progress.update(
                    main_task,
                    completed=int((batch_state["completed"] + current_file_progress) / len(files) * 100),
                )

                if stage == "finalizing":
                    progress.update(
                        current_task,
                        description=f"[green]{_filename} — {stage_names.get(stage, stage)}"
                    )

            # Процессор
            processor = transcription_service.build_processor(
                model_loader,
                stats_manager,
                logger=lambda msg: logger.debug(msg),
                progress_callback=_normalize_progress_event,
            )

            # Обработка
            result = processor.process_file(
                filepath=filepath,
                output_dir=output_dir,
                file_index=len(results),
                total_files=len(files),
                enable_diarization=enable_diarization,
                diarization_backend=diarization_backend,
                audio_preprocessing_mode=audio_preprocessing_mode,
                num_speakers=num_speakers,
                output_formats=output_formats,
            )

            results.append(result)
            batch_state["completed"] += 1.0 if result['success'] else current_file_progress

            # Сохраняем статистику
            if result['success'] and result['media_duration'] > 0:
                stats_manager.add_processing_record(
                    file_path=result.get('file_path', filepath),
                    file_size=result['file_size'],
                    duration=result['media_duration'],
                    conversion_time=result.get('conversion_time', 0),
                    transcription_time=result.get('transcription_time', 0),
                    success=result['success'],
                )

    return results


def display_results(results: list[dict]):
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
@click.option(
    '--format', '-F', 'formats',
    multiple=True,
    type=click.Choice(list(OUTPUT_FORMATS.keys())),
    help=('Форматы вывода (можно несколько). По умолчанию: txt. '
          'Доступно: ' + ', '.join(OUTPUT_FORMATS.keys()))
)
@click.option(
    '--backend',
    type=click.Choice(["auto", "mlx", "onnx", "pytorch"]),
    default=None,
    help='Режим ASR backend: auto/mlx/onnx/pytorch (по умолчанию берется из ASR_BACKEND)',
)
@click.option(
    '--model',
    type=click.Choice(list(ASR_MODELS)),
    default=None,
    help='Модель ASR (по умолчанию берется из ASR_MODEL)',
)
@click.option(
    '--onnx-provider',
    type=click.Choice(["auto", "cpu", "cuda", "tensorrt", "coreml", "directml"]),
    default=None,
    help='ONNX Runtime execution provider (по умолчанию берется из ONNX_PROVIDER)',
)
@click.option(
    '--diarize/--no-diarize',
    default=False,
    help='Включить диаризацию спикеров (по умолчанию: выключено)'
)
@click.option(
    '--diarization-backend',
    type=click.Choice(["pyannote", "sortformer", "onnx"]),
    default="pyannote",
    show_default=True,
    help='Движок диаризации: ONNX, pyannote или NVIDIA Sortformer v2.1',
)
@click.option(
    '--speakers', '-s',
    type=click.IntRange(min=1),
    default=None,
    help='Количество спикеров для диаризации (если известно)'
)
@click.option(
    '--audio-preprocessing',
    type=click.Choice(["off", "auto", "light", "denoise"]),
    default=AUDIO_PREPROCESSING_MODE,
    show_default=True,
    help='Интеллектуальная подготовка аудио перед распознаванием',
)
def main(
    files, directory, output, interactive, verbose, formats, backend, model, onnx_provider,
    diarize, diarization_backend, speakers, audio_preprocessing,
):
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

    # Проверка токена нужна только для pyannote. Публичный Sortformer
    # загружается без HF_TOKEN.
    if diarize and diarization_backend == "pyannote" and not os.getenv("HF_TOKEN", "").startswith("hf_"):
        console.print(Panel(
            "[bold yellow]⚠ Для диаризации pyannote требуется HF_TOKEN с доступом к pyannote/segmentation-3.0.[/bold yellow]",
            title="Внимание",
            border_style="yellow",
        ))
        sys.exit(1)
    if diarize and diarization_backend == "sortformer" and speakers is not None:
        raise click.UsageError(
            "NVIDIA Sortformer определяет число спикеров автоматически; не используйте --speakers"
        )

    # Предполётная проверка ffmpeg/ffprobe
    if not ffmpeg_available():
        console.print(Panel(
            "[bold red]⚠ ОШИБКА: ffmpeg/ffprobe не найдены в PATH![/bold red]\n\n"
            "Они нужны для конвертации и определения длительности медиа.\n"
            "Установите ffmpeg и убедитесь, что он доступен в PATH.",
            title="❌ Требуется ffmpeg",
            border_style="red"
        ))
        sys.exit(1)

    # Определяем список файлов
    file_list = []

    if directory:
        # Из директории
        file_list = cli_interactive.get_supported_files(directory)
        logger.info(f"Найдено {len(file_list)} файлов в директории: {directory}")
    elif files:
        # Из аргументов
        file_list = list(files)
        logger.info(f"Указано файлов: {len(file_list)}")
    elif interactive:
        # Интерактивный выбор
        logger.info("Интерактивный режим выбора файлов")
        file_list = cli_interactive.select_files_interactive(console, custom_style)
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
        output_dir = cli_interactive.select_output_directory(default_dir, custom_style)
    else:
        output_dir = os.path.dirname(file_list[0]) if file_list else os.getcwd()

    logger.info(f"Результаты будут сохранены в: {output_dir}")

    # Настройка логирования в файл
    file_logger = setup_logger()
    logger.set_file_logger(file_logger)

    # Инициализация компонентов
    logger.info("Загрузка модели GigaAM-v3...")

    with console.status("[bold cyan]Загрузка модели...", spinner="dots"):
        model_loader = ModelLoader(
            requested_backend=backend or ASR_BACKEND,
            model_name=model,
            model_revision=model,
            onnx_provider=onnx_provider or ONNX_PROVIDER,
        )
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
        logger=logger,
        output_formats=list(formats) if formats else ['txt'],
        enable_diarization=diarize,
        diarization_backend=diarization_backend,
        audio_preprocessing_mode=audio_preprocessing,
        num_speakers=speakers,
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
