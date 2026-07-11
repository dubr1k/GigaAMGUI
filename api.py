#!/usr/bin/env python3
"""
GigaAM v3 Transcriber - REST API
Безопасный REST API для транскрибации с доступом из интернета
"""

import asyncio
import hashlib
import hmac
import os
import re
import shutil
import tempfile
import time
import traceback
import uuid

# Подавляем предупреждения
import warnings
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from platform import machine
from platform import platform as runtime_platform
from typing import Literal

import aiofiles
from fastapi import BackgroundTasks, Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.background import BackgroundTask

warnings.filterwarnings("ignore", category=UserWarning)

# Импорты проекта
from src.config import HF_TOKEN, SUPPORTED_FORMATS
from src.core.model_loader import ModelLoader
from src.core.processor import TranscriptionProcessor
from src.services import file_policy
from src.utils.atomic_json import load_json, save_json_atomic
from src.utils.audio_converter import ffmpeg_available
from src.utils.logger import setup_logger
from src.utils.output_naming import find_result_file, output_filename
from src.utils.processing_stats import ProcessingStats

if HF_TOKEN and HF_TOKEN.startswith("hf_"):
    try:
        from src.utils.pyannote_patch import apply_pyannote_patch
        apply_pyannote_patch()
    except Exception:
        print("ПРЕДУПРЕЖДЕНИЕ: pyannote patch не применен; продолжаем без него.")

# ==================== КОНФИГУРАЦИЯ ====================

# Загрузка конфигурации из .env
from dotenv import load_dotenv

# Загружаем переменные окружения из .env файла
env_path = Path(__file__).parent / '.env'
if env_path.exists():
    load_dotenv(env_path, override=False)

# API ключи (в файле хранятся только SHA-256 хэши)
API_KEYS_FILE = Path(__file__).parent / os.getenv("API_KEYS_FILE", ".api_keys")
VALID_API_KEY_HASHES: set = set()

# Регулярка валидного task_id (uuid4().hex = 32 hex-символа)
TASK_ID_RE = re.compile(r"^[0-9a-f]{32}$")

# Разрешённые CORS-origins (через запятую в .env); по умолчанию пусто = кросс-доменные запросы запрещены
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]

# Показывать ли клиенту полный traceback (только для отладки!)
API_DEBUG = os.getenv("API_DEBUG", "false").lower() in ("1", "true", "yes")

# Директории
UPLOAD_DIR = Path(__file__).parent / os.getenv("UPLOAD_DIR", "uploads")
RESULTS_DIR = Path(__file__).parent / os.getenv("RESULTS_DIR", "results")
UPLOAD_DIR.mkdir(exist_ok=True)
RESULTS_DIR.mkdir(exist_ok=True)

# Ограничения (из .env или значения по умолчанию)
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", str(2 * 1024 * 1024 * 1024)))  # 2GB
MAX_CONCURRENT_TASKS = int(os.getenv("MAX_CONCURRENT_TASKS", "3"))
TASK_CLEANUP_HOURS = int(os.getenv("TASK_CLEANUP_HOURS", "24"))

# API настройки
API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("API_PORT", "8000"))
API_WORKERS = int(os.getenv("API_WORKERS", "2"))

# Глобальные переменные
model_loader = None
stats_manager = None
logger = None

# Хранилище задач (в продакшене использовать Redis или базу данных)
tasks_storage: dict[str, dict] = {}

# Семафор для ограничения одновременных задач
processing_semaphore = None


# ==================== МОДЕЛИ ====================

class TaskStatus(BaseModel):
    """Статус задачи"""
    task_id: str
    status: str  # pending, processing, completed, failed
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    progress: int = 0  # 0-100
    stage: str = ""
    stage_progress: float | None = None
    processed_seconds: float | None = None
    total_seconds: float | None = None
    progress_indeterminate: bool = False
    filename: str
    file_size: int
    message: str | None = None
    error: str | None = None
    error_details: str | None = None  # Полный traceback для отладки


class TaskResult(BaseModel):
    """Результат транскрибации"""
    task_id: str
    status: str
    filename: str
    transcription: str | None = None
    transcription_with_timecodes: str | None = None
    processing_time: float | None = None
    media_duration: float | None = None


class UploadResponse(BaseModel):
    """Ответ на загрузку файла"""
    task_id: str
    message: str
    filename: str
    file_size: int
    estimated_time: str | None = None


class BatchUploadResponse(BaseModel):
    """Ответ на множественную загрузку файлов"""
    tasks: list[UploadResponse]
    total_files: int
    message: str


class APIKeyCreate(BaseModel):
    """Создание нового API ключа"""
    description: str = Field(..., min_length=3, max_length=100)


class APIKeyResponse(BaseModel):
    """Ответ с API ключом"""
    api_key: str
    description: str
    created_at: str


# ==================== УТИЛИТЫ ====================

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def _hash_key(key: str) -> str:
    """SHA-256 хэш ключа (в файле и памяти хранятся только хэши)"""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _asr_health() -> dict[str, object]:
    if model_loader is None:
        return {
            "requested_backend": None,
            "active_backend": None,
            "fallback_reason": None,
            "model": None,
            "device": "N/A",
            "repo": None,
            "cache_root": None,
            "loader_loaded": False,
            "error": None,
        }

    diagnostics = {}
    try:
        diagnostics = model_loader.diagnostics()
    except Exception:
        pass

    return {
        "requested_backend": diagnostics.get("requested_backend"),
        "active_backend": diagnostics.get("active_backend"),
        "fallback_reason": diagnostics.get("fallback_reason"),
        "model": diagnostics.get("model"),
        "device": diagnostics.get("device") or "N/A",
        "repo": diagnostics.get("repo"),
        "cache_root": diagnostics.get("cache_root"),
        "loader_loaded": model_loader.is_loaded(),
        "error": diagnostics.get("error"),
    }


