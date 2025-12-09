#!/usr/bin/env python3
"""
GigaAM v3 Transcriber - REST API
Безопасный REST API для транскрибации с доступом из интернета
"""

import os
import sys
import uuid
import time
import asyncio
import shutil
import zipfile
import io
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict
from contextlib import asynccontextmanager

import aiofiles
from fastapi import (
    FastAPI, File, UploadFile, HTTPException, Depends,
    BackgroundTasks, status, Header, Request
)
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Подавляем предупреждения
import warnings
warnings.filterwarnings("ignore", category=UserWarning)

# Импорты проекта
from src.utils.pyannote_patch import apply_pyannote_patch
from src.core.model_loader import ModelLoader
from src.core.processor import TranscriptionProcessor
from src.utils.processing_stats import ProcessingStats
from src.utils.logger import setup_logger
from src.config import HF_TOKEN, SUPPORTED_FORMATS

# Применяем патч
apply_pyannote_patch()

# ==================== КОНФИГУРАЦИЯ ====================

# Загрузка конфигурации из .env
import os
from pathlib import Path
from dotenv import load_dotenv

# Загружаем переменные окружения из .env файла
env_path = Path(__file__).parent / '.env'
if env_path.exists():
    load_dotenv(env_path, override=False)

# API ключи
API_KEYS_FILE = Path(__file__).parent / os.getenv("API_KEYS_FILE", ".api_keys")
VALID_API_KEYS = set()

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
tasks_storage: Dict[str, dict] = {}

# Семафор для ограничения одновременных задач
processing_semaphore = None


# ==================== МОДЕЛИ ====================

class TaskStatus(BaseModel):
    """Статус задачи"""
    task_id: str
    status: str  # pending, processing, completed, failed
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    progress: int = 0  # 0-100
    filename: str
    file_size: int
    message: Optional[str] = None
    error: Optional[str] = None
    error_details: Optional[str] = None  # Полный traceback для отладки


class TaskResult(BaseModel):
    """Результат транскрибации"""
    task_id: str
    status: str
    filename: str
    transcription: Optional[str] = None
    transcription_with_timecodes: Optional[str] = None
    processing_time: Optional[float] = None
    media_duration: Optional[float] = None


class UploadResponse(BaseModel):
    """Ответ на загрузку файла"""
    task_id: str
    message: str
    filename: str
    file_size: int
    estimated_time: Optional[str] = None


class BatchUploadResponse(BaseModel):
    """Ответ на множественную загрузку файлов"""
    tasks: List[UploadResponse]
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

def load_api_keys():
    """Загружает API ключи из файла"""
    global VALID_API_KEYS
    if API_KEYS_FILE.exists():
        with open(API_KEYS_FILE, 'r') as f:
            VALID_API_KEYS = set(line.strip() for line in f if line.strip())
    else:
        # Создаем первый ключ по умолчанию
        default_key = f"gam_{uuid.uuid4().hex}"
        VALID_API_KEYS.add(default_key)
        save_api_keys()
        print(f"\n{'='*60}")
        print(f"ПЕРВЫЙ API КЛЮЧ СОЗДАН:")
        print(f"  {default_key}")
        print(f"Сохраните его в безопасном месте!")
        print(f"{'='*60}\n")


def save_api_keys():
    """Сохраняет API ключи в файл"""
    with open(API_KEYS_FILE, 'w') as f:
        for key in VALID_API_KEYS:
            f.write(f"{key}\n")
    os.chmod(API_KEYS_FILE, 0o600)  # Только владелец может читать


def verify_api_key(x_api_key: str = Header(..., alias="X-API-Key")) -> str:
    """Проверяет API ключ"""
    if x_api_key not in VALID_API_KEYS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный API ключ"
        )
    return x_api_key


def is_supported_format(filename: str) -> bool:
    """Проверяет поддерживаемый формат файла"""
    extensions = SUPPORTED_FORMATS[1].split()
    file_ext = Path(filename).suffix.lower()
    return any(file_ext == ext.replace('*', '') for ext in extensions)


