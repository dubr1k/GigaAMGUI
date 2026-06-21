#!/usr/bin/env python3
"""
GigaAM v3 Transcriber - Web GUI
Веб-интерфейс с авторизацией, полностью дублирующий функционал desktop GUI.
"""

import asyncio
import hashlib
import hmac
import json
import os
import shutil
import time
import traceback
import uuid
import warnings
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final, Literal

import aiofiles
import jwt
import yt_dlp
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Third-party ML libraries emit noisy deprecation/reproducibility warnings on
# supported pinned versions. Keep runtime logs focused on actionable failures.
warnings.filterwarnings("ignore", category=UserWarning, module="pyannote.audio.core.io")
warnings.filterwarnings("ignore", category=UserWarning, module="pyannote.audio.pipelines.speaker_verification")
warnings.filterwarnings("ignore", category=UserWarning, module="pyannote.audio.tasks.segmentation.mixins")
warnings.filterwarnings("ignore", category=UserWarning, module="pyannote.audio.utils.reproducibility")
warnings.filterwarnings("ignore", message=".*speechbrain.pretrained.*deprecated.*", category=UserWarning)
from starlette.middleware.sessions import SessionMiddleware

# Импорты проекта
from src.config import HF_TOKEN, MEDIA_EXTENSIONS, OUTPUT_FORMATS, SUPPORTED_FORMATS
from src.core.model_loader import ModelLoader
from src.core.processor import TranscriptionProcessor
from src.utils.audio_converter import AudioConverter, ffmpeg_available
from src.utils.media_downloader import MediaDownloader
from src.utils.output_naming import find_result_file, output_filename
from src.utils.processing_stats import ProcessingStats
from src.utils.pyannote_patch import apply_pyannote_patch
from src.utils.time_formatter import TimeFormatter

apply_pyannote_patch()

from dotenv import load_dotenv

env_path = Path(__file__).parent.parent / '.env'
if env_path.exists():
    load_dotenv(env_path, override=False)

# ==================== КОНФИГУРАЦИЯ ====================

WEB_PORT = int(os.getenv("WEB_PORT", "8000"))
WEB_SECRET: Final[str] = os.getenv("WEB_SECRET", "")
WEB_USERNAME: Final[str] = os.getenv("WEB_USERNAME", "")
WEB_PASSWORD: Final[str] = os.getenv("WEB_PASSWORD", "")
JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "72"))

if len(WEB_SECRET.encode("utf-8")) < 32:
    raise RuntimeError("WEB_SECRET must be set and contain at least 32 bytes")
if not WEB_USERNAME:
    raise RuntimeError("WEB_USERNAME must be set")
if not WEB_PASSWORD:
    raise RuntimeError("WEB_PASSWORD must be set")

UPLOAD_DIR = Path(__file__).parent.parent / os.getenv("UPLOAD_DIR", "uploads")
RESULTS_DIR = Path(__file__).parent.parent / os.getenv("RESULTS_DIR", "results")
UPLOAD_DIR.mkdir(exist_ok=True)
RESULTS_DIR.mkdir(exist_ok=True)

MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", str(2 * 1024 * 1024 * 1024)))
MAX_CONCURRENT_TASKS = int(os.getenv("MAX_CONCURRENT_TASKS", "3"))

STATIC_DIR = Path(__file__).parent / "static"

# Глобальные переменные
model_loader: ModelLoader | None = None
stats_manager: ProcessingStats | None = None
media_downloader: MediaDownloader | None = None
time_formatter = TimeFormatter()
processing_semaphore: asyncio.Semaphore | None = None

# Хранилище задач
tasks_storage: dict[str, dict] = {}

# Очередь логов для SSE (task_id -> list of log lines)
log_queues: dict[str, list[str]] = {}


# ==================== АВТОРИЗАЦИЯ ====================

def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _create_token(username: str) -> str:
    payload = {
        "sub": username,
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRE_HOURS),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, WEB_SECRET, algorithm="HS256")