def _runtime_info() -> dict[str, object]:
    return {
        "platform": runtime_platform(),
        "machine": machine(),
    }


def load_api_keys():
    """Загружает хэши API-ключей из файла (с миграцией старых plaintext-ключей)"""
    global VALID_API_KEY_HASHES
    if API_KEYS_FILE.exists():
        raw_lines = [ln.strip() for ln in API_KEYS_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()]
        hashes = set()
        migrated = False
        for line in raw_lines:
            if _HASH_RE.match(line):
                hashes.add(line)
            else:
                # Старый ключ в открытом виде — мигрируем в хэш
                hashes.add(_hash_key(line))
                migrated = True
        VALID_API_KEY_HASHES = hashes
        if migrated:
            save_api_keys()
            print("API-ключи мигрированы в хэшированный вид (.api_keys)")
    else:
        # Создаем первый ключ по умолчанию
        default_key = f"gam_{uuid.uuid4().hex}"
        VALID_API_KEY_HASHES = {_hash_key(default_key)}
        save_api_keys()
        print(f"\n{'='*60}")
        print("ПЕРВЫЙ API КЛЮЧ СОЗДАН (показывается только один раз):")
        print(f"  {default_key}")
        print("Сохраните его в безопасном месте! В файле хранится только хэш.")
        print(f"{'='*60}\n")


def save_api_keys():
    """Сохраняет хэши API-ключей в файл"""
    with open(API_KEYS_FILE, 'w') as f:
        for key_hash in VALID_API_KEY_HASHES:
            f.write(f"{key_hash}\n")
    os.chmod(API_KEYS_FILE, 0o600)  # Только владелец может читать


def verify_api_key(x_api_key: str = Header(..., alias="X-API-Key")) -> str:
    """Проверяет API ключ в постоянное время (constant-time)"""
    candidate = _hash_key(x_api_key)
    # hmac.compare_digest защищает от timing-атак при сравнении хэшей
    if not any(hmac.compare_digest(candidate, valid) for valid in VALID_API_KEY_HASHES):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный API ключ"
        )
    return x_api_key


def is_supported_format(filename: str) -> bool:
    """Проверяет поддерживаемый формат файла"""
    return file_policy.is_supported_by_glob(filename, SUPPORTED_FORMATS[1])


def safe_filename(filename: str | None) -> str:
    """Защита от path traversal: оставляет только базовое имя без разделителей путей."""
    return file_policy.safe_filename(filename)


def validated_task_id(task_id: str) -> str:
    """Валидирует task_id перед любым обращением к файловой системе"""
    if not TASK_ID_RE.match(task_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Некорректный формат task_id"
        )
    return task_id


def _fsync_path(path: Path):
    """Гарантирует запись файла на диск (синхронный вызов, запускать в executor)"""
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _register_task(task_id: str, filename: str, file_size: int):
    """Создаёт запись о задаче в хранилище"""
    tasks_storage[task_id] = {
        'task_id': task_id,
        'status': 'pending',
        'created_at': datetime.now().isoformat(),
        'started_at': None,
        'completed_at': None,
        'progress': 0,
        'stage_progress': None,
        'processed_seconds': None,
        'total_seconds': None,
        'progress_indeterminate': False,
        'filename': filename,
        'file_size': file_size,
        'message': 'Задача в очереди на обработку'
    }


async def _save_upload(file: UploadFile, request: Request) -> tuple:
    """
    Валидирует формат/размер и сохраняет загруженный файл.

    Возвращает (task_id, file_path, filename, file_size).
    Бросает HTTPException с корректными кодами (400/413/500) — вызывающий код
    обязан пробрасывать HTTPException дальше (не оборачивать в 500).
    """
    # Проверка формата
    if not is_supported_format(file.filename):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Неподдерживаемый формат файла {file.filename}. Поддерживаемые: {SUPPORTED_FORMATS[1]}"
        )

    max_gb = MAX_FILE_SIZE / 1024 / 1024 / 1024

    # Ранний отказ по Content-Length до записи на диск (защита от DoS)
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"Файл слишком большой (макс. {max_gb:.1f} GB)"
                )
        except ValueError:
            pass  # некорректный заголовок — проверим по факту записи

    task_id = uuid.uuid4().hex
    filename = safe_filename(file.filename)
    file_path = UPLOAD_DIR / f"{task_id}_{filename}"
    file_size = 0

    try:
        async with aiofiles.open(file_path, 'wb') as f:
            while chunk := await file.read(1024 * 1024):
                file_size += len(chunk)
                if file_size > MAX_FILE_SIZE:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Файл {filename} слишком большой (макс. {max_gb:.1f} GB)"
                    )
                await f.write(chunk)

        # Синхронизируем на диск, не блокируя event loop
        await asyncio.get_running_loop().run_in_executor(None, _fsync_path, file_path)

        if not file_path.exists():
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Файл не был сохранён на диск"
            )
        actual_size = file_path.stat().st_size
        if actual_size != file_size:
            # Усечённая запись — считаем ошибкой, а не «не критично»
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Размер сохранённого файла не совпадает с полученным"
            )
    except HTTPException:
        # Корректные коды (400/413/500) пробрасываем как есть, удалив частичный файл
        if file_path.exists():
            file_path.unlink()
        raise
    except Exception as e:
        if file_path.exists():
            file_path.unlink()
        if logger:
            logger.error(f"Ошибка сохранения файла {filename}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка сохранения файла"
        ) from e

    return task_id, file_path, filename, file_size


