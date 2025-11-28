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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict
from contextlib import asynccontextmanager

import aiofiles
from fastapi import (
    FastAPI, File, UploadFile, HTTPException, Depends,
    BackgroundTasks, status, Header, Request
)
from fastapi.responses import JSONResponse, FileResponse
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

# API ключи (в продакшене использовать .env или базу данных)
API_KEYS_FILE = Path(__file__).parent / ".api_keys"
VALID_API_KEYS = set()

# Директории
UPLOAD_DIR = Path(__file__).parent / "uploads"
RESULTS_DIR = Path(__file__).parent / "results"
UPLOAD_DIR.mkdir(exist_ok=True)
RESULTS_DIR.mkdir(exist_ok=True)

# Ограничения
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB
MAX_CONCURRENT_TASKS = 3
TASK_CLEANUP_HOURS = 24

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
            
            # Обработка в синхронном коде через executor
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                processor.process_file,
                str(file_path),
                str(output_dir),
                0,
                1
            )
            
            if result['success']:
                # Читаем результаты
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
            logger.error(f"Ошибка при обработке задачи {task_id}: {str(e)}")
            tasks_storage[task_id].update({
                'status': 'failed',
                'completed_at': datetime.now().isoformat(),
                'error': str(e),
                'message': f'Ошибка обработки: {str(e)}'
            })
        
        finally:
            # Удаляем загруженный файл
            if file_path.exists():
                file_path.unlink()


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
    """Корневой эндпоинт"""
    return {
        "service": "GigaAM v3 Transcriber API",
        "version": "3.0.0",
        "status": "running",
        "endpoints": {
            "health": "/health",
            "upload": "/api/v1/transcribe",
            "status": "/api/v1/tasks/{task_id}",
            "result": "/api/v1/tasks/{task_id}/result",
            "download": "/api/v1/tasks/{task_id}/download",
            "tasks": "/api/v1/tasks",
            "docs": "/docs"
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
    
    if format == "timecodes":
        file_path = result_dir / f"{filename_base}_timecodes.txt"
    else:
        file_path = result_dir / f"{filename_base}.txt"
    
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Файл результата не найден"
        )
    
    return FileResponse(
        path=file_path,
        filename=file_path.name,
        media_type="text/plain"
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


# ==================== ЗАПУСК ====================

if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "api:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        log_level="info"
    )