def _verify_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, WEB_SECRET, algorithms=["HS256"])
        return payload.get("sub")
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


async def require_auth(request: Request) -> str:
    """Зависимость: проверяет авторизацию через cookie или заголовок."""
    # Проверяем JWT в cookie
    token = request.cookies.get("gigaam_token")
    if token:
        username = _verify_token(token)
        if username:
            return username

    # Проверяем Bearer токен в заголовке
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        username = _verify_token(token)
        if username:
            return username

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Не авторизован",
    )


# ==================== УТИЛИТЫ ====================

def is_supported_format(filename: str) -> bool:
    extensions = MEDIA_EXTENSIONS
    file_ext = Path(filename).suffix.lower()
    return file_ext in extensions


def safe_filename(filename: str | None) -> str:
    name = (filename or "").replace("\\", "/")
    name = name.split("/")[-1]
    name = name.replace("\x00", "").strip()
    return name or "upload"


def _register_task(task_id: str, filename: str, file_size: int):
    tasks_storage[task_id] = {
        'task_id': task_id,
        'status': 'pending',
        'created_at': datetime.now().isoformat(),
        'started_at': None,
        'completed_at': None,
        'progress': 0,
        'filename': filename,
        'file_size': file_size,
        'message': 'В очереди',
        'stage': '',
        'output_formats': [],
        'enable_diarization': False,
        'num_speakers': None,
    }
    log_queues[task_id] = []


def _task_log(task_id: str, message: str):
    """Добавляет сообщение в лог задачи."""
    if task_id in log_queues:
        log_queues[task_id].append(message)


async def _save_upload(file: UploadFile, request: Request) -> tuple:
    if not is_supported_format(file.filename):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Неподдерживаемый формат: {file.filename}. Поддерживаемые: {', '.join(MEDIA_EXTENSIONS)}",
        )

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_FILE_SIZE:
                max_gb = MAX_FILE_SIZE / 1024 / 1024 / 1024
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"Файл слишком большой (макс. {max_gb:.1f} GB)",
                )
        except ValueError:
            pass

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
                        detail=f"Файл {filename} слишком большой",
                    )
                await f.write(chunk)
    except HTTPException:
        if file_path.exists():
            file_path.unlink()
        raise
    except Exception as e:
        if file_path.exists():
            file_path.unlink()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка сохранения: {e}",
        )

    return task_id, file_path, filename, file_size


# ==================== ОБРАБОТКА ====================

async def process_transcription(
    task_id: str,
    file_path: Path,
    filename: str,
    output_formats: list[str],
    enable_diarization: bool,
    num_speakers: int | None,
):
    """Фоновая обработка транскрибации."""
    async with processing_semaphore:
        try:
            if not file_path.exists() or task_id not in tasks_storage:
                return

            tasks_storage[task_id].update({
                'status': 'processing',
                'started_at': datetime.now().isoformat(),
                'progress': 5,
                'stage': 'Подготовка...',
                'message': 'Обработка началась',
            })
            _task_log(task_id, f"Начало обработки: {filename}")

            output_dir = RESULTS_DIR / task_id
            output_dir.mkdir(exist_ok=True)

            def progress_callback(stage: str, progress: float):
                task = tasks_storage.get(task_id)
                if task is None:
                    return
                stage_names = {
                    'conversion': 'Конвертация...',
                    'transcription': 'Распознавание речи...',
                }
                task['stage'] = stage_names.get(stage, stage)
                if stage == 'conversion':
                    task['progress'] = int(5 + progress * 15)
                elif stage == 'transcription':
                    task['progress'] = int(20 + progress * 75)

            def logger(msg: str):
                _task_log(task_id, msg)

            processor = TranscriptionProcessor(
                model_loader=model_loader,
                stats_manager=stats_manager,
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
                    num_speakers=num_speakers,
                ),
            )

            if not result['success']:
                raise Exception("Обработка не удалась")

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

            tasks_storage[task_id].update({
                'status': 'completed',
                'completed_at': datetime.now().isoformat(),
                'progress': 100,
                'stage': 'Готово',
                'message': 'Транскрибация завершена',
                'result_files': result_files,
                'processing_time': result['total_time'],
                'media_duration': result.get('media_duration', 0),
            })
            _task_log(task_id, f"Обработка завершена за {time_formatter.format_duration(result['total_time'])}")

            # Сохраняем meta.json
            try:
                from src.utils.atomic_json import save_json_atomic
                save_json_atomic(str(output_dir / "meta.json"), {
                    'task_id': task_id,
                    'filename': filename,
                    'file_size': tasks_storage[task_id].get('file_size', 0),
                    'created_at': tasks_storage[task_id].get('created_at'),
                    'started_at': tasks_storage[task_id].get('started_at'),
                    'completed_at': tasks_storage[task_id].get('completed_at'),
                    'output_formats': output_formats,
                    'enable_diarization': enable_diarization,
                    'num_speakers': num_speakers,
                })
            except Exception:
                pass

        except Exception as e:
            _task_log(task_id, f"Ошибка: {e}")
            _task_log(task_id, traceback.format_exc())
            if task_id in tasks_storage:
                tasks_storage[task_id].update({
                    'status': 'failed',
                    'completed_at': datetime.now().isoformat(),
                    'stage': 'Ошибка',
                    'message': str(e),
                })

        finally:
            task_status = tasks_storage.get(task_id, {}).get('status', 'unknown')
            if task_status == 'completed' and file_path.exists():
                try:
                    file_path.unlink()
                except OSError:
                    pass