def restore_tasks_from_results():
    """Восстанавливает информацию о задачах из существующих результатов при запуске API"""
    if not RESULTS_DIR.exists():
        return

    restored_count = 0
    for task_dir in RESULTS_DIR.iterdir():
        if not task_dir.is_dir():
            continue

        task_id = task_dir.name

        # Предпочитаем реальные метаданные из meta.json, если они есть
        meta = load_json(str(task_dir / "meta.json"), None)
        if isinstance(meta, dict) and meta.get('filename'):
            tasks_storage[task_id] = {
                'task_id': task_id,
                'status': 'completed',
                'created_at': meta.get('created_at'),
                'started_at': meta.get('started_at', meta.get('created_at')),
                'completed_at': meta.get('completed_at', meta.get('created_at')),
                'progress': 100,
                'stage_progress': 1.0,
                'processed_seconds': meta.get('media_duration'),
                'total_seconds': meta.get('media_duration'),
                'progress_indeterminate': False,
                'filename': meta['filename'],
                'file_size': meta.get('file_size', 0),
                'message': 'Задача восстановлена из результатов при запуске API'
            }
            restored_count += 1
            continue

        # Fallback (старые задачи без meta.json): определяем имя по txt-файлу
        txt_files = list(task_dir.glob("*.txt"))
        if not txt_files:
            continue

        # Берем первый файл (не timecodes)
        result_file = None
        for f in txt_files:
            if not f.name.endswith('_timecodes.txt'):
                result_file = f
                break

        if not result_file:
            continue

        # Оригинальное расширение неизвестно — оставляем только базовое имя
        filename_base = result_file.stem
        created_timestamp = datetime.fromtimestamp(result_file.stat().st_ctime)

        tasks_storage[task_id] = {
            'task_id': task_id,
            'status': 'completed',
            'created_at': created_timestamp.isoformat(),
            'started_at': created_timestamp.isoformat(),
            'completed_at': created_timestamp.isoformat(),
            'progress': 100,
            'stage_progress': 1.0,
            'processed_seconds': 0,
            'total_seconds': 0,
            'progress_indeterminate': False,
            'filename': filename_base,  # без выдуманного .ogg
            'file_size': 0,  # Неизвестно
            'message': 'Задача восстановлена из результатов при запуске API'
        }

        restored_count += 1

    if restored_count > 0:
        logger.info(f"Восстановлено {restored_count} задач из существующих результатов")


async def cleanup_old_tasks():
    """Очищает старые задачи и файлы"""
    now = time.time()
    cutoff = now - (TASK_CLEANUP_HOURS * 3600)

    tasks_to_remove = []
    # Итерируемся по снимку, чтобы не словить "dictionary changed size during iteration"
    for task_id, task in list(tasks_storage.items()):
        created_timestamp = datetime.fromisoformat(task['created_at']).timestamp()
        if created_timestamp < cutoff:
            tasks_to_remove.append(task_id)

            # Удаляем файлы
            upload_path = UPLOAD_DIR / f"{task_id}_{task['filename']}"
            if upload_path.exists():
                upload_path.unlink()

            # Удаляем результаты
            result_dir = RESULTS_DIR / task_id
            if result_dir.exists():
                shutil.rmtree(result_dir)

    for task_id in tasks_to_remove:
        tasks_storage.pop(task_id, None)
        logger.info(f"Очищена задача {task_id} (старше {TASK_CLEANUP_HOURS}ч)")


async def _cleanup_loop():
    """Периодически запускает очистку старых задач (раз в час)"""
    while True:
        try:
            await asyncio.sleep(3600)
            await cleanup_old_tasks()
        except asyncio.CancelledError:
            break
        except Exception as e:
            if logger:
                logger.error(f"Ошибка в цикле очистки задач: {e}")


