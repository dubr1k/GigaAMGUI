#!/usr/bin/env python3
"""
GigaAM v3 Transcriber - Web GUI
Веб-интерфейс с авторизацией, полностью дублирующий функционал desktop GUI.
"""

import asyncio
import hashlib
import hmac
import logging
import os
import shutil
import traceback
import uuid
import warnings
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from platform import machine
from platform import platform as runtime_platform
from typing import Final
from urllib.parse import urlsplit

import aiofiles
import jwt
from dotenv import load_dotenv
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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from limits import parse_many
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from src.config import AUDIO_PREPROCESSING_MODE, HF_TOKEN, MEDIA_EXTENSIONS, OUTPUT_FORMATS
from src.core.asr.models import ASR_MODELS
from src.core.model_loader import ModelLoader
from src.core.subtitles import SubtitleOptions
from src.services import cli_tools, file_policy, llm_service, mcp_backend, task_store, transcription_service
from src.services import health as health_service
from src.services import llm_settings as llm_settings_service
from src.services.api_keys import KeyStore
from src.services.mcp_backend import LocalBackend
from src.services.mcp_http import backend_options_from_env, mount_mcp
from src.utils.atomic_json import load_json, save_json_atomic
from src.utils.audio_converter import ffmpeg_available
from src.utils.diarization import normalize_diarization_backend
from src.utils.media_downloader import MediaDownloader
from src.utils.output_naming import find_result_file, output_filename
from src.utils.processing_stats import ProcessingStats
from src.utils.time_formatter import TimeFormatter

# Third-party ML libraries emit noisy deprecation/reproducibility warnings on
# supported pinned versions. Keep runtime logs focused on actionable failures.
warnings.filterwarnings("ignore", category=UserWarning, module="pyannote.audio.core.io")
warnings.filterwarnings("ignore", category=UserWarning, module="pyannote.audio.pipelines.speaker_verification")
warnings.filterwarnings("ignore", category=UserWarning, module="pyannote.audio.tasks.segmentation.mixins")
warnings.filterwarnings("ignore", category=UserWarning, module="pyannote.audio.utils.reproducibility")
warnings.filterwarnings("ignore", message=".*speechbrain.pretrained.*deprecated.*", category=UserWarning)

if HF_TOKEN and HF_TOKEN.startswith("hf_"):
    try:
        from src.utils.pyannote_patch import apply_pyannote_patch
        apply_pyannote_patch()
    except Exception:
        print("ПРЕДУПРЕЖДЕНИЕ: pyannote patch не применен; продолжим без него.")

env_path = Path(__file__).parent.parent / '.env'
if env_path.exists():
    load_dotenv(env_path, override=False)

# ==================== КОНФИГУРАЦИЯ ====================

WEB_PORT = int(os.getenv("WEB_PORT", "8000"))
WEB_SECRET: Final[str] = os.getenv("WEB_SECRET", "")
WEB_USERNAME: Final[str] = os.getenv("WEB_USERNAME", "")
WEB_PASSWORD: Final[str] = os.getenv("WEB_PASSWORD", "")
JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "72"))
# secure-cookie требует HTTPS (или localhost). За TLS-прокси/на HTTPS оставляем
# True; при доступе по чистому HTTP с не-localhost домена браузер молча выкинет
# cookie и логин зациклится — тогда выставите COOKIE_SECURE=0.
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "1").strip().lower() not in ("0", "false", "no")


def _validated_login_rate_limit(value: str | None) -> str:
    """Лимит попыток входа; неразборная строка — отказ при старте, а не молча без лимита
    (slowapi при ошибке разбора лимит просто не применяет)."""
    limit = (value or "").strip() or "10/minute"
    try:
        parse_many(limit)
    except ValueError as exc:
        raise ValueError(f"WEB_LOGIN_RATE_LIMIT={limit!r} is not a valid rate limit (e.g. '10/minute').") from exc
    return limit


WEB_LOGIN_RATE_LIMIT = _validated_login_rate_limit(os.getenv("WEB_LOGIN_RATE_LIMIT"))
# Origin-ы (scheme://host[:port]) кроме самой панели, которым можно слать изменяющие
# запросы с cookie сессии и читать API через CORS (фронтенд разработки). Обычно пусто.
WEB_TRUSTED_ORIGINS: tuple[str, ...] = tuple(
    origin.strip().rstrip("/") for origin in os.getenv("WEB_TRUSTED_ORIGINS", "").split(",") if origin.strip()
)

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
# Транскрипты для LLM-вкладки — текст; лимит на всё тело запроса /api/llm/process
MAX_LLM_BODY_SIZE = int(os.getenv("WEB_MAX_LLM_BODY_SIZE", str(50 * 1024 * 1024)))
MAX_CONCURRENT_TASKS = int(os.getenv("MAX_CONCURRENT_TASKS", "3"))
# 1 — пути/аргументы CLI и «инструменты агента» из LLM-формы принимаются как есть
# (прежнее поведение: сессия = запуск любой команды на сервере). По умолчанию выкл.
WEB_ALLOW_CLIENT_LLM_CLI = os.getenv("WEB_ALLOW_CLIENT_LLM_CLI", "").strip().lower() in ("1", "true", "yes")
# Ключи для /mcp — тот же файл, что у api.py (в контейнере API_KEYS_FILE=/data/.api_keys, persist)
API_KEYS_FILE = Path(__file__).parent.parent / os.getenv("API_KEYS_FILE", ".api_keys")

STATIC_DIR = Path(__file__).parent / "static"

# Глобальные переменные
model_loader: ModelLoader | None = None
stats_manager: ProcessingStats | None = None
media_downloader: MediaDownloader | None = None
time_formatter = TimeFormatter()
processing_semaphore: asyncio.Semaphore | None = None
key_store: KeyStore | None = None

# Хранилище задач
tasks_storage: dict[str, dict] = {}
TASKS_INDEX_PATH: Final[Path] = RESULTS_DIR / ".tasks_index.json"
DELETED_TASKS_PATH: Final[Path] = RESULTS_DIR / ".deleted_tasks.json"
TASK_RECOVERY_MESSAGE: Final[str] = "Сервер перезапустился во время обработки задачи"
ACTIVE_TASK_STATUSES: Final[set[str]] = {'pending', 'downloading', 'processing'}
ALL_TASK_STATUSES: Final[set[str]] = {'pending', 'downloading', 'processing', 'completed', 'failed'}
deleted_task_ids: set[str] = set()
LLM_RESULTS_DIR = RESULTS_DIR / "llm"
LLM_RESULTS_DIR.mkdir(exist_ok=True)
SUMMARY_PROMPT = (
    "Ты аналитик встреч и голосовых сообщений. Сделай сильную, плотную и полезную выжимку транскрипта на русском языке. "
    "Убери повторы, слова-паразиты и шум распознавания. Сохрани только смысл.\n\n"
    "Структура ответа:\n1. Краткое резюме в 3-6 пунктах.\n2. Ключевые договоренности и решения.\n3. Важные факты, цифры, сроки, имена и роли — если они есть.\n4. Риски, спорные места или открытые вопросы — если они есть.\n\n"
    "Пиши четко, по делу, без воды."
)
TASKS_PROMPT = (
    "Ты project manager assistant. Из транскрипта выдели только конкретные задачи и оформи их в максимально рабочем виде на русском языке. "
    "Игнорируй рассуждения, повторы и фоновые фразы. Не выдумывай задачи, которых нет в тексте.\n\n"
    "Для каждой задачи укажи: что нужно сделать; кто ответственный, если можно понять; срок; важный контекст; приоритет. "
    "Если задач нет — напиши: «Явных задач не найдено»."
)

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