def _detect_format(filename: str, stem: str) -> str:
    """Определяет формат вывода по имени файла."""
    for fmt, label in OUTPUT_FORMATS.items():
        expected = output_filename(stem, fmt)
        if filename == expected:
            return fmt
    return 'unknown'


# ==================== LIFESPAN ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global model_loader, stats_manager, media_downloader, processing_semaphore

    print("=" * 60)
    print("GigaAM v3 Transcriber - Web GUI")
    print("=" * 60)

    if not ffmpeg_available():
        print("ВНИМАНИЕ: ffmpeg/ffprobe не найдены в PATH!")

    if not HF_TOKEN or not HF_TOKEN.startswith("hf_"):
        print("ВНИМАНИЕ: HF_TOKEN не настроен!")

    print("Загрузка модели GigaAM-v3...")
    model_loader = ModelLoader()
    success = model_loader.load_model(logger=print)
    if not success:
        print("ОШИБКА загрузки модели!")
        raise RuntimeError("Ошибка загрузки модели")
    print(f"Модель загружена. Устройство: {model_loader.device.upper()}")

    stats_manager = ProcessingStats(os.getenv("STATS_FILE", str(RESULTS_DIR / "processing_stats.json")))
    media_downloader = MediaDownloader()
    processing_semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

    print(f"Web GUI готов (порт {WEB_PORT}, макс. {MAX_CONCURRENT_TASKS} задач)")
    print("=" * 60)

    yield

    print("Остановка Web GUI...")


# ==================== ПРИЛОЖЕНИЕ ====================

app = FastAPI(
    title="GigaAM v3 Transcriber - Web GUI",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    SessionMiddleware,
    secret_key=WEB_SECRET,
    session_cookie="gigaam_session",
    max_age=72 * 3600,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8001", "http://127.0.0.1:8001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ==================== ЭНДПОИНТЫ АВТОРИЗАЦИИ ====================

class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
async def login(req: LoginRequest):
    if req.username == WEB_USERNAME and hmac.compare_digest(_hash_password(req.password), _hash_password(WEB_PASSWORD)):
        token = _create_token(req.username)
        response = JSONResponse({"ok": True, "username": req.username})
        response.set_cookie(
            key="gigaam_token",
            value=token,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=JWT_EXPIRE_HOURS * 3600,
        )
        return response
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Неверное имя пользователя или пароль",
    )


@app.post("/api/auth/logout")
async def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie("gigaam_token")
    return response