async def process_transcription(task_id: str, file_path: Path, filename: str):
    """
    Фоновая обработка транскрибации

    Args:
        task_id: ID задачи
        file_path: путь к файлу
        filename: имя файла
    """
    async with processing_semaphore:
        try:
            # Файл уже записан и fsync-нут в _save_upload до планирования задачи —
            # доверяем переданному пути, без glob/retry-эвристик.
            if not file_path.exists():
                logger.error(f"[{task_id}] Файл не найден: {file_path}")
                if task_id in tasks_storage:
                    tasks_storage[task_id].update({
                        'status': 'failed',
                        'completed_at': datetime.now().isoformat(),
                        'error': 'Файл не найден',
                        'message': 'Файл не найден'
                    })
                return

            # Задача могла быть удалена, пока ждала в очереди семафора
            if task_id not in tasks_storage:
                logger.debug(f"[{task_id}] Задача удалена до начала обработки — пропускаем")
                return

            # Обновляем статус
            tasks_storage[task_id]['status'] = 'processing'
            tasks_storage[task_id]['started_at'] = datetime.now().isoformat()
            tasks_storage[task_id]['progress'] = 5
            tasks_storage[task_id]['stage'] = 'Подготовка...'
            tasks_storage[task_id]['stage_progress'] = 0.0
            tasks_storage[task_id]['processed_seconds'] = 0.0
            tasks_storage[task_id]['total_seconds'] = None
            tasks_storage[task_id]['progress_indeterminate'] = False

            # Создаем директорию для результатов
            output_dir = RESULTS_DIR / task_id
            output_dir.mkdir(exist_ok=True)

            # Callback для обновления прогресса (с защитой от удалённой задачи)
            def progress_callback(event_or_stage, progress: float | None = None):
                task = tasks_storage.get(task_id)
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
                    progress_value = event_or_stage.get('file_progress')
                elif hasattr(event_or_stage, 'stage'):
                    stage = getattr(event_or_stage, 'stage', None)
                    stage_progress = getattr(event_or_stage, 'stage_progress', None)
                    processed_seconds = getattr(event_or_stage, 'processed_seconds', None)
                    total_seconds = getattr(event_or_stage, 'total_seconds', None)
                    progress_value = getattr(event_or_stage, 'file_progress', None)
                else:
                    stage = event_or_stage
                    progress_value = progress

                if progress_value is None:
                    progress_value = 0.0

                progress_value = max(0.0, min(float(progress_value), 1.0))
                stage_names = {
                    'preparing': 'Подготовка...',
                    'conversion': 'Конвертация...',
                    'transcription': 'Распознавание речи...',
                    'diarization': 'Диаризация...',
                    'export': 'Экспорт...',
                    'finalizing': 'Завершение...',
                }
                task['progress'] = int(progress_value * 100)
                if stage:
                    task['stage'] = stage_names.get(stage, stage)
                task['stage_progress'] = stage_progress
                task['processed_seconds'] = processed_seconds
                task['total_seconds'] = total_seconds
                task['progress_indeterminate'] = stage_progress is None

            # Процессор
            processor = TranscriptionProcessor(
                model_loader=model_loader,
                stats_manager=stats_manager,
                logger=lambda msg: logger.debug(f"[{task_id}] {msg}"),
                progress_callback=progress_callback
            )

            # Обработка в синхронном коде через executor.
            # Запрашиваем и txt, и txt_timecodes, чтобы _timecodes.txt реально создавался
            # (иначе transcription_with_timecodes всегда пуст).
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                lambda: processor.process_file(
                    str(file_path),
                    str(output_dir),
                    0,
                    1,
                    filename,  # Передаем оригинальное имя файла
                    output_formats=['txt', 'txt_timecodes'],
                )
            )

            if not result['success']:
                raise Exception("Обработка не удалась")

            # Читаем результаты (имена файлов — из единого модуля output_naming)
            stem = Path(filename).stem
            text_file = output_dir / output_filename(stem, 'txt')
            timecode_file = output_dir / output_filename(stem, 'txt_timecodes')

            transcription = ""
            transcription_timecoded = ""
            if text_file.exists():
                async with aiofiles.open(text_file, encoding='utf-8') as f:
                    transcription = await f.read()
            if timecode_file.exists():
                async with aiofiles.open(timecode_file, encoding='utf-8') as f:
                    transcription_timecoded = await f.read()

            # Обновляем задачу (если она ещё существует)
            if task_id in tasks_storage:
                tasks_storage[task_id].update({
                    'status': 'completed',
                    'completed_at': datetime.now().isoformat(),
                    'progress': 100,
                    'stage': 'Готово',
                    'stage_progress': 1.0,
                    'processed_seconds': result.get('media_duration'),
                    'total_seconds': result.get('media_duration'),
                    'progress_indeterminate': False,
                    'transcription': transcription,
                    'transcription_timecoded': transcription_timecoded,
                    'processing_time': result['total_time'],
                    'media_duration': result.get('media_duration', 0),
                    'message': 'Транскрибация успешно завершена'
                })

            # Сохраняем метаданные задачи на диск, чтобы корректно восстановить
            # реальное имя файла после перезапуска API (без выдуманного .ogg)
            try:
                task = tasks_storage.get(task_id, {})
                save_json_atomic(str(output_dir / "meta.json"), {
                    'task_id': task_id,
                    'filename': filename,
                    'file_size': task.get('file_size', 0),
                    'created_at': task.get('created_at'),
                    'started_at': task.get('started_at'),
                    'completed_at': task.get('completed_at'),
                })
            except OSError as meta_err:
                logger.debug(f"[{task_id}] Не удалось сохранить meta.json: {meta_err}")

            logger.info(f"Задача {task_id} успешно завершена ({result['total_time']:.1f}с)")

        except Exception as e:
            # Полный traceback — только в логи; клиенту отдаём обобщённое сообщение
            logger.error(f"Ошибка при обработке задачи {task_id}: {e}")
            logger.debug(f"Traceback для задачи {task_id}:\n{traceback.format_exc()}")
            if task_id in tasks_storage:
                update = {
                    'status': 'failed',
                    'completed_at': datetime.now().isoformat(),
                    'progress_indeterminate': False,
                    'error': 'Внутренняя ошибка обработки',
                    'message': 'Ошибка обработки файла'
                }
                if API_DEBUG:
                    update['error_details'] = traceback.format_exc()
                tasks_storage[task_id].update(update)

        finally:
            # Удаляем загруженный файл только после успешной обработки
            # (при ошибке файл может понадобиться для повторной попытки)
            task_status = tasks_storage.get(task_id, {}).get('status', 'unknown')
            if task_status == 'completed' and file_path.exists():
                try:
                    file_path.unlink()
                    logger.debug(f"[{task_id}] Удалён файл после успешной обработки: {file_path}")
                except OSError as e:
                    logger.debug(f"[{task_id}] Не удалось удалить файл {file_path}: {e}")