def restore_tasks_from_results():
    """Восстанавливает информацию о задачах из существующих результатов при запуске API"""
    if not RESULTS_DIR.exists():
        return
    
    restored_count = 0
    for task_dir in RESULTS_DIR.iterdir():
        if not task_dir.is_dir():
            continue
        
        task_id = task_dir.name
        
        # Ищем любой txt файл для определения имени оригинального файла
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
        
        # Определяем оригинальное имя файла
        filename_base = result_file.stem
        # Предполагаем что это был аудио файл (можем использовать .ogg как fallback)
        original_filename = filename_base + '.ogg'
        
        # Получаем время создания файла
        created_timestamp = datetime.fromtimestamp(result_file.stat().st_ctime)
        
        # Восстанавливаем запись о задаче
        tasks_storage[task_id] = {
            'task_id': task_id,
            'status': 'completed',
            'created_at': created_timestamp.isoformat(),
            'started_at': created_timestamp.isoformat(),
            'completed_at': created_timestamp.isoformat(),
            'progress': 100,
            'filename': original_filename,
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
    for task_id, task in tasks_storage.items():
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
        del tasks_storage[task_id]
        logger.info(f"Очищена задача {task_id} (старше {TASK_CLEANUP_HOURS}ч)")


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
            # Проверяем существование файла перед обработкой
            # Используем оригинальный путь и resolved путь для надежности
            file_path_original = file_path
            file_path_resolved = file_path.resolve()
            
            # Логируем оба пути для отладки
            logger.debug(f"[{task_id}] Проверка файла:")
            logger.debug(f"[{task_id}]   Оригинальный путь: {file_path_original}")
            logger.debug(f"[{task_id}]   Resolved путь: {file_path_resolved}")
            logger.debug(f"[{task_id}]   Оригинальный существует: {file_path_original.exists()}")
            logger.debug(f"[{task_id}]   Resolved существует: {file_path_resolved.exists()}")
            
            # Проверяем оба варианта пути
            actual_file_path = None
            if file_path_resolved.exists():
                actual_file_path = file_path_resolved
            elif file_path_original.exists():
                actual_file_path = file_path_original
            else:
                # Пробуем найти файл по имени в директории uploads
                filename_only = file_path_original.name
                alternative_path = UPLOAD_DIR / filename_only
                logger.debug(f"[{task_id}]   Альтернативный путь: {alternative_path}")
                logger.debug(f"[{task_id}]   Альтернативный существует: {alternative_path.exists()}")
                
                if alternative_path.exists():
                    actual_file_path = alternative_path
                else:
                    # КРИТИЧНО: Пробуем найти файл через glob, так как файл может еще не полностью записан на диск
                    # и exists() может вернуть False, но glob все равно найдет файл
                    try:
                        files_in_dir = list(UPLOAD_DIR.glob(f"*{task_id}*"))
                        logger.debug(f"[{task_id}] Файлы с task_id в директории uploads (через glob): {[str(f) for f in files_in_dir]}")
                        
                        if files_in_dir:
                            # Используем первый найденный файл
                            found_file = files_in_dir[0]
                            logger.debug(f"[{task_id}] Найден файл через glob: {found_file}")
                            logger.debug(f"[{task_id}] Файл существует (проверка после glob): {found_file.exists()}")
                            
                            # Даже если exists() возвращает False, пробуем использовать файл
                            # Возможно, файл еще записывается, но уже доступен через glob
                            actual_file_path = found_file
                            logger.info(f"[{task_id}] Используем файл, найденный через glob (может быть еще записывается)")
                        else:
                            error_msg = f"Файл не найден ни по одному из путей:\n  - {file_path_original}\n  - {file_path_resolved}\n  - {alternative_path}"
                            logger.error(f"[{task_id}] {error_msg}")
                            
                            tasks_storage[task_id].update({
                                'status': 'failed',
                                'completed_at': datetime.now().isoformat(),
                                'error': error_msg,
                                'message': 'Файл не найден'
                            })
                            return
                    except Exception as e:
                        logger.debug(f"[{task_id}] Не удалось прочитать директорию uploads: {e}")
                        error_msg = f"Ошибка при поиске файла: {str(e)}"
                        logger.error(f"[{task_id}] {error_msg}")
                        tasks_storage[task_id].update({
                            'status': 'failed',
                            'completed_at': datetime.now().isoformat(),
                            'error': error_msg,
                            'message': 'Ошибка при поиске файла'
                        })
                        return
            
            logger.debug(f"[{task_id}] Используемый путь к файлу: {actual_file_path}")
            
            # Обновляем статус
            tasks_storage[task_id]['status'] = 'processing'
            tasks_storage[task_id]['started_at'] = datetime.now().isoformat()
            tasks_storage[task_id]['progress'] = 5
            
            # Создаем директорию для результатов
            output_dir = RESULTS_DIR / task_id
            output_dir.mkdir(exist_ok=True)
            
            # Callback для обновления прогресса
            def progress_callback(stage: str, progress: float):
                if stage == 'conversion':
                    tasks_storage[task_id]['progress'] = int(5 + progress * 15)
                elif stage == 'transcription':
                    tasks_storage[task_id]['progress'] = int(20 + progress * 75)
            
            # Процессор
            processor = TranscriptionProcessor(
                model_loader=model_loader,
                stats_manager=stats_manager,
                logger=lambda msg: logger.debug(f"[{task_id}] {msg}"),
                progress_callback=progress_callback
            )
            
            # Финальная проверка файла перед обработкой
            # Если файл был найден через glob, он может еще записываться
            # Пробуем несколько раз с небольшой задержкой
            file_found = False
            for attempt in range(3):
                if actual_file_path.exists():
                    file_found = True
                    break
                else:
                    logger.debug(f"[{task_id}] Попытка {attempt + 1}/3: файл еще не доступен, ждем 0.5 сек...")
                    await asyncio.sleep(0.5)
            
            if not file_found:
                # Последняя попытка через glob
                files_in_dir = list(UPLOAD_DIR.glob(f"*{task_id}*"))
                if files_in_dir:
                    actual_file_path = files_in_dir[0]
                    logger.info(f"[{task_id}] Файл найден через glob после ожидания: {actual_file_path}")
                    file_found = True
            
            if not file_found:
                error_msg = f"Файл недоступен после ожидания: {actual_file_path}"
                logger.error(f"[{task_id}] {error_msg}")
                tasks_storage[task_id].update({
                    'status': 'failed',
                    'completed_at': datetime.now().isoformat(),
                    'error': error_msg,
                    'message': 'Файл недоступен после ожидания'
                })
                return
            
            logger.debug(f"[{task_id}] Файл подтвержден перед обработкой: {actual_file_path}")
            try:
                file_size = actual_file_path.stat().st_size
                logger.debug(f"[{task_id}] Размер файла: {file_size} байт")
            except Exception as e:
                logger.warning(f"[{task_id}] Не удалось получить размер файла: {e}")
            
            # Обработка в синхронном коде через executor
            # Используем найденный путь к файлу
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                processor.process_file,
                str(actual_file_path),
                str(output_dir),
                0,
                1,
                filename  # Передаем оригинальное имя файла
            )
            
            if result['success']:
                # Читаем результаты
                # Файлы сохраняются с оригинальным именем
                text_file = output_dir / f"{Path(filename).stem}.txt"
                timecode_file = output_dir / f"{Path(filename).stem}_timecodes.txt"
                
                transcription = ""
                transcription_timecoded = ""
                
                if text_file.exists():
                    async with aiofiles.open(text_file, 'r', encoding='utf-8') as f:
                        transcription = await f.read()
                
                if timecode_file.exists():
                    async with aiofiles.open(timecode_file, 'r', encoding='utf-8') as f:
                        transcription_timecoded = await f.read()
                
                # Обновляем задачу
                tasks_storage[task_id].update({
                    'status': 'completed',
                    'completed_at': datetime.now().isoformat(),
                    'progress': 100,
                    'transcription': transcription,
                    'transcription_timecoded': transcription_timecoded,
                    'processing_time': result['total_time'],
                    'media_duration': result.get('media_duration', 0),
                    'message': 'Транскрибация успешно завершена'
                })
                
                logger.info(f"Задача {task_id} успешно завершена ({result['total_time']:.1f}с)")
                
            else:
                raise Exception("Обработка не удалась")
                
        except Exception as e:
            import traceback
            error_traceback = traceback.format_exc()
            error_msg = str(e)
            logger.error(f"Ошибка при обработке задачи {task_id}: {error_msg}")
            logger.debug(f"Traceback для задачи {task_id}:\n{error_traceback}")
            tasks_storage[task_id].update({
                'status': 'failed',
                'completed_at': datetime.now().isoformat(),
                'error': error_msg,
                'error_details': error_traceback,  # Добавляем полный traceback для отладки
                'message': f'Ошибка обработки: {error_msg}'
            })
        
        finally:
            # Удаляем загруженный файл только после успешной обработки
            # НЕ удаляем файл, если обработка не удалась - файл может понадобиться для повторной попытки
            task_status = tasks_storage.get(task_id, {}).get('status', 'unknown')
            if task_status == 'completed':
                # Удаляем файл только после успешной обработки
                paths_to_check = []
                if 'actual_file_path' in locals():
                    paths_to_check.append(actual_file_path)
                paths_to_check.append(file_path)
                if hasattr(file_path, 'resolve'):
                    paths_to_check.append(file_path.resolve())
                
                for path_to_check in paths_to_check:
                    if path_to_check and Path(path_to_check).exists():
                        try:
                            Path(path_to_check).unlink()
                            logger.debug(f"[{task_id}] Удален файл после успешной обработки: {path_to_check}")
                            break
                        except Exception as e:
                            logger.debug(f"[{task_id}] Не удалось удалить файл {path_to_check}: {e}")
            else:
                logger.debug(f"[{task_id}] Файл не удален (статус: {task_status}), может понадобиться для повторной попытки")


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
    
    # Проверка токена
    if not HF_TOKEN or not HF_TOKEN.startswith("hf_"):
        logger.error("HuggingFace токен не настроен!")
        raise RuntimeError("Требуется настроить HF_TOKEN в .env")
    
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
    
    logger.info(f"API готов к работе (макс. {MAX_CONCURRENT_TASKS} задач одновременно)")
    print("✅ API сервер успешно запущен!")
    print("="*60)
    
    yield
    
    # Очистка
    logger.info("Остановка API сервера...")
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

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # В продакшене указать конкретные домены
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
    
    # Проверка формата
    if not is_supported_format(file.filename):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Неподдерживаемый формат файла. Поддерживаемые: {SUPPORTED_FORMATS[1]}"
        )
    
    # Генерируем ID задачи
    task_id = uuid.uuid4().hex
    
    # Сохраняем файл
    file_path = UPLOAD_DIR / f"{task_id}_{file.filename}"
    file_size = 0
    
    try:
        async with aiofiles.open(file_path, 'wb') as f:
            while chunk := await file.read(8192):
                file_size += len(chunk)
                
                # Проверка размера
                if file_size > MAX_FILE_SIZE:
                    await f.close()
                    file_path.unlink()
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Файл слишком большой (макс. {MAX_FILE_SIZE/1024/1024/1024:.1f} GB)"
                    )
                
                await f.write(chunk)
        
        # Убеждаемся, что файл полностью записан на диск
        # Синхронизируем файловую систему
        file_fd = os.open(str(file_path), os.O_RDONLY)
        try:
            os.fsync(file_fd)  # Синхронизируем файл
        finally:
            os.close(file_fd)
        
        # Финальная проверка, что файл существует и имеет правильный размер
        if not file_path.exists():
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Файл не был сохранен на диск"
            )
        
        actual_size = file_path.stat().st_size
        if actual_size != file_size:
            logger.warning(f"Размер файла не совпадает: ожидалось {file_size}, получено {actual_size}")
            # Не критично, но логируем
    
    except Exception as e:
        if file_path.exists():
            file_path.unlink()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка сохранения файла: {str(e)}"
        )
    
    # Создаем задачу
    tasks_storage[task_id] = {
        'task_id': task_id,
        'status': 'pending',
        'created_at': datetime.now().isoformat(),
        'started_at': None,
        'completed_at': None,
        'progress': 0,
        'filename': file.filename,
        'file_size': file_size,
        'message': 'Задача в очереди на обработку'
    }
    
    # Запускаем обработку в фоне
    background_tasks.add_task(process_transcription, task_id, file_path, file.filename)
    
    logger.info(f"Создана задача {task_id}: {file.filename} ({file_size/1024/1024:.1f} MB)")
    
    return UploadResponse(
        task_id=task_id,
        message="Файл успешно загружен и отправлен на обработку",
        filename=file.filename,
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
    files: List[UploadFile] = File(..., description="Список аудио или видео файлов")
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
        # Проверка формата
        if not is_supported_format(file.filename):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Неподдерживаемый формат файла {file.filename}. Поддерживаемые: {SUPPORTED_FORMATS[1]}"
            )

        # Генерируем ID задачи
        task_id = uuid.uuid4().hex

        # Сохраняем файл
        file_path = UPLOAD_DIR / f"{task_id}_{file.filename}"
        file_size = 0

        try:
            async with aiofiles.open(file_path, 'wb') as f:
                while chunk := await file.read(8192):
                    file_size += len(chunk)

                    # Проверка размера
                    if file_size > MAX_FILE_SIZE:
                        await f.close()
                        file_path.unlink()
                        raise HTTPException(
                            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail=f"Файл {file.filename} слишком большой (макс. {MAX_FILE_SIZE/1024/1024/1024:.1f} GB)"
                        )

                    await f.write(chunk)
            
            # Убеждаемся, что файл полностью записан на диск
            # Синхронизируем файловую систему
            import os
            file_fd = os.open(str(file_path), os.O_RDONLY)
            try:
                os.fsync(file_fd)  # Синхронизируем файл
            finally:
                os.close(file_fd)
            
            # Финальная проверка, что файл существует и имеет правильный размер
            if not file_path.exists():
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Файл {file.filename} не был сохранен на диск"
                )
            
            actual_size = file_path.stat().st_size
            if actual_size != file_size:
                logger.warning(f"Размер файла {file.filename} не совпадает: ожидалось {file_size}, получено {actual_size}")

        except Exception as e:
            if file_path.exists():
                file_path.unlink()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Ошибка сохранения файла {file.filename}: {str(e)}"
            )

        # Создаем задачу
        tasks_storage[task_id] = {
            'task_id': task_id,
            'status': 'pending',
            'created_at': datetime.now().isoformat(),
            'started_at': None,
            'completed_at': None,
            'progress': 0,
            'filename': file.filename,
            'file_size': file_size,
            'message': 'Задача в очереди на обработку'
        }

        # Запускаем обработку в фоне
        background_tasks.add_task(process_transcription, task_id, file_path, file.filename)

        logger.info(f"Создана задача {task_id}: {file.filename} ({file_size/1024/1024:.1f} MB)")

        uploaded_tasks.append(UploadResponse(
            task_id=task_id,
            message="Файл успешно загружен и отправлен на обработку",
            filename=file.filename,
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
    status_filter: Optional[str] = None,
    limit: int = 100
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
    try:
        task_id_list = [tid.strip() for tid in task_ids.split(',') if tid.strip()]
    except:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Неверный формат списка task_id. Используйте: task_id1,task_id2,task_id3"
        )
    
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
    
    return TaskResult(
        task_id=task_id,
        status=task['status'],
        filename=task['filename'],
        transcription=task.get('transcription'),
        transcription_with_timecodes=task.get('transcription_timecoded'),
        processing_time=task.get('processing_time'),
        media_duration=task.get('media_duration')
    )


@app.get(
    "/api/v1/download-batch",
    dependencies=[Depends(verify_api_key)]
)
async def download_batch_results(task_ids: str, format: str = "txt"):
    """
    Скачать результаты нескольких задач в ZIP архиве

    - **task_ids**: Список ID задач через запятую (task_id1,task_id2,task_id3)
    - **format**: формат файлов (txt или timecodes)
    """

    # Парсим список task_id
    try:
        task_id_list = [tid.strip() for tid in task_ids.split(',') if tid.strip()]
    except:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Неверный формат списка task_id. Используйте: task_id1,task_id2,task_id3"
        )

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
            detail=f"Задачи не найдены: {', '.join(not_found_ids)}. Всего задач в системе: {len(tasks_storage)}. Используйте GET /api/v1/tasks для получения списка доступных task_id."
        )
    
    if not_completed_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Некоторые задачи еще не завершены: {', '.join(not_completed_ids)}"
        )

    # Создаем ZIP архив в памяти
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for task in valid_tasks:
            task_id = task['task_id']
            result_dir = RESULTS_DIR / task_id
            filename_base = Path(task['filename']).stem

            # Используем оригинальное имя файла (без task_id префикса)
            if format == "timecodes":
                file_path = result_dir / f"{filename_base}_timecodes.txt"
                zip_name = f"{filename_base}_timecodes.txt"
            else:
                file_path = result_dir / f"{filename_base}.txt"
                zip_name = f"{filename_base}.txt"

            # Логируем для отладки
            logger.debug(f"Batch download: ищем файл {file_path}")
            
            # Пробуем найти файл
            found_file = None
            if file_path.exists():
                found_file = file_path
                logger.debug(f"Batch download: файл найден напрямую {file_path}")
            else:
                # Если файл не найден напрямую, пробуем найти через glob
                # Это помогает, если есть проблемы с кодировкой или нормализацией Unicode
                logger.debug(f"Batch download: файл не найден напрямую, ищем через glob")
                
                # Ищем все .txt файлы в директории
                txt_files = list(result_dir.glob("*.txt"))
                logger.debug(f"Batch download: найдено .txt файлов в директории: {len(txt_files)}")
                
                # Ищем файл по базовому имени (без расширения)
                for txt_file in txt_files:
                    file_stem = txt_file.stem
                    # Убираем _timecodes из имени, если это файл с таймкодами
                    if file_stem.endswith("_timecodes"):
                        file_stem = file_stem[:-10]  # Убираем "_timecodes"
                    
                    # Сравниваем базовое имя
                    if file_stem == filename_base:
                        found_file = txt_file
                        logger.debug(f"Batch download: файл найден через glob: {txt_file}")
                        break
                
                # Если не нашли по имени, пробуем найти по паттерну
                if not found_file:
                    pattern_files = list(result_dir.glob(f"*{filename_base}*"))
                    if pattern_files:
                        # Берем первый подходящий файл
                        for pf in pattern_files:
                            if pf.suffix == ".txt":
                                if format == "timecodes" and "_timecodes" in pf.name:
                                    found_file = pf
                                    break
                                elif format != "timecodes" and "_timecodes" not in pf.name:
                                    found_file = pf
                                    break
                        
                        if found_file:
                            logger.debug(f"Batch download: файл найден по паттерну: {found_file}")
            
            if found_file and found_file.exists():
                try:
                    zip_file.write(found_file, zip_name)
                    logger.debug(f"Batch download: добавлен файл {zip_name} из {found_file}")
                except Exception as e:
                    logger.error(f"Batch download: ошибка при добавлении файла {found_file}: {e}")
                    error_msg = f"Ошибка при чтении файла: {str(e)}"
                    zip_file.writestr(zip_name, error_msg)
            else:
                # Если файл не найден, создаем пустой файл с сообщением
                error_msg = f"Результаты для файла {task['filename']} не найдены\n"
                error_msg += f"Искали: {file_path}\n"
                error_msg += f"Папка существует: {result_dir.exists()}\n"
                if result_dir.exists():
                    all_files = list(result_dir.iterdir())
                    error_msg += f"Файлы в папке: {all_files}\n"
                    error_msg += f"Базовое имя файла: {filename_base}\n"
                    error_msg += f"Формат: {format}"
                zip_file.writestr(zip_name, error_msg)
                logger.warning(f"Batch download: файл не найден {file_path}")

    zip_buffer.seek(0)

    # Возвращаем ZIP файл
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"transcription_results_{timestamp}.zip"

    return StreamingResponse(
        iter([zip_buffer.getvalue()]),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.get(
    "/api/v1/tasks/{task_id}/download",
    dependencies=[Depends(verify_api_key)]
)
async def download_result(task_id: str, format: str = "txt"):
    """
    Скачать файл с результатами
    
    - **task_id**: ID задачи
    - **format**: формат файла (txt или timecodes)
    """
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
    
    # Определяем файл
    result_dir = RESULTS_DIR / task_id
    filename_base = Path(task['filename']).stem
    
    # Используем оригинальное имя файла (без task_id префикса)
    if format == "timecodes":
        file_path = result_dir / f"{filename_base}_timecodes.txt"
    else:
        file_path = result_dir / f"{filename_base}.txt"
    
    # Пробуем найти файл
    found_file = None
    if file_path.exists():
        found_file = file_path
    else:
        # Если файл не найден напрямую, пробуем найти через glob
        logger.debug(f"Download: файл не найден напрямую {file_path}, ищем через glob")
        
        # Ищем все .txt файлы в директории
        txt_files = list(result_dir.glob("*.txt"))
        
        # Ищем файл по базовому имени
        for txt_file in txt_files:
            file_stem = txt_file.stem
            # Убираем _timecodes из имени, если это файл с таймкодами
            if file_stem.endswith("_timecodes"):
                file_stem = file_stem[:-10]
            
            # Сравниваем базовое имя
            if file_stem == filename_base:
                if format == "timecodes" and "_timecodes" in txt_file.name:
                    found_file = txt_file
                    break
                elif format != "timecodes" and "_timecodes" not in txt_file.name:
                    found_file = txt_file
                    break
        
        # Если не нашли по имени, пробуем найти по паттерну
        if not found_file:
            pattern_files = list(result_dir.glob(f"*{filename_base}*"))
            for pf in pattern_files:
                if pf.suffix == ".txt":
                    if format == "timecodes" and "_timecodes" in pf.name:
                        found_file = pf
                        break
                    elif format != "timecodes" and "_timecodes" not in pf.name:
                        found_file = pf
                        break
    
    if not found_file or not found_file.exists():
        error_detail = f"Файл результата не найден. Искали: {file_path}"
        if result_dir.exists():
            all_files = list(result_dir.iterdir())
            error_detail += f". Файлы в папке: {all_files}"
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_detail
        )
    
    return FileResponse(
        path=found_file,
        filename=found_file.name,
        media_type="text/plain"
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
    del tasks_storage[task_id]
    
    logger.info(f"Задача {task_id} удалена")
    
    return {"message": "Задача успешно удалена"}


@app.delete(
    "/api/v1/tasks",
    dependencies=[Depends(verify_api_key)]
)
async def delete_all_tasks(status_filter: Optional[str] = None):
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
            detail=f"Неверный статус фильтра. Используйте: completed, failed, pending, all"
        )
    
    # Собираем задачи для удаления
    tasks_to_delete = []
    for task_id, task in tasks_storage.items():
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
            task = tasks_storage[task_id]
            
            # Удаляем загруженный файл
            upload_path = UPLOAD_DIR / f"{task_id}_{task['filename']}"
            if upload_path.exists():
                upload_path.unlink()
            
            # Удаляем результаты
            result_dir = RESULTS_DIR / task_id
            if result_dir.exists():
                shutil.rmtree(result_dir)
            
            # Удаляем из хранилища
            del tasks_storage[task_id]
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