@app.get("/api/auth/check")
async def auth_check(user: str = Depends(require_auth)):
    return {"ok": True, "username": user}


# ==================== ЭНДПОИНТЫ GUI ====================

@app.get("/api/formats")
async def get_formats(user: str = Depends(require_auth)):
    return {"formats": OUTPUT_FORMATS}


@app.get("/api/device")
async def get_device(user: str = Depends(require_auth)):
    if model_loader and model_loader.device:
        return {"device": model_loader.device.upper()}
    return {"device": "CPU"}


@app.post("/api/upload")
async def upload_files(
    request: Request,
    files: list[UploadFile] = File(...),
    output_formats: str = Form("txt,txt_timecodes"),
    enable_diarization: bool = Form(False),
    num_speakers: str = Form(""),
    user: str = Depends(require_auth),
):
    """Загрузка одного или нескольких файлов для транскрибации."""
    if len(files) > 20:
        raise HTTPException(status_code=400, detail="Максимум 20 файлов за раз")

    fmt_list = [f.strip() for f in output_formats.split(",") if f.strip()]
    if not fmt_list:
        fmt_list = ['txt', 'txt_timecodes']

    ns = None
    if num_speakers.strip():
        try:
            ns = int(num_speakers.strip())
            if ns <= 0:
                ns = None
        except ValueError:
            ns = None

    uploaded = []
    for file in files:
        task_id, file_path, filename, file_size = await _save_upload(file, request)
        _register_task(task_id, filename, file_size)
        tasks_storage[task_id]['output_formats'] = fmt_list
        tasks_storage[task_id]['enable_diarization'] = enable_diarization
        tasks_storage[task_id]['num_speakers'] = ns

        asyncio.create_task(
            process_transcription(task_id, file_path, filename, fmt_list, enable_diarization, ns)
        )
        uploaded.append({
            'task_id': task_id,
            'filename': filename,
            'file_size': file_size,
        })

    return {"tasks": uploaded, "total": len(uploaded)}


@app.post("/api/download-url")
async def download_from_url(
    request: Request,
    user: str = Depends(require_auth),
    url: str = Form(...),
    output_formats: str = Form("txt,txt_timecodes"),
    enable_diarization: bool = Form(False),
    num_speakers: str = Form(""),
):
    """Загрузка медиа по URL через yt-dlp и постановка в очередь."""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL должен начинаться с http:// или https://")

    fmt_list = [f.strip() for f in output_formats.split(",") if f.strip()]
    if not fmt_list:
        fmt_list = ['txt', 'txt_timecodes']

    ns = None
    if num_speakers.strip():
        try:
            ns = int(num_speakers.strip())
            if ns <= 0:
                ns = None
        except ValueError:
            ns = None

    task_id = uuid.uuid4().hex
    _register_task(task_id, url.split("/")[-1][:80], 0)
    tasks_storage[task_id]['output_formats'] = fmt_list
    tasks_storage[task_id]['enable_diarization'] = enable_diarization
    tasks_storage[task_id]['num_speakers'] = ns
    tasks_storage[task_id]['status'] = 'downloading'
    tasks_storage[task_id]['stage'] = 'Загрузка медиа...'
    tasks_storage[task_id]['message'] = 'Загрузка по URL'

    asyncio.create_task(
        _download_and_process(task_id, url, fmt_list, enable_diarization, ns)
    )

    return {"task_id": task_id, "url": url}