# ==================== LIFESPAN ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Управление жизненным циклом приложения"""
    global model_loader, stats_manager, logger, processing_semaphore

    # Инициализация
    print("="*60)
    print("🚀 Запуск GigaAM v3 Transcriber API")
    print("="*60)

    # Загрузка API ключей
    load_api_keys()

    # Логгер
    logger = setup_logger()
    logger.info("API сервер запускается...")

    # Восстановление задач из существующих результатов
    restore_tasks_from_results()

    # Предполётная проверка ffmpeg/ffprobe (без них конвертация не работает)
    if not ffmpeg_available():
        logger.error("ffmpeg/ffprobe не найдены в PATH — обработка файлов будет невозможна!")

    # Проверка токена
    if not HF_TOKEN or not HF_TOKEN.startswith("hf_"):
        logger.error("HuggingFace токен не настроен!")
        logger.warning("HF_TOKEN не настроен. Диаризация будет недоступна.")

    # Загрузка модели
    logger.info("Загрузка модели GigaAM-v3...")
    model_loader = ModelLoader()
    success = model_loader.load_model(logger=logger.info)

    if not success:
        logger.error("Не удалось загрузить модель!")
        raise RuntimeError("Ошибка загрузки модели")

    logger.info("Модель успешно загружена")

    # Статистика
    stats_manager = ProcessingStats()

    # Семафор для ограничения задач
    processing_semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

    # Фоновая периодическая очистка старых задач
    cleanup_task = asyncio.create_task(_cleanup_loop())

    logger.info(f"API готов к работе (макс. {MAX_CONCURRENT_TASKS} задач одновременно)")
    print("✅ API сервер успешно запущен!")
    print("="*60)

    yield

    # Очистка
    logger.info("Остановка API сервера...")
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    print("\n👋 API сервер остановлен")


# ==================== ПРИЛОЖЕНИЕ ====================

# Rate limiter
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="GigaAM v3 Transcriber API",
    description="REST API для транскрибации аудио и видео файлов на русском языке",
    version="3.0.0",
    lifespan=lifespan
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS — список доменов задаётся через CORS_ORIGINS в .env (по умолчанию кросс-домен запрещён).
# Аутентификация по заголовку X-API-Key, поэтому credentials не нужны.
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["X-API-Key", "Content-Type"],
)


# ==================== ЭНДПОИНТЫ ====================

@app.get("/")
async def root():
    """Корневой эндпоинт - информация о сервисе"""
    return {
        "service": "GigaAM v3 Transcriber API",
        "version": "3.0.0",
        "status": "running",
        "description": "REST API для транскрибации аудио и видео файлов на русском языке",
        "endpoints": {
            "docs": {
                "swagger": "/docs",
                "redoc": "/redoc"
            },
            "health": "/health",
            "transcription": {
                "upload_single": "POST /api/v1/transcribe",
                "upload_batch": "POST /api/v1/transcribe/batch"
            },
            "tasks": {
                "list_all": "GET /api/v1/tasks",
                "get_status": "GET /api/v1/tasks/{task_id}",
                "get_result": "GET /api/v1/tasks/{task_id}/result",
                "delete": "DELETE /api/v1/tasks/{task_id}",
                "delete_all": "DELETE /api/v1/tasks?status=completed|failed|all"
            },
            "progress": {
                "single": "GET /api/v1/tasks/{task_id}",
                "batch": "GET /api/v1/tasks/progress/batch?task_ids=id1,id2,id3"
            },
            "download": {
                "single": "GET /api/v1/tasks/{task_id}/download?format=txt|timecodes",
                "batch": "GET /api/v1/download-batch?task_ids=id1,id2&format=txt|timecodes"
            }
        }
    }


@app.get("/health")
async def health_check():
    """Проверка здоровья сервиса"""
    return {
        "status": "healthy",
        "model_loaded": model_loader is not None and model_loader.is_loaded(),
        "active_tasks": sum(1 for t in tasks_storage.values() if t['status'] == 'processing'),
        "total_tasks": len(tasks_storage),
        "runtime": _runtime_info(),
        "asr": _asr_health(),
        "uptime": "running"
    }




@app.post(
    "/api/v1/transcribe",
    response_model=UploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verify_api_key)]
)
@limiter.limit("10/minute")
async def upload_file(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="Аудио или видео файл")
):
    """
    Загрузить файл для транскрибации

    - **file**: Аудио или видео файл (mp3, wav, m4a, mp4, avi, mov, mkv, webm, flac, ogg, wma)
    - Максимальный размер: 2GB
    - Требуется заголовок X-API-Key

    Возвращает task_id для проверки статуса и получения результата.
    """
    # Валидация, сохранение и проверка файла (HTTPException пробрасывается с корректным кодом)
    task_id, file_path, filename, file_size = await _save_upload(file, request)

    # Создаем задачу и запускаем обработку в фоне
    _register_task(task_id, filename, file_size)
    background_tasks.add_task(process_transcription, task_id, file_path, filename)

    logger.info(f"Создана задача {task_id}: {filename} ({file_size/1024/1024:.1f} MB)")

    return UploadResponse(
        task_id=task_id,
        message="Файл успешно загружен и отправлен на обработку",
        filename=filename,
        file_size=file_size,
        estimated_time="Оценка будет доступна после начала обработки"
    )


