"""Фоновые задачи веб-панели: транскрибация загруженного файла и загрузка по URL.

Обе работают на записи в `registry`: двигают статус/прогресс, пишут журнал,
сохраняют индекс; тяжёлая часть (процессор, yt-dlp) идёт в executor под
общим с /mcp семафором `state.processing_semaphore`. Задачу могут удалить,
пока поток ещё работает, — тогда финальную уборку делает выход отсюда
(`registry.finalize_deleted`).
"""
from __future__ import annotations

import asyncio
import shutil
import traceback
from datetime import datetime
from pathlib import Path

from src.config import AUDIO_PREPROCESSING_MODE, OUTPUT_FORMATS
from src.core.subtitles import SubtitleOptions
from src.services import file_policy, mcp_backend, transcription_service
from src.utils.atomic_json import save_json_atomic
from src.utils.media_downloader import MediaDownloader
from src.utils.output_naming import output_filename
from src.utils.time_formatter import TimeFormatter
from web.state import state
from web.task_registry import registry

time_formatter = TimeFormatter()

# Ссылки на фоновые задачи: цикл событий хранит на них только слабые ссылки, и
# задача без своей ссылки может быть собрана сборщиком мусора посреди работы.
background_jobs: set[asyncio.Task] = set()


def start_background(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    background_jobs.add(task)
    task.add_done_callback(background_jobs.discard)
    return task


async def process_transcription(
    task_id: str,
    file_path: Path,
    filename: str,
    output_formats: list[str],
    enable_diarization: bool,
    diarization_backend: str,
    num_speakers: int | None,
    asr_selection: transcription_service.AsrSelection | None = None,
    subtitle_options: SubtitleOptions | None = None,
):
    """Фоновая обработка транскрибации."""
    subtitle_options = subtitle_options or SubtitleOptions()
    if state.processing_semaphore is None:
        raise RuntimeError("Семафор обработки не инициализирован")

    # Проверка loader-а живёт внутри try ниже: подняв её сюда, мы бы убили
    # фоновую задачу до создания обработчика ошибок, и запись в registry.tasks
    # навсегда осталась бы в статусе pending.
    request_loader = state.model_loader
    owns_loader = False
    async with state.processing_semaphore:
        try:
            if state.model_loader is None:
                raise RuntimeError("ASR model loader не инициализирован")
            asr_selection = asr_selection or transcription_service.normalize_asr_selection(
                state.model_loader
            )
            if not file_path.exists() or task_id not in registry.tasks:
                return

            registry.tasks[task_id].update({
                'status': 'processing',
                'started_at': datetime.now().isoformat(),
                'progress': 5,
                'stage_progress': 0.0,
                'processed_seconds': 0.0,
                'total_seconds': None,
                'progress_indeterminate': False,
                'stage': 'Подготовка...',
                'message': 'Обработка началась',
            })
            registry.persist()
            registry.log(task_id, f"Начало обработки: {filename}")

            output_dir = registry.result_dir(task_id)
            output_dir.mkdir(exist_ok=True)

            def progress_callback(event_or_stage, progress: float | None = None):
                task = registry.tasks.get(task_id)
                if task is None:
                    return

                stage = None
                stage_progress = None
                processed_seconds = None
                total_seconds = None

                if isinstance(event_or_stage, dict):
                    stage = event_or_stage.get('stage')
                    stage_progress = event_or_stage.get('stage_progress')
                    processed_seconds = event_or_stage.get('processed_seconds')
                    total_seconds = event_or_stage.get('total_seconds')
                    file_progress = event_or_stage.get('file_progress')
                elif hasattr(event_or_stage, 'stage'):
                    stage = getattr(event_or_stage, 'stage', None)
                    stage_progress = getattr(event_or_stage, 'stage_progress', None)
                    processed_seconds = getattr(event_or_stage, 'processed_seconds', None)
                    total_seconds = getattr(event_or_stage, 'total_seconds', None)
                    file_progress = getattr(event_or_stage, 'file_progress', None)
                else:
                    stage = event_or_stage
                    file_progress = progress

                if file_progress is None:
                    file_progress = task.get('progress', 0) / 100

                file_progress = max(0.0, min(float(file_progress), 1.0))
                stage_names = {
                    'preparing': 'Подготовка...',
                    'conversion': 'Конвертация...',
                    'preprocessing': 'Анализ и подготовка аудио...',
                    'transcription': 'Распознавание речи...',
                    'diarization': 'Диаризация...',
                    'export': 'Экспорт...',
                    'finalizing': 'Завершение...',
                }
                task['progress'] = int(file_progress * 100)
                if stage:
                    task['stage'] = stage_names.get(stage, stage)
                task['stage_progress'] = stage_progress
                task['processed_seconds'] = processed_seconds
                task['total_seconds'] = total_seconds
                task['progress_indeterminate'] = stage_progress is None

            def logger(msg: str):
                registry.log(task_id, msg)

            if state.model_loader is None:
                raise RuntimeError("ASR model loader не инициализирован")
            request_loader, owns_loader = transcription_service.acquire_request_model_loader(
                state.model_loader,
                asr_selection,
                loader_factory=state.loader_factory,
            )
            if owns_loader:
                loop = asyncio.get_running_loop()
                loaded = await loop.run_in_executor(None, lambda: request_loader.load_model(logger=logger))
                if not loaded:
                    raise RuntimeError("Не удалось загрузить выбранный ASR backend")

            processor = transcription_service.build_processor(
                request_loader,
                state.stats_manager,
                logger=logger,
                progress_callback=progress_callback,
            )

            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                lambda: processor.process_file(
                    str(file_path),
                    str(output_dir),
                    0,
                    1,
                    filename,
                    output_formats=output_formats if output_formats else ['txt', 'txt_timecodes'],
                    enable_diarization=enable_diarization,
                    diarization_backend=diarization_backend,
                    audio_preprocessing_mode=AUDIO_PREPROCESSING_MODE,
                    num_speakers=num_speakers,
                    subtitle_options=subtitle_options,
                ),
            )

            if not result['success']:
                # Причину провала процессор кладёт в result['error']; старые версии её не дают
                raise Exception(mcp_backend.failure_reason(result) or "Обработка не удалась")

            if task_id not in registry.tasks:
                if task_id in registry.deleted:
                    registry.finalize_deleted(task_id, filename)
                return

            if task_id in registry.deleted:
                registry.finalize_deleted(task_id, filename)
                return

            # Собираем результаты
            stem = Path(filename).stem
            saved_files = result.get('saved_files', [])
            result_files = []
            for sf in saved_files:
                p = Path(sf)
                if p.exists():
                    result_files.append({
                        'name': p.name,
                        'path': str(p),
                        'size': p.stat().st_size,
                        'format': _detect_format(p.name, stem),
                    })

            registry.tasks[task_id].update({
                'status': 'completed',
                'completed_at': datetime.now().isoformat(),
                'progress': 100,
                'stage_progress': 1.0,
                'processed_seconds': result.get('media_duration'),
                'total_seconds': result.get('media_duration'),
                'progress_indeterminate': False,
                'stage': 'Готово',
                'message': 'Транскрибация завершена',
                'result_files': result_files,
                'processing_time': result['total_time'],
                'media_duration': result.get('media_duration', 0),
                'audio_preprocessing': result.get('audio_preprocessing'),
                'asr_diagnostics': request_loader.diagnostics(),
            })
            registry.persist()
            registry.log(task_id, f"Обработка завершена за {time_formatter.format_duration(result['total_time'])}")

            if task_id in registry.deleted or task_id not in registry.tasks:
                if task_id in registry.deleted:
                    registry.finalize_deleted(task_id, filename)
                return

            # Сохраняем meta.json
            try:
                save_json_atomic(str(output_dir / "meta.json"), {
                    'task_id': task_id,
                    'filename': filename,
                    'file_size': registry.tasks[task_id].get('file_size', 0),
                    'created_at': registry.tasks[task_id].get('created_at'),
                    'started_at': registry.tasks[task_id].get('started_at'),
                    'completed_at': registry.tasks[task_id].get('completed_at'),
                    'output_formats': output_formats,
                    'enable_diarization': enable_diarization,
                    'diarization_backend': diarization_backend,
                    'num_speakers': num_speakers,
                    'subtitle_options': {
                        'sentence_split': subtitle_options.sentence_split,
                        'max_line_count': subtitle_options.max_line_count,
                        'max_line_width': subtitle_options.max_line_width,
                    },
                    **asr_selection.as_dict(),
                    'asr_diagnostics': request_loader.diagnostics(),
                    'user': registry.tasks[task_id].get('user'),
                    'processing_time': result['total_time'],
                    'media_duration': result.get('media_duration', 0),
                    'audio_preprocessing_mode': AUDIO_PREPROCESSING_MODE,
                    'audio_preprocessing': result.get('audio_preprocessing'),
                })
            except Exception:
                pass

        except Exception as e:
            registry.log(task_id, f"Ошибка: {e}")
            registry.log(task_id, traceback.format_exc())
            if task_id in registry.tasks:
                registry.tasks[task_id].update({
                    'status': 'failed',
                    'completed_at': datetime.now().isoformat(),
                    'stage': 'Ошибка',
                    'message': str(e),
                })
            registry.persist()

        finally:
            if owns_loader and request_loader is not None:
                request_loader.unload()
            task_status = registry.tasks.get(task_id, {}).get('status', 'unknown')
            if task_status == 'completed' and file_path.exists():
                try:
                    file_path.unlink()
                except OSError:
                    pass
            if task_id in registry.deleted:
                registry.finalize_deleted(task_id, filename)