async def _download_and_process(
    task_id: str,
    url: str,
    output_formats: list[str],
    enable_diarization: bool,
    num_speakers: int | None,
):
    """Скачивает медиа по URL и запускает обработку."""
    try:
        download_dir = UPLOAD_DIR
        download_dir.mkdir(exist_ok=True)

        def progress_hook(data):
            status = data.get("status")
            if status == "downloading":
                total = data.get("total_bytes") or data.get("total_bytes_estimate")
                downloaded = data.get("downloaded_bytes")
                if total and downloaded is not None:
                    pct = max(0, min(95, int(downloaded * 100 / total)))
                    if task_id in tasks_storage:
                        tasks_storage[task_id]['progress'] = pct
            elif status == "finished":
                if task_id in tasks_storage:
                    tasks_storage[task_id]['progress'] = 95

        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": str(download_dir / f"{task_id}_%(title)s.%(ext)s"),
            "progress_hooks": [progress_hook],
            "ignoreerrors": False,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
        }

        loop = asyncio.get_running_loop()

        def do_download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

        await loop.run_in_executor(None, do_download)

        # Найти скачанный файл
        downloaded_files = [
            p for p in download_dir.iterdir()
            if p.name.startswith(f"{task_id}_") and p.is_file()
            and not p.name.endswith((".part", ".ytdl"))
        ]

        if not downloaded_files:
            raise RuntimeError("yt-dlp не вернул файл")

        file_path = downloaded_files[0]
        filename = file_path.name[len(task_id) + 1:]
        file_size = file_path.stat().st_size

        tasks_storage[task_id].update({
            'filename': filename,
            'file_size': file_size,
            'status': 'pending',
            'stage': 'В очереди',
            'progress': 0,
            'message': 'Загрузка завершена, ожидание обработки',
        })
        _task_log(task_id, f"Загружено: {filename} ({file_size / 1024 / 1024:.1f} MB)")

        await process_transcription(task_id, file_path, filename, output_formats, enable_diarization, num_speakers)

    except Exception as e:
        _task_log(task_id, f"Ошибка загрузки: {e}")
        if task_id in tasks_storage:
            tasks_storage[task_id].update({
                'status': 'failed',
                'stage': 'Ошибка загрузки',
                'message': str(e),
                'completed_at': datetime.now().isoformat(),
            })


# ==================== ЭНДПОИНТЫ ЗАДАЧ ====================

@app.get("/api/tasks")
async def list_tasks(user: str = Depends(require_auth)):
    tasks = list(tasks_storage.values())
    tasks.sort(key=lambda x: x['created_at'], reverse=True)
    # Не отправляем тяжёлые данные
    for t in tasks:
        t.pop('result_files', None)
    return {"total": len(tasks), "tasks": tasks}


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str, user: str = Depends(require_auth)):
    if task_id not in tasks_storage:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return tasks_storage[task_id]


@app.get("/api/tasks/{task_id}/logs")
async def get_task_logs(task_id: str, user: str = Depends(require_auth)):
    if task_id not in tasks_storage:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return {"logs": log_queues.get(task_id, [])}


@app.get("/api/tasks/{task_id}/result")
async def get_task_result(task_id: str, user: str = Depends(require_auth)):
    if task_id not in tasks_storage:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    task = tasks_storage[task_id]
    if task['status'] != 'completed':
        raise HTTPException(status_code=400, detail=f"Задача не завершена (статус: {task['status']})")

    result_dir = RESULTS_DIR / task_id
    result_files = []
    if result_dir.exists():
        stem = Path(task['filename']).stem
        for fmt in task.get('output_formats', ['txt', 'txt_timecodes']):
            found = find_result_file(result_dir, stem, fmt)
            if found:
                try:
                    async with aiofiles.open(found, encoding='utf-8') as f:
                        content = await f.read()
                    result_files.append({
                        'format': fmt,
                        'name': found.name,
                        'content': content,
                    })
                except Exception:
                    pass

    return {
        'task_id': task_id,
        'filename': task['filename'],
        'result_files': result_files,
        'processing_time': task.get('processing_time'),
        'media_duration': task.get('media_duration'),
    }


@app.get("/api/tasks/{task_id}/download")
async def download_result_file(
    task_id: str,
    format: str = "txt",
    user: str = Depends(require_auth),
):
    if task_id not in tasks_storage:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    task = tasks_storage[task_id]
    if task['status'] != 'completed':
        raise HTTPException(status_code=400, detail="Задача не завершена")

    result_dir = RESULTS_DIR / task_id
    if not result_dir.exists():
        raise HTTPException(status_code=404, detail="Результаты не найдены")

    stem = Path(task['filename']).stem
    found = find_result_file(result_dir, stem, format)
    if not found:
        raise HTTPException(status_code=404, detail=f"Файл формата {format} не найден")

    return FileResponse(
        path=str(found),
        filename=found.name,
        media_type="application/octet-stream",
    )