@app.post(
    "/api/v1/transcribe/batch",
    response_model=BatchUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verify_api_key)]
)
@limiter.limit("5/minute")  # Ограничение на множественную загрузку
async def upload_files_batch(
    request: Request,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(..., description="Список аудио или видео файлов")
):
    """
    Загрузить несколько файлов для транскрибации

    - **files**: Список аудио или видео файлов (mp3, wav, m4a, mp4, avi, mov, mkv, webm, flac, ogg, wma)
    - Максимум 10 файлов за раз
    - Максимальный размер каждого файла: 2GB
    - Требуется заголовок X-API-Key

    Возвращает список task_id для проверки статуса каждого файла.
    """

    # Проверка количества файлов
    if len(files) > 10:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Максимум 10 файлов за раз"
        )

    if len(files) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Необходимо загрузить хотя бы один файл"
        )

    uploaded_tasks = []

    for file in files:
        # Валидация, сохранение и проверка файла (общий хелпер; HTTPException пробрасывается)
        task_id, file_path, filename, file_size = await _save_upload(file, request)

        _register_task(task_id, filename, file_size)
        background_tasks.add_task(process_transcription, task_id, file_path, filename)

        logger.info(f"Создана задача {task_id}: {filename} ({file_size/1024/1024:.1f} MB)")

        uploaded_tasks.append(UploadResponse(
            task_id=task_id,
            message="Файл успешно загружен и отправлен на обработку",
            filename=filename,
            file_size=file_size,
            estimated_time="Оценка будет доступна после начала обработки"
        ))

    return BatchUploadResponse(
        tasks=uploaded_tasks,
        total_files=len(uploaded_tasks),
        message=f"Успешно загружено {len(uploaded_tasks)} файлов для обработки"
    )


@app.get(
    "/api/v1/tasks",
    dependencies=[Depends(verify_api_key)]
)
async def list_tasks(
    status_filter: Literal["pending", "processing", "completed", "failed"] | None = None,
    limit: int = Query(100, ge=1, le=1000)
):
    """
    Получить список задач

    - **status_filter**: фильтр по статусу (pending, processing, completed, failed)
    - **limit**: максимальное количество задач (по умолчанию 100)
    """
    tasks = list(tasks_storage.values())

    # Фильтрация
    if status_filter:
        tasks = [t for t in tasks if t['status'] == status_filter]

    # Сортировка по дате создания (новые первыми)
    tasks.sort(key=lambda x: x['created_at'], reverse=True)

    # Ограничение
    tasks = tasks[:limit]

    return {
        "total": len(tasks),
        "tasks": tasks
    }


@app.get(
    "/api/v1/tasks/{task_id}",
    response_model=TaskStatus,
    dependencies=[Depends(verify_api_key)]
)
async def get_task_status(task_id: str):
    """
    Получить статус задачи

    - **task_id**: ID задачи, полученный при загрузке файла

    Статусы:
    - pending: в очереди
    - processing: обрабатывается
    - completed: завершено успешно
    - failed: ошибка
    """
    validated_task_id(task_id)
    if task_id not in tasks_storage:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Задача не найдена"
        )

    task = tasks_storage[task_id]
    return TaskStatus(**task)


@app.get(
    "/api/v1/tasks/progress/batch",
    dependencies=[Depends(verify_api_key)]
)
async def get_batch_progress(task_ids: str):
    """
    Получить прогресс нескольких задач одновременно

    - **task_ids**: Список ID задач через запятую (task_id1,task_id2,task_id3)

    Возвращает статус и прогресс для каждой задачи.
    Полезно для отображения прогресса в Postman/клиенте.
    """
    # Парсим список task_id
    task_id_list = [tid.strip() for tid in task_ids.split(',') if tid.strip()]

    if len(task_id_list) > 50:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Максимум 50 задач за раз"
        )

    if len(task_id_list) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Необходимо указать хотя бы один task_id. Получено: '{task_ids}'. Убедитесь, что переменные task_id установлены в Postman или укажите ID вручную."
        )

    # Собираем информацию о задачах
    results = []
    not_found = []

    for task_id in task_id_list:
        if task_id in tasks_storage:
            task = tasks_storage[task_id]
            results.append({
                "task_id": task_id,
                "status": task['status'],
                "progress": task.get('progress', 0),
                "filename": task.get('filename', ''),
                "message": task.get('message', ''),
                "error": task.get('error')
            })
        else:
            not_found.append(task_id)

    return {
        "total_requested": len(task_id_list),
        "found": len(results),
        "not_found": not_found,
        "tasks": results
    }