def _detect_format(filename: str, stem: str) -> str:
    """Определяет формат вывода по имени файла."""
    for fmt, _label in OUTPUT_FORMATS.items():
        expected = output_filename(stem, fmt)
        if filename == expected:
            return fmt
    return 'unknown'


async def download_and_process(
    task_id: str,
    url: str,
    output_formats: list[str],
    enable_diarization: bool,
    diarization_backend: str,
    num_speakers: int | None,
    asr_selection: transcription_service.AsrSelection,
    subtitle_options: SubtitleOptions,
):
    """Скачивает медиа по URL и запускает обработку.

    Через общий MediaDownloader (как у MCP): лимит MAX_FILE_SIZE доходит до
    yt-dlp, схема URL проверяется там же. Загрузка идёт в свою папку
    `{task_id}_download`, которая убирается в любом исходе — с .part/.ytdl
    оборванной загрузки; готовый файл переносится в обычное
    `{task_id}_<имя>` загрузок.
    """
    download_dir = state.upload_dir / f"{task_id}_download"
    try:
        def on_percent(percent) -> None:  # MediaDownloader отдаёт 0..100; 95+ — уже обработка
            if task_id in registry.tasks and isinstance(percent, (int, float)):
                registry.tasks[task_id]['progress'] = max(0, min(95, int(percent)))

        downloader = state.media_downloader or MediaDownloader()
        loop = asyncio.get_running_loop()
        downloaded = await loop.run_in_executor(
            None,
            lambda: downloader.download(
                url, str(download_dir), progress_callback=on_percent, max_filesize=state.max_file_size,
            ),
        )

        if task_id in registry.deleted or task_id not in registry.tasks:
            if task_id in registry.deleted:
                registry.finalize_deleted(task_id)
            else:
                registry.remove_files(task_id)
            return

        files = [Path(p) for p in (getattr(downloaded, "files", None) or []) if Path(p).is_file()]
        if not files:
            # yt-dlp молча пропускает файл больше max_filesize
            max_gb = state.max_file_size / 1024 / 1024 / 1024
            raise RuntimeError(f"Ничего не скачано: медиа недоступно или больше лимита {max_gb:.1f} GB")
        if files[0].stat().st_size > state.max_file_size:
            raise RuntimeError("Скачанный файл больше лимита MAX_FILE_SIZE")

        filename = file_policy.safe_filename(files[0].name)
        file_path = state.upload_dir / f"{task_id}_{filename}"
        files[0].replace(file_path)
        shutil.rmtree(download_dir, ignore_errors=True)
        file_size = file_path.stat().st_size

        registry.tasks[task_id].update({
            'filename': filename,
            'file_size': file_size,
            'status': 'pending',
            'stage': 'В очереди',
            'progress': 0,
            'message': 'Загрузка завершена, ожидание обработки',
        })
        registry.persist()
        registry.log(task_id, f"Загружено: {filename} ({file_size / 1024 / 1024:.1f} MB)")

        await process_transcription(
            task_id, file_path, filename, output_formats,
            enable_diarization, diarization_backend, num_speakers,
            asr_selection,
            subtitle_options,
        )

    except Exception as e:
        registry.log(task_id, f"Ошибка загрузки: {e}")
        if task_id in registry.tasks:
            registry.tasks[task_id].update({
                'status': 'failed',
                'stage': 'Ошибка загрузки',
                'message': str(e),
                'completed_at': datetime.now().isoformat(),
            })
            registry.persist()

    finally:
        shutil.rmtree(download_dir, ignore_errors=True)
        if task_id in registry.deleted and task_id not in registry.tasks:
            registry.finalize_deleted(task_id)