def _asr_health() -> dict[str, object]:
    return health_service.asr_health(model_loader)


def _runtime_info() -> dict[str, object]:
    return health_service.runtime_info(runtime_platform, machine)


def _bearer_user(request: Request) -> str | None:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return _verify_token(auth_header[7:])
    return None


def _authenticated_user(request: Request) -> str | None:
    """Пользователь из JWT в cookie или в `Authorization: Bearer`; None — не авторизован."""
    token = request.cookies.get("gigaam_token")
    if token:
        username = _verify_token(token)
        if username:
            return username
    return _bearer_user(request)


def _hostname(value: str | None) -> str | None:
    """Имя хоста без порта из `Host`/`X-Forwarded-Host` (первого из списка)."""
    if not value:
        return None
    try:
        return urlsplit("//" + value.split(",")[0].strip()).hostname
    except ValueError:
        return None


def _same_origin(request: Request) -> bool:
    """Запрос пришёл со страницы самой панели (или из WEB_TRUSTED_ORIGINS).

    Главный признак — `Sec-Fetch-Site`: его ставит сам браузер (страница
    подделать не может), и он не зависит от того, передаёт ли reverse proxy
    `Host` (nginx без `proxy_set_header Host` шлёт 127.0.0.1:8001).
    Старые браузеры его не шлют — тогда источник берётся из Origin, иначе
    Referer, и сравнивается только имя хоста с `Host`/`X-Forwarded-Host`:
    схема и порт за TLS-прокси у браузера и у приложения разные. Без Origin и
    Referer запрос не из браузера (curl, скрипт): CSRF — атака через чужой
    браузер, а он на POST/DELETE шлёт Origin.
    """
    source = request.headers.get("origin") or request.headers.get("referer")
    source_origin = None
    source_host = None
    if source and source != "null":
        try:
            parsed = urlsplit(source)
            source_origin = f"{parsed.scheme}://{parsed.netloc}"
            source_host = parsed.hostname
        except ValueError:
            pass
    if source_origin in WEB_TRUSTED_ORIGINS:
        return True

    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None:
        return fetch_site in ("same-origin", "none")

    if not source:
        return True
    own_hosts = {_hostname(request.headers.get("host")), _hostname(request.headers.get("x-forwarded-host"))}
    return source_host is not None and source_host in own_hosts - {None}


async def require_auth(request: Request) -> str:
    """Зависимость: проверяет авторизацию через cookie или заголовок."""
    username = _authenticated_user(request)
    if username:
        return username
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Не авторизован",
    )


# Запас над лимитом тела на multipart-обвязку (как в api.py)
_CONTENT_LENGTH_SLACK = 1024 * 1024
# Тело запросов /api без файлов (форма URL, проверка CLI и т.п.)
_SMALL_BODY_LIMIT = 1024 * 1024
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# Без сессии: вход и выход
_AUTH_EXEMPT_PATHS = frozenset({"/api/auth/login", "/api/auth/logout"})


def _body_limit(path: str) -> int:
    if path == "/api/upload":
        return MAX_FILE_SIZE + _CONTENT_LENGTH_SLACK
    if path == "/api/llm/process":
        return MAX_LLM_BODY_SIZE + _CONTENT_LENGTH_SLACK
    return _SMALL_BODY_LIMIT