@app.get(
    "/api/v1/tasks/{task_id}/result",
    response_model=TaskResult,
    dependencies=[Depends(verify_api_key)]
)
async def get_task_result(task_id: str):
    """
    Получить результат транскрибации

    - **task_id**: ID задачи

    Возвращает текст транскрибации с таймкодами и без.
    Доступно только для завершенных задач.
    """
    validated_task_id(task_id)
    if task_id not in tasks_storage:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Задача не найдена"
        )

    task = tasks_storage[task_id]

    if task['status'] != 'completed':
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Задача еще не завершена (статус: {task['status']})"
        )

    # Проверяем что результаты есть в tasks_storage
    # Если нет - пробуем прочитать из файлов (для восстановленных задач)
    transcription = task.get('transcription')
    transcription_timecoded = task.get('transcription_timecoded')

    # Если результатов нет в памяти, читаем из файлов
    if not transcription or not transcription_timecoded:
        result_dir = RESULTS_DIR / task_id
        if not result_dir.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Директория с результатами не найдена"
            )

        filename_base = Path(task['filename']).stem

        # Имена детерминированы — ищем через единый модуль output_naming
        text_file = find_result_file(result_dir, filename_base, 'txt')
        timecode_file = find_result_file(result_dir, filename_base, 'txt_timecodes')

        if text_file:
            async with aiofiles.open(text_file, encoding='utf-8') as f:
                transcription = await f.read()

        if timecode_file:
            async with aiofiles.open(timecode_file, encoding='utf-8') as f:
                transcription_timecoded = await f.read()

    return TaskResult(
        task_id=task_id,
        status=task['status'],
        filename=task['filename'],
        transcription=transcription,
        transcription_with_timecodes=transcription_timecoded,
        processing_time=task.get('processing_time'),
        media_duration=task.get('media_duration')
    )


@app.get(
    "/api/v1/download-batch",
    dependencies=[Depends(verify_api_key)]
)
async def download_batch_results(task_ids: str, format: Literal["txt", "timecodes"] = "txt"):
    """
    Скачать результаты нескольких задач в ZIP архиве

    - **task_ids**: Список ID задач через запятую (task_id1,task_id2,task_id3)
    - **format**: формат файлов (txt или timecodes)
    """

    # Парсим список task_id
    task_id_list = [tid.strip() for tid in task_ids.split(',') if tid.strip()]
    for tid in task_id_list:
        validated_task_id(tid)

    if len(task_id_list) > 20:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Максимум 20 задач за раз"
        )

    if len(task_id_list) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Необходимо указать хотя бы один task_id. Получено: '{task_ids}'. Убедитесь, что переменные task_id установлены в Postman или укажите ID вручную."
        )

    # Проверяем все задачи
    valid_tasks = []
    not_found_ids = []
    not_completed_ids = []

    for task_id in task_id_list:
        if task_id not in tasks_storage:
            not_found_ids.append(task_id)
            continue

        task = tasks_storage[task_id]
        if task['status'] != 'completed':
            not_completed_ids.append(f"{task_id} (status: {task['status']})")
            continue

        valid_tasks.append(task)

    if not_found_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Задачи не найдены: {', '.join(not_found_ids)}. Используйте GET /api/v1/tasks для получения списка доступных task_id."
        )

    if not_completed_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Некоторые задачи еще не завершены: {', '.join(not_completed_ids)}"
        )

    # Создаём ZIP во временном файле, чтобы не держать весь архив в памяти
    tmp_fd, tmp_zip_path = tempfile.mkstemp(suffix=".zip")
    os.close(tmp_fd)

    with zipfile.ZipFile(tmp_zip_path, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for task in valid_tasks:
            task_id = task['task_id']
            result_dir = RESULTS_DIR / task_id

            # Проверяем что директория существует
            if not result_dir.exists():
                error_msg = f"Директория с результатами не найдена для задачи {task_id}\n"
                error_msg += f"Файл: {task['filename']}"
                zip_name = f"{Path(task['filename']).stem}.txt"
                zip_file.writestr(zip_name, error_msg)
                logger.warning(f"Batch download: директория не найдена {result_dir}")
                continue

            filename_base = Path(task['filename']).stem
            fmt_key = 'txt_timecodes' if format == "timecodes" else 'txt'
            zip_name = output_filename(filename_base, fmt_key)

            try:
                # Имена детерминированы — ищем через единый модуль output_naming
                found_file = find_result_file(result_dir, filename_base, fmt_key)

                # Добавляем файл в архив
                if found_file:
                    zip_file.write(str(found_file), zip_name)
                    logger.info(f"Batch download: добавлен файл {zip_name} для задачи {task_id}")
                else:
                    # Файл не найден - добавляем сообщение об ошибке (без листинга папки)
                    error_msg = f"Результат не найден для файла: {task['filename']}\n"
                    error_msg += f"Искали: {zip_name}"
                    zip_file.writestr(zip_name, error_msg)
                    logger.warning(f"Batch download: файл не найден для задачи {task_id}")

            except Exception as e:
                logger.error(f"Batch download: ошибка при обработке задачи {task_id}: {e}")
                error_msg = f"Ошибка при обработке файла {task['filename']}"
                zip_file.writestr(zip_name, error_msg)

    # Возвращаем ZIP файл и удаляем временный файл после отправки
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"transcription_results_{timestamp}.zip"

    return FileResponse(
        path=tmp_zip_path,
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(os.unlink, tmp_zip_path),
    )


@app.get(
    "/api/v1/tasks/{task_id}/download",
    dependencies=[Depends(verify_api_key)]
)
async def download_result(task_id: str, format: Literal["txt", "timecodes"] = "txt"):
    """
    Скачать файл с результатами

    - **task_id**: ID задачи
    - **format**: формат файла (txt или timecodes)
    """
    validated_task_id(task_id)
    if task_id not in tasks_storage:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Задача не найдена"
        )

    task = tasks_storage[task_id]

    if task['status'] != 'completed':
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Задача еще не завершена (статус: {task['status']})"
        )

    # Проверяем директорию с результатами
    result_dir = RESULTS_DIR / task_id
    if not result_dir.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Директория с результатами не найдена"
        )

    # Получаем базовое имя файла и формат (имена детерминированы — output_naming)
    filename_base = Path(task['filename']).stem
    fmt_key = 'txt_timecodes' if format == "timecodes" else 'txt'
    found_file = find_result_file(result_dir, filename_base, fmt_key)

    # Если файл не найден, возвращаем ошибку (без листинга папки)
    if not found_file:
        error_detail = "Файл результата не найден. "
        error_detail += f"Искали: {output_filename(filename_base, fmt_key)}"
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_detail
        )

    logger.info(f"Download: отправляем файл {found_file.name} для задачи {task_id}")

    return FileResponse(
        path=str(found_file),  # Используем строку для надежности
        filename=found_file.name,
        media_type="text/plain; charset=utf-8"
    )