@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str, user: str = Depends(require_auth)):
    if task_id not in tasks_storage:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    task = tasks_storage[task_id]
    if task['status'] == 'processing':
        raise HTTPException(status_code=400, detail="Нельзя удалить задачу в процессе обработки")

    upload_path = UPLOAD_DIR / f"{task_id}_{task['filename']}"
    if upload_path.exists():
        upload_path.unlink()

    result_dir = RESULTS_DIR / task_id
    if result_dir.exists():
        shutil.rmtree(result_dir)

    tasks_storage.pop(task_id, None)
    log_queues.pop(task_id, None)

    return {"ok": True, "message": "Задача удалена"}


@app.delete("/api/tasks")
async def delete_all_tasks(
    status_filter: str = "completed,failed",
    user: str = Depends(require_auth),
):
    statuses = set(status_filter.split(","))
    if "all" in statuses:
        statuses = {'pending', 'completed', 'failed', 'downloading'}

    removed = 0
    for tid in list(tasks_storage.keys()):
        task = tasks_storage[tid]
        if task['status'] in statuses and task['status'] != 'processing':
            result_dir = RESULTS_DIR / tid
            if result_dir.exists():
                shutil.rmtree(result_dir)
            tasks_storage.pop(tid, None)
            log_queues.pop(tid, None)
            removed += 1

    return {"ok": True, "removed": removed}


# ==================== SSE ПРОГРЕСС ====================

@app.get("/api/progress")
async def progress_stream(request: Request, user: str = Depends(require_auth)):
    """SSE-стрим прогресса всех задач в реальном времени."""
    import json as json_mod
    from starlette.responses import StreamingResponse

    async def event_generator():
        last_snapshot = {}
        while True:
            if await request.is_disconnected():
                break

            current = {}
            for tid, task in tasks_storage.items():
                current[tid] = {
                    'status': task['status'],
                    'progress': task['progress'],
                    'stage': task.get('stage', ''),
                    'message': task.get('message', ''),
                    'filename': task['filename'],
                }

            # Отправляем только изменившиеся задачи
            changed = {}
            for tid, data in current.items():
                prev = last_snapshot.get(tid)
                if prev != data:
                    changed[tid] = data

            # Новые логи
            new_logs = {}
            for tid, logs in log_queues.items():
                prev_len = last_snapshot.get(f"_logs_{tid}", 0)
                if len(logs) > prev_len:
                    new_logs[tid] = logs[prev_len:]
                    last_snapshot[f"_logs_{tid}"] = len(logs)

            if changed or new_logs:
                yield f"data: {json_mod.dumps({'tasks': changed, 'logs': new_logs})}\n\n"

            last_snapshot.update(current)

            await asyncio.sleep(1.0)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ==================== STATIC ====================

@app.get("/favicon.ico")
async def favicon():
    return FileResponse(STATIC_DIR / "icon.svg", media_type="image/svg+xml")


@app.get("/robots.txt")
async def robots_txt():
    return FileResponse(STATIC_DIR / "robots.txt", media_type="text/plain")


@app.get("/", response_class=HTMLResponse)
async def index():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return HTMLResponse(index_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>GigaAM Web GUI</h1><p>index.html not found</p>", status_code=404)


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "model_loaded": model_loader is not None and model_loader.is_loaded(),
        "device": model_loader.device.upper() if model_loader else "N/A",
        "active_tasks": sum(1 for t in tasks_storage.values() if t['status'] in ('processing', 'downloading')),
        "total_tasks": len(tasks_storage),
    }


# ==================== ЗАПУСК ====================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "web.web_app:app",
        host="0.0.0.0",
        port=WEB_PORT,
        reload=False,
    )