class _BodyGuard:
    """Авторизация, источник и Content-Length изменяющих запросов /api ДО чтения тела.

    Чистый ASGI, как `_UploadGuard` в api.py. FastAPI разбирает тело формы
    (multipart спулится во временную директорию — в Docker это tmpfs /tmp)
    раньше, чем выполняется Depends(require_auth), поэтому без гарда любой
    без сессии мог залить сколько угодно байт и получить 401 только после
    записи. Здесь — та же проверка JWT, что у require_auth, и лимит по
    заголовку; точный размер файла по-прежнему считает _save_upload при
    записи (тело без Content-Length, chunked, проверяется только им).

    Запрос, авторизованный cookie, должен прийти со страницы панели
    (`_same_origin`), иначе 403: cookie браузер подставит и в запрос с чужого
    сайта. SameSite=Lax этого не закрывает для соседних поддоменов (один
    «сайт»). Действующий `Authorization: Bearer` проверку снимает — его
    чужая страница подставить не может; мусорный Bearer при живой cookie — нет.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] == "http"
            and scope.get("method") in _UNSAFE_METHODS
            and scope.get("path", "").startswith("/api/")
            and scope.get("path") not in _AUTH_EXEMPT_PATHS
        ):
            request = Request(scope)
            if _authenticated_user(request) is None:
                await JSONResponse({"detail": "Не авторизован"}, status_code=401)(scope, receive, send)
                return
            if _bearer_user(request) is None and not _same_origin(request):
                await JSONResponse(
                    {"detail": "Запрос с чужого источника (Origin) отклонён; см. WEB_TRUSTED_ORIGINS"},
                    status_code=403,
                )(scope, receive, send)
                return
            content_length = request.headers.get("content-length", "")
            limit = _body_limit(scope["path"])
            if content_length.isdigit() and int(content_length) > limit:
                await JSONResponse(
                    {"detail": f"Запрос слишком большой (макс. {limit / 1024 / 1024:.0f} MB)"},
                    status_code=413,
                )(scope, receive, send)
                return
        await self.app(scope, receive, send)


# ==================== УТИЛИТЫ ====================

def is_supported_format(filename: str) -> bool:
    return file_policy.is_supported_by_set(filename, MEDIA_EXTENSIONS)


def safe_filename(filename: str | None) -> str:
    return file_policy.safe_filename(filename)


def _persist_tasks_index() -> None:
    save_json_atomic(str(TASKS_INDEX_PATH), tasks_storage)


def _persist_deleted_task_ids() -> None:
    save_json_atomic(str(DELETED_TASKS_PATH), sorted(deleted_task_ids))


def _restore_deleted_task_ids() -> None:
    raw_deleted = load_json(str(DELETED_TASKS_PATH), [])
    if isinstance(raw_deleted, list):
        deleted_task_ids.update(task_id for task_id in raw_deleted if isinstance(task_id, str))


def _task_result_dir(task_id: str) -> Path:
    return RESULTS_DIR / task_id


def _remove_task_files(task_id: str, filename: str | None = None) -> None:
    for upload_path in UPLOAD_DIR.glob(f"{task_id}_*"):
        if upload_path.is_dir():
            # `{task_id}_download` — загрузка по URL, оборванная падением сервера
            shutil.rmtree(upload_path, ignore_errors=True)
        elif upload_path.is_file():
            try:
                upload_path.unlink()
            except OSError:
                pass

    if filename:
        exact_upload = UPLOAD_DIR / f"{task_id}_{filename}"
        if exact_upload.exists():
            try:
                exact_upload.unlink()
            except OSError:
                pass

    result_dir = _task_result_dir(task_id)
    if result_dir.exists():
        shutil.rmtree(result_dir, ignore_errors=True)


def _delete_task_data(task_id: str, task: dict) -> None:
    if task.get('status') in ACTIVE_TASK_STATUSES:
        deleted_task_ids.add(task_id)
        _persist_deleted_task_ids()
    filename = task.get('filename')
    _remove_task_files(task_id, filename if isinstance(filename, str) else None)


def _finalize_deleted_task(task_id: str, filename: str | None = None) -> None:
    _remove_task_files(task_id, filename)
    deleted_task_ids.discard(task_id)
    _persist_deleted_task_ids()


def _cleanup_deleted_task_tombstones() -> None:
    if not deleted_task_ids:
        return

    raw_index = load_json(str(TASKS_INDEX_PATH), {})
    index_changed = False
    if isinstance(raw_index, dict):
        for task_id in list(deleted_task_ids):
            if task_id in raw_index:
                raw_index.pop(task_id, None)
                index_changed = True
    if index_changed:
        save_json_atomic(str(TASKS_INDEX_PATH), raw_index)

    for task_id in list(deleted_task_ids):
        _remove_task_files(task_id)
        tasks_storage.pop(task_id, None)
        log_queues.pop(task_id, None)
        deleted_task_ids.discard(task_id)
    _persist_deleted_task_ids()


def _register_task(
    task_id: str,
    filename: str,
    file_size: int,
    user: str,
    asr_selection: transcription_service.AsrSelection | None = None,
):
    asr_fields = asr_selection.as_dict() if asr_selection is not None else {}
    tasks_storage[task_id] = task_store.new_task_record(
        task_id, filename, file_size, message="В очереди",
        extra={
            "stage": "",
            "output_formats": [],
            "enable_diarization": False,
            "diarization_backend": "pyannote",
            "num_speakers": None,
            "user": user,
            **asr_fields,
        },
    )
    log_queues[task_id] = []
    _persist_tasks_index()


def _task_log(task_id: str, message: str):
    """Добавляет сообщение в лог задачи."""
    if task_id in log_queues:
        log_queues[task_id].append(message)


def _user_task_or_404(task_id: str, user: str):
    task = tasks_storage.get(task_id)
    if task is None or task.get('user') != user:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return task


def _visible_task_copy(task: dict) -> dict:
    task_copy = dict(task)
    task_copy.pop('result_files', None)
    return task_copy


def _restore_completed_task_from_meta(task_dir: Path) -> dict | None:
    meta = load_json(str(task_dir / "meta.json"), None)
    if not isinstance(meta, dict):
        return None

    filename = meta.get('filename')
    user = meta.get('user')
    if not isinstance(filename, str):
        return None
    if not isinstance(user, str) or not user:
        user = WEB_USERNAME

    task_id = task_dir.name
    created_at = meta.get('created_at')
    if not isinstance(created_at, str):
        created_at = datetime.fromtimestamp(task_dir.stat().st_mtime).isoformat()
    started_at = meta.get('started_at', created_at)
    completed_at = meta.get('completed_at', started_at)
    if not isinstance(started_at, str):
        started_at = created_at
    if not isinstance(completed_at, str):
        completed_at = started_at

    task = {
        'task_id': task_id,
        'status': 'completed',
        'created_at': created_at,
        'started_at': started_at,
        'completed_at': completed_at,
        'progress': 100,
        'stage_progress': 1.0,
        'processed_seconds': meta.get('media_duration') if isinstance(meta.get('media_duration'), (int, float)) else None,
        'total_seconds': meta.get('media_duration') if isinstance(meta.get('media_duration'), (int, float)) else None,
        'progress_indeterminate': False,
        'filename': filename,
        'file_size': meta.get('file_size', 0),
        'message': 'Задача восстановлена из результатов при запуске Web GUI',
        'stage': 'Готово',
        'output_formats': meta.get('output_formats') if isinstance(meta.get('output_formats'), list) else ['txt', 'txt_timecodes'],
        'enable_diarization': meta.get('enable_diarization', False),
        'diarization_backend': meta.get('diarization_backend', 'pyannote'),
        'num_speakers': meta.get('num_speakers'),
        'subtitle_options': (
            dict(meta['subtitle_options'])
            if isinstance(meta.get('subtitle_options'), dict)
            else {
                'sentence_split': True,
                'max_line_count': 2,
                'max_line_width': 64,
            }
        ),
        'asr_backend': meta.get('asr_backend'),
        'asr_model': meta.get('asr_model'),
        'onnx_provider': meta.get('onnx_provider'),
        'asr_diagnostics': meta.get('asr_diagnostics'),
        'user': user,
    }
    if isinstance(meta.get('processing_time'), (int, float)):
        task['processing_time'] = meta['processing_time']
    if isinstance(meta.get('media_duration'), (int, float)):
        task['media_duration'] = meta['media_duration']
    return task


def _restore_tasks_from_index() -> bool:
    raw_index = load_json(str(TASKS_INDEX_PATH), {})
    if not isinstance(raw_index, dict):
        return False

    restored_any = False
    changed = False
    now_iso = datetime.now().isoformat()

    for task_id, raw_task in raw_index.items():
        if not isinstance(task_id, str) or not isinstance(raw_task, dict):
            continue

        task = dict(raw_task)
        task['task_id'] = task_id

        user = task.get('user')
        if not isinstance(user, str) or not user:
            task['user'] = WEB_USERNAME
            changed = True

        if task.get('status') in ACTIVE_TASK_STATUSES:
            task['status'] = 'failed'
            task['completed_at'] = now_iso
            task['stage'] = 'Ошибка'
            task['message'] = TASK_RECOVERY_MESSAGE
            task['error'] = TASK_RECOVERY_MESSAGE
            changed = True

        task.setdefault('stage_progress', None)
        task.setdefault('processed_seconds', None)
        task.setdefault('total_seconds', None)
        task.setdefault('progress_indeterminate', False)

        tasks_storage[task_id] = task
        log_queues[task_id] = []
        restored_any = True

    if changed:
        _persist_tasks_index()

    return restored_any


def _restore_tasks_from_results() -> bool:
    restored_any = False
    if not RESULTS_DIR.exists():
        return False

    for task_dir in RESULTS_DIR.iterdir():
        if not task_dir.is_dir():
            continue
        task = _restore_completed_task_from_meta(task_dir)
        if task is None:
            continue
        task_id = task['task_id']
        if task_id in tasks_storage:
            continue
        tasks_storage[task_id] = task
        log_queues[task_id] = []
        restored_any = True

    if restored_any:
        _persist_tasks_index()

    return restored_any


def _restore_persisted_tasks() -> None:
    _restore_deleted_task_ids()
    _cleanup_deleted_task_tombstones()
    _restore_tasks_from_index()
    _restore_tasks_from_results()


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
        ) from e

    return task_id, file_path, filename, file_size


# ==================== ОБРАБОТКА ====================

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
    if processing_semaphore is None:
        raise RuntimeError("Семафор обработки не инициализирован")

    # Проверка loader-а живёт внутри try ниже: подняв её сюда, мы бы убили
    # фоновую задачу до создания обработчика ошибок, и запись в tasks_storage
    # навсегда осталась бы в статусе pending.
    request_loader = model_loader
    owns_loader = False
    async with processing_semaphore:
        try:
            if model_loader is None:
                raise RuntimeError("ASR model loader не инициализирован")
            asr_selection = asr_selection or transcription_service.normalize_asr_selection(
                model_loader
            )
            if not file_path.exists() or task_id not in tasks_storage:
                return

            tasks_storage[task_id].update({
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
            _persist_tasks_index()
            _task_log(task_id, f"Начало обработки: {filename}")

            output_dir = _task_result_dir(task_id)
            output_dir.mkdir(exist_ok=True)

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
                _task_log(task_id, msg)

            if model_loader is None:
                raise RuntimeError("ASR model loader не инициализирован")
            request_loader, owns_loader = transcription_service.acquire_request_model_loader(
                model_loader,
                asr_selection,
                loader_factory=ModelLoader,
            )
            if owns_loader:
                loop = asyncio.get_running_loop()
                loaded = await loop.run_in_executor(None, lambda: request_loader.load_model(logger=logger))
                if not loaded:
                    raise RuntimeError("Не удалось загрузить выбранный ASR backend")

            processor = transcription_service.build_processor(
                request_loader,
                stats_manager,
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

            if task_id not in tasks_storage:
                if task_id in deleted_task_ids:
                    _finalize_deleted_task(task_id, filename)
                return

            if task_id in deleted_task_ids:
                _finalize_deleted_task(task_id, filename)
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

            tasks_storage[task_id].update({
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
            _persist_tasks_index()
            _task_log(task_id, f"Обработка завершена за {time_formatter.format_duration(result['total_time'])}")

            if task_id in deleted_task_ids or task_id not in tasks_storage:
                if task_id in deleted_task_ids:
                    _finalize_deleted_task(task_id, filename)
                return

            # Сохраняем meta.json
            try:
                save_json_atomic(str(output_dir / "meta.json"), {
                    'task_id': task_id,
                    'filename': filename,
                    'file_size': tasks_storage[task_id].get('file_size', 0),
                    'created_at': tasks_storage[task_id].get('created_at'),
                    'started_at': tasks_storage[task_id].get('started_at'),
                    'completed_at': tasks_storage[task_id].get('completed_at'),
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
                    'user': tasks_storage[task_id].get('user'),
                    'processing_time': result['total_time'],
                    'media_duration': result.get('media_duration', 0),
                    'audio_preprocessing_mode': AUDIO_PREPROCESSING_MODE,
                    'audio_preprocessing': result.get('audio_preprocessing'),
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
            _persist_tasks_index()

        finally:
            if owns_loader and request_loader is not None:
                request_loader.unload()
            task_status = tasks_storage.get(task_id, {}).get('status', 'unknown')
            if task_status == 'completed' and file_path.exists():
                try:
                    file_path.unlink()
                except OSError:
                    pass
            if task_id in deleted_task_ids:
                _finalize_deleted_task(task_id, filename)


def _detect_format(filename: str, stem: str) -> str:
    """Определяет формат вывода по имени файла."""
    for fmt, _label in OUTPUT_FORMATS.items():
        expected = output_filename(stem, fmt)
        if filename == expected:
            return fmt
    return 'unknown'


# ==================== LIFESPAN ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global model_loader, stats_manager, media_downloader, processing_semaphore, key_store

    print("=" * 60)
    print("GigaAM v3 Transcriber - Web GUI")
    print("=" * 60)

    # Ключи /mcp; при первом старте создаётся и печатается (один раз) первый ключ
    key_store = KeyStore(API_KEYS_FILE).load()

    if not ffmpeg_available():
        print("ВНИМАНИЕ: ffmpeg/ffprobe не найдены в PATH!")

    if not HF_TOKEN or not HF_TOKEN.startswith("hf_"):
        print("ВНИМАНИЕ: HF_TOKEN не настроен!")
        print("Диаризация будет недоступна без HF_TOKEN.")

    print("Загрузка модели GigaAM-v3...")
    model_loader = ModelLoader()
    success = model_loader.load_model(logger=print)
    if not success:
        print("ОШИБКА загрузки модели!")
        raise RuntimeError("Ошибка загрузки модели")
    device_name = model_loader.device.upper() if model_loader.device else "N/A"
    print(f"Модель загружена. Устройство: {device_name}")

    stats_manager = ProcessingStats(os.getenv("STATS_FILE", str(RESULTS_DIR / "processing_stats.json")))
    media_downloader = MediaDownloader()
    processing_semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

    _restore_persisted_tasks()

    print(f"Web GUI готов (порт {WEB_PORT}, макс. {MAX_CONCURRENT_TASKS} задач)")
    print("=" * 60)

    yield

    print("Остановка Web GUI...")


# ==================== MCP ====================
# /mcp — Streamable HTTP MCP-сервера над тем же model_loader и семафором; вход только по
# API-ключу (сессии веб-панели здесь не действуют). Бэкенд строится после lifespan.


def _mcp_backend() -> LocalBackend:
    return LocalBackend(
        model_loader=model_loader, stats_manager=stats_manager, semaphore=processing_semaphore,
        upload_dir=UPLOAD_DIR, media_downloader=media_downloader, loader_factory=ModelLoader,
        logger=logging.getLogger("GigaAM"),  # веб-панель логгер не настраивает: ошибки уходят в stderr
        http_mode=True, max_file_size=MAX_FILE_SIZE, hf_token=HF_TOKEN, max_concurrent=MAX_CONCURRENT_TASKS,
        **backend_options_from_env())


# ==================== ПРИЛОЖЕНИЕ ====================

app = FastAPI(
    title="GigaAM v3 Transcriber - Web GUI",
    version="1.0.0",
    lifespan=lifespan,
)
limiter = Limiter(key_func=get_remote_address, headers_enabled=True)
app.state.limiter = limiter

app.add_middleware(_BodyGuard)

# Панель и её API — один origin, CORS ей не нужен. Отдельно поднятый фронтенд
# (разработка) перечисляется в WEB_TRUSTED_ORIGINS; раньше здесь был зашитый
# localhost:8001 с credentials — любая страница на этом порту читала API панели.
# Добавляется после _BodyGuard, т.е. снаружи: его 401/403/413 тоже с CORS-заголовками.
if WEB_TRUSTED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(WEB_TRUSTED_ORIGINS),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

mount_mcp(app, "/mcp", _mcp_backend, lambda: key_store)


# ==================== ЭНДПОИНТЫ АВТОРИЗАЦИИ ====================

class LoginRequest(BaseModel):
    username: str
    password: str


def _server_llm_settings(client: dict) -> dict:
    """Настройки для llm_service: бинари CLI решает сервер, а не форма.

    Клиент выбирает провайдера, поля API (url/ключ/модель/temperature) и
    внутреннего провайдера pi/omp. Пути и аргументы CLI берутся из настроек
    сервера (`llm_settings.resolve`: реестр cli_tools + файлы настроек), а
    инструменты агента выключены — как у MCP в HTTP-режиме. Иначе укравший
    cookie запускал в контейнере что угодно: `other_path=/bin/sh`,
    `claude_args=...` или `llm_allow_tools`. Оператор может вернуть прежнее
    поведение переменной WEB_ALLOW_CLIENT_LLM_CLI=1. Вызывать в потоке: первый
    resolve() сканирует CLI (`--version`).
    """
    if WEB_ALLOW_CLIENT_LLM_CLI:
        return dict(client)
    server = llm_settings_service.resolve()
    settings = {key: client[key] for key in ("provider", "api_url", "api_key", "model", "temperature")}
    for spec in cli_tools.PROVIDERS:
        if spec.id == "api":
            continue
        prefix = spec.settings_prefix
        settings[f"{prefix}_path"] = server.get(f"{prefix}_path") or spec.binary or ""
        settings[f"{prefix}_args"] = server.get(f"{prefix}_args") or ""
        if spec.has_provider_field:
            settings[f"{prefix}_provider"] = client.get(f"{prefix}_provider", "")
    settings["llm_allow_tools"] = False
    return settings


def _server_tool_override(spec) -> str | None:
    """Путь к CLI из настроек сервера (для проверки `--version`), а не из формы."""
    return cli_tools.overrides_from_settings(llm_settings_service.resolve()).get(spec.id)


def _run_llm_provider(llm_settings: dict, transcript_text: str, prompt: str) -> str:
    raw = llm_settings.get("provider", "API")
    # web исторически распознавал русский ключ "Другое" (англ. "Other" фронтенд не шлёт).
    provider = "Other" if raw == "Другое" else raw
    try:
        return llm_service.run_provider(
            llm_settings, transcript_text, prompt,
            provider=provider, strict_empty_cli=False,
        )
    except llm_service.UnknownLLMProvider as exc:
        raise RuntimeError(f"Неизвестный провайдер: {exc.provider}") from exc


@app.exception_handler(RateLimitExceeded)
async def _login_rate_limited(request: Request, exc: RateLimitExceeded):
    response = JSONResponse({"detail": "Слишком много попыток входа. Повторите позже."}, status_code=429)
    # Retry-After / X-RateLimit-* — как у api.py
    view_rate_limit = getattr(request.state, "view_rate_limit", None)
    if view_rate_limit is not None:
        response = request.app.state.limiter._inject_headers(response, view_rate_limit)
    return response


@app.post("/api/auth/login")
@limiter.limit(WEB_LOGIN_RATE_LIMIT)
async def login(request: Request, req: LoginRequest):
    # Лимит считает все попытки с адреса: без него пароль единственной учётной
    # записи перебирался со скоростью сети. За прокси без доверенного
    # X-Forwarded-For адрес у всех общий — лимит тогда общий на панель.
    if req.username == WEB_USERNAME and hmac.compare_digest(_hash_password(req.password), _hash_password(WEB_PASSWORD)):
        token = _create_token(req.username)
        response = JSONResponse({"ok": True, "username": req.username})
        response.set_cookie(
            key="gigaam_token",
            value=token,
            httponly=True,
            secure=COOKIE_SECURE,
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


@app.get("/api/asr-options")
async def get_asr_options(user: str = Depends(require_auth)):
    if model_loader is None:
        raise HTTPException(status_code=503, detail="ASR model loader не инициализирован")
    return {
        "backends": transcription_service.available_asr_backends(),
        "models": ASR_MODELS,
        "onnx_providers": list(transcription_service.ONNX_PROVIDERS),
        "defaults": transcription_service.normalize_asr_selection(model_loader).as_dict(),
        "active": model_loader.diagnostics(),
    }


@dataclass(frozen=True)
class TranscribeForm:
    """Проверенные параметры формы транскрибации (общие у /api/upload и /api/download-url)."""

    output_formats: list[str]
    enable_diarization: bool
    diarization_backend: str
    num_speakers: int | None
    asr_selection: transcription_service.AsrSelection
    subtitle_options: SubtitleOptions

    def task_fields(self) -> dict:
        """Поля записи задачи, которые задаёт форма."""
        return {
            'output_formats': self.output_formats,
            'enable_diarization': self.enable_diarization,
            'diarization_backend': self.diarization_backend,
            'num_speakers': self.num_speakers,
            'subtitle_options': {
                'sentence_split': self.subtitle_options.sentence_split,
                'max_line_count': self.subtitle_options.max_line_count,
                'max_line_width': self.subtitle_options.max_line_width,
            },
        }


def _parse_transcribe_form(
    *,
    output_formats: str,
    enable_diarization: bool,
    diarization_backend: str,
    num_speakers: str,
    asr_backend: str,
    asr_model: str,
    onnx_provider: str,
    subtitle_sentence_split: bool,
    subtitle_max_lines: int,
    subtitle_max_width: int,
) -> TranscribeForm:
    """Разбор и проверка параметров формы; ошибки — HTTP 400."""
    fmt_list = [f.strip() for f in output_formats.split(",") if f.strip()]
    if not fmt_list:
        fmt_list = ['txt', 'txt_timecodes']
    unknown = [fmt for fmt in fmt_list if fmt not in OUTPUT_FORMATS]
    if unknown:
        # Иначе мусорный формат попадает в индекс, и /result и /download этой задачи
        # навсегда отвечают 500 (output_filename бросает ValueError)
        raise HTTPException(
            status_code=400,
            detail=f"Неизвестный формат вывода: {', '.join(unknown)}. Доступны: {', '.join(OUTPUT_FORMATS)}",
        )
    try:
        diarization_backend = normalize_diarization_backend(diarization_backend)
        asr_selection = transcription_service.normalize_asr_selection(
            model_loader,
            backend=asr_backend,
            model=asr_model,
            onnx_provider=onnx_provider,
        )
        subtitle_options = SubtitleOptions(
            sentence_split=subtitle_sentence_split,
            max_line_count=subtitle_max_lines,
            max_line_width=subtitle_max_width,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    ns = None
    if num_speakers.strip():
        try:
            ns = int(num_speakers.strip())
            if ns <= 0:
                ns = None
        except ValueError:
            ns = None
    if enable_diarization and diarization_backend == "sortformer" and ns is not None:
        raise HTTPException(
            status_code=400,
            detail="NVIDIA Sortformer определяет число спикеров автоматически",
        )
    return TranscribeForm(
        output_formats=fmt_list,
        enable_diarization=enable_diarization,
        diarization_backend=diarization_backend,
        num_speakers=ns,
        asr_selection=asr_selection,
        subtitle_options=subtitle_options,
    )


@app.post("/api/upload")
async def upload_files(
    request: Request,
    files: list[UploadFile] = File(...),
    output_formats: str = Form("txt,txt_timecodes"),
    enable_diarization: bool = Form(False),
    diarization_backend: str = Form("pyannote"),
    num_speakers: str = Form(""),
    asr_backend: str = Form(""),
    asr_model: str = Form(""),
    onnx_provider: str = Form(""),
    subtitle_sentence_split: bool = Form(True),
    subtitle_max_lines: int = Form(2),
    subtitle_max_width: int = Form(64),
    user: str = Depends(require_auth),
):
    """Загрузка одного или нескольких файлов для транскрибации."""
    if len(files) > 20:
        raise HTTPException(status_code=400, detail="Максимум 20 файлов за раз")

    form = _parse_transcribe_form(
        output_formats=output_formats,
        enable_diarization=enable_diarization,
        diarization_backend=diarization_backend,
        num_speakers=num_speakers,
        asr_backend=asr_backend,
        asr_model=asr_model,
        onnx_provider=onnx_provider,
        subtitle_sentence_split=subtitle_sentence_split,
        subtitle_max_lines=subtitle_max_lines,
        subtitle_max_width=subtitle_max_width,
    )

    # Пакет принимается целиком или никак: сначала имена всех файлов, потом запись
    # всех на диск, и только затем задачи. Иначе на k-м файле клиент получал 400/413,
    # а файлы 1..k-1 уже лежали на диске и обрабатывались.
    unsupported = [file.filename or "" for file in files if not is_supported_format(file.filename or "")]
    if unsupported:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Неподдерживаемый формат: {', '.join(unsupported)}. Поддерживаемые: {', '.join(MEDIA_EXTENSIONS)}",
        )
    saved = []
    try:
        for file in files:
            saved.append(await _save_upload(file, request))
    except BaseException:
        for _task_id, file_path, _filename, _size in saved:
            file_path.unlink(missing_ok=True)
        raise

    uploaded = []
    for task_id, file_path, filename, file_size in saved:
        _register_task(task_id, filename, file_size, user, form.asr_selection)
        tasks_storage[task_id].update(form.task_fields())
        _persist_tasks_index()

        asyncio.create_task(
            process_transcription(
                task_id, file_path, filename, form.output_formats,
                form.enable_diarization, form.diarization_backend, form.num_speakers,
                form.asr_selection,
                form.subtitle_options,
            )
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
    diarization_backend: str = Form("pyannote"),
    num_speakers: str = Form(""),
    asr_backend: str = Form(""),
    asr_model: str = Form(""),
    onnx_provider: str = Form(""),
    subtitle_sentence_split: bool = Form(True),
    subtitle_max_lines: int = Form(2),
    subtitle_max_width: int = Form(64),
):
    """Загрузка медиа по URL через yt-dlp и постановка в очередь."""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL должен начинаться с http:// или https://")

    form = _parse_transcribe_form(
        output_formats=output_formats,
        enable_diarization=enable_diarization,
        diarization_backend=diarization_backend,
        num_speakers=num_speakers,
        asr_backend=asr_backend,
        asr_model=asr_model,
        onnx_provider=onnx_provider,
        subtitle_sentence_split=subtitle_sentence_split,
        subtitle_max_lines=subtitle_max_lines,
        subtitle_max_width=subtitle_max_width,
    )

    task_id = uuid.uuid4().hex
    _register_task(task_id, url.split("/")[-1][:80], 0, user, form.asr_selection)
    tasks_storage[task_id].update(form.task_fields())
    tasks_storage[task_id]['status'] = 'downloading'
    tasks_storage[task_id]['stage'] = 'Загрузка медиа...'
    tasks_storage[task_id]['message'] = 'Загрузка по URL'
    _persist_tasks_index()

    asyncio.create_task(
        _download_and_process(
            task_id, url, form.output_formats, form.enable_diarization,
            form.diarization_backend, form.num_speakers,
            form.asr_selection,
            form.subtitle_options,
        )
    )

    return {"task_id": task_id, "url": url}


async def _download_and_process(
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
    download_dir = UPLOAD_DIR / f"{task_id}_download"
    try:
        def on_percent(percent) -> None:  # MediaDownloader отдаёт 0..100; 95+ — уже обработка
            if task_id in tasks_storage and isinstance(percent, (int, float)):
                tasks_storage[task_id]['progress'] = max(0, min(95, int(percent)))

        downloader = media_downloader or MediaDownloader()
        loop = asyncio.get_running_loop()
        downloaded = await loop.run_in_executor(
            None,
            lambda: downloader.download(
                url, str(download_dir), progress_callback=on_percent, max_filesize=MAX_FILE_SIZE,
            ),
        )

        if task_id in deleted_task_ids or task_id not in tasks_storage:
            if task_id in deleted_task_ids:
                _finalize_deleted_task(task_id)
            else:
                _remove_task_files(task_id)
            return

        files = [Path(p) for p in (getattr(downloaded, "files", None) or []) if Path(p).is_file()]
        if not files:
            # yt-dlp молча пропускает файл больше max_filesize
            max_gb = MAX_FILE_SIZE / 1024 / 1024 / 1024
            raise RuntimeError(f"Ничего не скачано: медиа недоступно или больше лимита {max_gb:.1f} GB")
        if files[0].stat().st_size > MAX_FILE_SIZE:
            raise RuntimeError("Скачанный файл больше лимита MAX_FILE_SIZE")

        filename = safe_filename(files[0].name)
        file_path = UPLOAD_DIR / f"{task_id}_{filename}"
        files[0].replace(file_path)
        shutil.rmtree(download_dir, ignore_errors=True)
        file_size = file_path.stat().st_size

        tasks_storage[task_id].update({
            'filename': filename,
            'file_size': file_size,
            'status': 'pending',
            'stage': 'В очереди',
            'progress': 0,
            'message': 'Загрузка завершена, ожидание обработки',
        })
        _persist_tasks_index()
        _task_log(task_id, f"Загружено: {filename} ({file_size / 1024 / 1024:.1f} MB)")

        await process_transcription(
            task_id, file_path, filename, output_formats,
            enable_diarization, diarization_backend, num_speakers,
            asr_selection,
            subtitle_options,
        )

    except Exception as e:
        _task_log(task_id, f"Ошибка загрузки: {e}")
        if task_id in tasks_storage:
            tasks_storage[task_id].update({
                'status': 'failed',
                'stage': 'Ошибка загрузки',
                'message': str(e),
                'completed_at': datetime.now().isoformat(),
            })
            _persist_tasks_index()

    finally:
        shutil.rmtree(download_dir, ignore_errors=True)
        if task_id in deleted_task_ids and task_id not in tasks_storage:
            _finalize_deleted_task(task_id)


# ==================== ЭНДПОИНТЫ ЗАДАЧ ====================

@app.get("/api/tasks")
async def list_tasks(user: str = Depends(require_auth)):
    tasks = [_visible_task_copy(task) for task in tasks_storage.values() if task.get('user') == user]
    tasks.sort(key=lambda x: x.get('created_at') or '', reverse=True)
    return {"total": len(tasks), "tasks": tasks}


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str, user: str = Depends(require_auth)):
    # Без result_files: там абсолютные пути сервера; файлы отдают /result и /download
    return _visible_task_copy(_user_task_or_404(task_id, user))


@app.get("/api/tasks/{task_id}/logs")
async def get_task_logs(task_id: str, user: str = Depends(require_auth)):
    _user_task_or_404(task_id, user)
    return {"logs": log_queues.get(task_id, [])}


@app.get("/api/tasks/{task_id}/result")
async def get_task_result(task_id: str, user: str = Depends(require_auth)):
    task = _user_task_or_404(task_id, user)
    if task['status'] != 'completed':
        raise HTTPException(status_code=400, detail=f"Задача не завершена (статус: {task['status']})")

    result_dir = _task_result_dir(task_id)
    result_files = []
    if result_dir.exists():
        stem = Path(task['filename']).stem
        for fmt in task.get('output_formats', ['txt', 'txt_timecodes']):
            if fmt not in OUTPUT_FORMATS:
                continue  # задачи старых версий могли сохранить формат без проверки
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
    task = _user_task_or_404(task_id, user)
    if task['status'] != 'completed':
        raise HTTPException(status_code=400, detail="Задача не завершена")

    result_dir = _task_result_dir(task_id)
    if not result_dir.exists():
        raise HTTPException(status_code=404, detail="Результаты не найдены")

    stem = Path(task['filename']).stem
    found = find_result_file(result_dir, stem, format) if format in OUTPUT_FORMATS else None
    if not found:
        raise HTTPException(status_code=404, detail=f"Файл формата {format} не найден")

    return FileResponse(
        path=str(found),
        filename=found.name,
        media_type="application/octet-stream",
    )


@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str, user: str = Depends(require_auth)):
    task = _user_task_or_404(task_id, user)
    if task['status'] == 'processing':
        raise HTTPException(status_code=400, detail="Нельзя удалить задачу в процессе обработки")

    _delete_task_data(task_id, task)

    tasks_storage.pop(task_id, None)
    log_queues.pop(task_id, None)
    _persist_tasks_index()

    return {"ok": True, "message": "Задача удалена"}


@app.get("/api/llm/tools")
async def llm_tools(fresh: bool = False, user: str = Depends(require_auth)):
    """Реестр LLM-провайдеров и статусы CLI-инструментов на сервере (скан — в пуле потоков)."""
    statuses = await asyncio.to_thread(cli_tools.scan, None, fresh=fresh)
    return {
        "providers": cli_tools.canonical_provider_names(),
        "tools": [status.to_dict() for status in statuses],
        # false — пути/аргументы CLI и инструменты агента из формы сервер игнорирует
        "client_cli": WEB_ALLOW_CLIENT_LLM_CLI,
    }


@app.post("/api/llm/tools/check")
async def llm_tool_check(
    provider: str = Form(...),
    path: str = Form(""),
    user: str = Depends(require_auth),
):
    try:
        spec = cli_tools.provider_by_name(provider)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Неизвестный провайдер: {provider}") from exc

    def check():
        # Присланный путь запускается (`<path> --version`) только с разрешения оператора
        override = (path.strip() or None) if WEB_ALLOW_CLIENT_LLM_CLI else _server_tool_override(spec)
        return cli_tools.resolve_tool(spec, override)

    status = await asyncio.to_thread(check)
    return {"tool": status.to_dict()}


@app.post("/api/llm/process")
async def llm_process(
    request: Request,
    provider: str = Form("API"),
    api_url: str = Form(""),
    api_key: str = Form(""),
    model: str = Form(""),
    temperature: str = Form("0.2"),
    claude_path: str = Form("claude"),
    claude_args: str = Form(""),
    codex_path: str = Form("codex"),
    codex_args: str = Form(""),
    opencode_path: str = Form("opencode"),
    opencode_args: str = Form(""),
    pi_path: str = Form("pi"),
    pi_provider: str = Form(""),
    pi_args: str = Form(""),
    omp_path: str = Form("omp"),
    omp_provider: str = Form(""),
    omp_args: str = Form(""),
    other_path: str = Form(""),
    other_args: str = Form(""),
    llm_allow_tools: bool = Form(False),
    summary_enabled: bool = Form(False),
    tasks_enabled: bool = Form(False),
    custom_enabled: bool = Form(False),
    summary_prompt: str = Form(SUMMARY_PROMPT),
    tasks_prompt: str = Form(TASKS_PROMPT),
    custom_prompt: str = Form(""),
    manual_text: str = Form(""),
    export_formats: str = Form("txt"),
    transcript_files: list[UploadFile] = File(default=[]),
    user: str = Depends(require_auth),
):
    try:
        temperature_value = float((temperature or "0.2").strip())
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Temperature должно быть числом") from e

    client_settings = {
        "provider": provider,
        "api_url": api_url.strip(),
        "api_key": api_key.strip(),
        "model": model.strip(),
        "temperature": temperature_value,
        "claude_path": claude_path.strip() or "claude",
        "claude_args": claude_args.strip(),
        "codex_path": codex_path.strip() or "codex",
        "codex_args": codex_args.strip(),
        "opencode_path": opencode_path.strip() or "opencode",
        "opencode_args": opencode_args.strip(),
        "pi_path": pi_path.strip() or "pi",
        "pi_provider": pi_provider.strip(),
        "pi_args": pi_args.strip(),
        "omp_path": omp_path.strip() or "omp",
        "omp_provider": omp_provider.strip(),
        "omp_args": omp_args.strip(),
        "other_path": other_path.strip(),
        "other_args": other_args.strip(),
        "llm_allow_tools": bool(llm_allow_tools),
    }
    llm_settings = await asyncio.to_thread(_server_llm_settings, client_settings)

    items = []
    manual_text = (manual_text or "").strip()
    uploaded_names = []
    if manual_text:
        items.append({"name": "manual_transcript", "text": manual_text})
    for upload in transcript_files:
        text = (await upload.read()).decode("utf-8", errors="ignore").strip()
        if text:
            items.append({"name": Path(upload.filename or "transcript.txt").stem, "text": text})
            uploaded_names.append(upload.filename or "transcript.txt")
    if not items:
        raise HTTPException(status_code=400, detail="Выберите хотя бы один транскрипт или вставьте текст вручную")

    modes = []
    if summary_enabled:
        modes.append(("summary", "Выжимка", summary_prompt.strip() or SUMMARY_PROMPT))
    if tasks_enabled:
        modes.append(("tasks", "Задачи", tasks_prompt.strip() or TASKS_PROMPT))
    if custom_enabled:
        if not custom_prompt.strip():
            raise HTTPException(status_code=400, detail="Для режима «Свой промпт» укажите пользовательский промпт")
        modes.append(("custom", "Свой промпт", custom_prompt.strip()))
    if not modes:
        raise HTTPException(status_code=400, detail="Выберите хотя бы один режим LLM-обработки")

    formats = [fmt.strip() for fmt in export_formats.split(",") if fmt.strip()]
    if not formats:
        raise HTTPException(status_code=400, detail="Выберите хотя бы один формат вывода")

    job_id = uuid.uuid4().hex
    job_dir = LLM_RESULTS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    results = []
    saved_files = []
    for item in items:
        blocks = []
        for mode_suffix, mode_label, prompt in modes:
            answer = await asyncio.get_running_loop().run_in_executor(None, lambda s=llm_settings, t=item["text"], p=prompt: _run_llm_provider(s, t, p))
            blocks.append(f"=== {item['name']} / {mode_label} ===\n{answer}")
            for fmt in formats:
                save_path = job_dir / f"{item['name']}_llm_{mode_suffix}.{fmt}"
                if fmt in ("txt", "md"):
                    save_path.write_text(answer, encoding="utf-8")
                elif fmt == "docx":
                    from docx import Document
                    doc = Document()
                    for part in answer.split("\n\n"):
                        doc.add_paragraph(part)
                    doc.save(save_path)
                saved_files.append({"name": save_path.name, "path": str(save_path), "format": fmt})
        results.append("\n\n".join(blocks))

    result_text = "\n\n".join(results)
    meta = {"job_id": job_id, "provider": provider, "created_at": datetime.now().isoformat(), "user": user, "files": uploaded_names}
    save_json_atomic(str(job_dir / "meta.json"), meta)
    return {"job_id": job_id, "provider": provider, "result_text": result_text, "saved_files": saved_files}

@app.get("/api/llm/download/{job_id}/{filename}")
async def llm_download(job_id: str, filename: str, user: str = Depends(require_auth)):
    job_dir = LLM_RESULTS_DIR / job_id
    meta = load_json(str(job_dir / "meta.json"), {})
    if not job_dir.exists() or not isinstance(meta, dict) or meta.get("user") != user:
        raise HTTPException(status_code=404, detail="LLM-результат не найден")
    path = job_dir / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Файл не найден")
    return FileResponse(path=str(path), filename=path.name, media_type="application/octet-stream")

@app.delete("/api/tasks")
async def delete_all_tasks(
    status_filter: str = "completed,failed",
    user: str = Depends(require_auth),
):
    statuses = {status.strip() for status in status_filter.split(",") if status.strip()}
    if "all" in statuses:
        statuses = set(ALL_TASK_STATUSES)

    removed = 0
    for tid in list(tasks_storage.keys()):
        task = tasks_storage[tid]
        if task.get('user') != user:
            continue
        if task['status'] in statuses:
            _delete_task_data(tid, task)
            tasks_storage.pop(tid, None)
            log_queues.pop(tid, None)
            removed += 1

    _persist_tasks_index()

    return {"ok": True, "removed": removed}


# ==================== SSE ПРОГРЕСС ====================

class ProgressFeed:
    """Сообщения SSE одного подключения: снимок, затем только изменения.

    Первое сообщение — `{"snapshot": true, "tasks": все задачи пользователя,
    "logs": {}}`, даже если задач нет. Раньше каждое подключение начиналось с
    пустой памяти и первым сообщением отдавало ВСЕ задачи и ВЕСЬ журнал как
    новые события: клиент на каждое (пере)подключение заново писал историю в
    журнал, исторические ошибки и по запросу /api/tasks на каждую завершённую
    задачу. Журнал в снимок не входит — история не повторяется; строки,
    появившиеся после снимка, приходят как обычно.
    """

    def __init__(self, user: str):
        self.user = user
        self.started = False
        self.last: dict[str, dict] = {}
        self.log_lengths: dict[str, int] = {}

    def _current(self) -> dict[str, dict]:
        current = {}
        for tid, task in tasks_storage.items():
            if task.get('user') != self.user:
                continue
            current[tid] = {
                'status': task['status'],
                'progress': task['progress'],
                'stage_progress': task.get('stage_progress'),
                'processed_seconds': task.get('processed_seconds'),
                'total_seconds': task.get('total_seconds'),
                'progress_indeterminate': task.get('progress_indeterminate', False),
                'file_progress': int(task['progress']),
                'stage': task.get('stage', ''),
                'message': task.get('message', ''),
                'filename': task['filename'],
            }
        return current

    def next_payload(self) -> dict | None:
        current = self._current()
        if not self.started:
            self.started = True
            self.last = dict(current)
            self.log_lengths = {tid: len(log_queues.get(tid, [])) for tid in current}
            return {'snapshot': True, 'tasks': current, 'logs': {}}

        changed = {tid: data for tid, data in current.items() if self.last.get(tid) != data}
        new_logs = {}
        for tid, logs in list(log_queues.items()):
            task = tasks_storage.get(tid)
            if task is None or task.get('user') != self.user:
                continue
            seen = self.log_lengths.get(tid, 0)
            if len(logs) > seen:
                new_logs[tid] = logs[seen:]
                self.log_lengths[tid] = len(logs)
        self.last.update(current)
        if changed or new_logs:
            return {'tasks': changed, 'logs': new_logs}
        return None


@app.get("/api/progress")
async def progress_stream(request: Request, user: str = Depends(require_auth)):
    """SSE-стрим прогресса всех задач в реальном времени."""
    import json as json_mod

    from starlette.responses import StreamingResponse

    async def event_generator():
        feed = ProgressFeed(user)
        while True:
            if await request.is_disconnected():
                break
            payload = feed.next_payload()
            if payload is not None:
                yield f"data: {json_mod.dumps(payload)}\n\n"
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
        "runtime": _runtime_info(),
        "asr": _asr_health(),
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