@app.delete(
    "/api/v1/tasks/{task_id}",
    dependencies=[Depends(verify_api_key)]
)
async def delete_task(task_id: str):
    """
    Удалить задачу и связанные файлы

    - **task_id**: ID задачи
    """
    validated_task_id(task_id)
    if task_id not in tasks_storage:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Задача не найдена"
        )

    task = tasks_storage[task_id]

    if task['status'] == 'processing':
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Нельзя удалить задачу в процессе обработки"
        )

    # Удаляем файлы
    upload_path = UPLOAD_DIR / f"{task_id}_{task['filename']}"
    if upload_path.exists():
        upload_path.unlink()

    result_dir = RESULTS_DIR / task_id
    if result_dir.exists():
        shutil.rmtree(result_dir)

    # Удаляем задачу
    tasks_storage.pop(task_id, None)

    logger.info(f"Задача {task_id} удалена")

    return {"message": "Задача успешно удалена"}


@app.delete(
    "/api/v1/tasks",
    dependencies=[Depends(verify_api_key)]
)
async def delete_all_tasks(status_filter: str | None = None):
    """
    Удалить все задачи (массовое удаление)

    - **status_filter**: фильтр по статусу для удаления (completed, failed, pending, all)
      - Если не указан, по умолчанию удаляются только завершенные задачи (completed, failed)
      - Используйте "all" для удаления всех задач включая pending
      - Задачи со статусом "processing" НИКОГДА не удаляются

    Примеры:
    - DELETE /api/v1/tasks - удалит все completed и failed задачи
    - DELETE /api/v1/tasks?status_filter=completed - удалит только completed
    - DELETE /api/v1/tasks?status_filter=all - удалит все кроме processing
    """

    # Определяем какие статусы удалять
    if status_filter == "all":
        # Удаляем все кроме processing
        statuses_to_delete = ['pending', 'completed', 'failed']
    elif status_filter in ['completed', 'failed', 'pending']:
        # Удаляем только указанный статус
        statuses_to_delete = [status_filter]
    elif status_filter is None:
        # По умолчанию удаляем completed и failed
        statuses_to_delete = ['completed', 'failed']
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Неверный статус фильтра. Используйте: completed, failed, pending, all"
        )

    # Собираем задачи для удаления (по снимку — без гонок при изменении словаря)
    tasks_to_delete = []
    for task_id, task in list(tasks_storage.items()):
        if task['status'] in statuses_to_delete:
            tasks_to_delete.append(task_id)

    if len(tasks_to_delete) == 0:
        return {
            "message": "Нет задач для удаления",
            "deleted_count": 0,
            "filter": status_filter or "completed, failed (default)"
        }

    # Удаляем задачи и их файлы
    deleted_count = 0
    errors = []

    for task_id in tasks_to_delete:
        try:
            task = tasks_storage.get(task_id)
            if task is None:
                continue  # уже удалена другим запросом

            # Удаляем загруженный файл
            upload_path = UPLOAD_DIR / f"{task_id}_{task['filename']}"
            if upload_path.exists():
                upload_path.unlink()

            # Удаляем результаты
            result_dir = RESULTS_DIR / task_id
            if result_dir.exists():
                shutil.rmtree(result_dir)

            # Удаляем из хранилища
            tasks_storage.pop(task_id, None)
            deleted_count += 1

        except Exception as e:
            errors.append(f"{task_id}: {str(e)}")
            logger.error(f"Ошибка при удалении задачи {task_id}: {str(e)}")

    logger.info(f"Массовое удаление: удалено {deleted_count} задач (фильтр: {status_filter or 'default'})")

    response = {
        "message": f"Успешно удалено задач: {deleted_count}",
        "deleted_count": deleted_count,
        "filter": status_filter or "completed, failed (default)"
    }

    if errors:
        response["errors"] = errors
        response["message"] += f" (с ошибками: {len(errors)})"

    return response


# ==================== ЗАПУСК ====================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api:app",
        host=API_HOST,
        port=API_PORT,
        reload=False,
        log_level="info"
    )
