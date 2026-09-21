#!/usr/bin/env python3
"""GigaAM v3 Transcriber — REST API, совместимый с OpenAI Audio API.

POST /v1/audio/transcriptions, GET /v1/models, GET /health. Клиенты OpenAI SDK
работают, поменяв base_url и ключ. Формат ошибок — конверт OpenAI.
"""

import asyncio
import hashlib
import hmac
import json
import os
import re
import shutil
import tempfile
import uuid

# Подавляем предупреждения
import warnings
from contextlib import asynccontextmanager
from pathlib import Path
from platform import machine
from platform import platform as runtime_platform
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Header, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.exceptions import HTTPException as StarletteHTTPException

warnings.filterwarnings("ignore", category=UserWarning)

# Импорты проекта
from src import __version__
from src.config import AUDIO_PREPROCESSING_MODE, HF_TOKEN, SUPPORTED_FORMATS
from src.core.asr.models import ASR_MODELS
from src.core.model_loader import ModelLoader
from src.services import file_policy, transcript_formats, transcription_service
from src.services import health as health_service
from src.utils.audio_converter import ffmpeg_available
from src.utils.diarization import normalize_diarization_backend
from src.utils.logger import setup_logger
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

# Разрешённые CORS-origins (через запятую в .env); по умолчанию пусто = кросс-доменные запросы запрещены
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]

# Показывать ли клиенту полный traceback (только для отладки!)
API_DEBUG = os.getenv("API_DEBUG", "false").lower() in ("1", "true", "yes")

# Директории
UPLOAD_DIR = Path(__file__).parent / os.getenv("UPLOAD_DIR", "uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

# Ограничения (из .env или значения по умолчанию)
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", str(2 * 1024 * 1024 * 1024)))  # 2GB
MAX_CONCURRENT_TASKS = int(os.getenv("MAX_CONCURRENT_TASKS", "3"))

# API настройки
API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("API_PORT", "8000"))
API_WORKERS = int(os.getenv("API_WORKERS", "2"))

# Лимит запросов на транскрибацию с одного IP (формат slowapi: "10/minute", "100/hour")
RATE_LIMIT_UPLOAD = os.getenv("RATE_LIMIT_UPLOAD", "10/minute").strip() or "10/minute"

# Запас над MAX_FILE_SIZE для multipart-обвязки при проверке Content-Length до чтения тела
_CONTENT_LENGTH_SLACK = 1024 * 1024

# Глобальные переменные
model_loader = None
stats_manager = None
logger = None

# Семафор для ограничения одновременных запросов на транскрибацию
processing_semaphore = None


# ==================== OpenAI error envelope ====================


class OpenAIError(Exception):
    def __init__(self, status_code: int, message: str, *, type_: str = "invalid_request_error",
                 param: str | None = None, code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.type = type_
        self.param = param
        self.code = code

    def payload(self) -> dict[str, Any]:
        return {"error": {"message": self.message, "type": self.type, "param": self.param, "code": self.code}}


def openai_error(status: int, message: str, *, type_: str = "invalid_request_error",
                 param: str | None = None, code: str | None = None) -> OpenAIError:
    return OpenAIError(status, message, type_=type_, param=param, code=code)


_STATUS_TYPES = {401: "authentication_error", 429: "rate_limit_error"}


def _type_for_status(status: int) -> str:
    if status in _STATUS_TYPES:
        return _STATUS_TYPES[status]
    return "server_error" if status >= 500 else "invalid_request_error"


# ==================== КЛЮЧИ ====================

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def _hash_key(key: str) -> str:
    """SHA-256 хэш ключа (в файле и памяти хранятся только хэши)"""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _asr_health() -> dict[str, object]:
    return health_service.asr_health(model_loader)


def _runtime_info() -> dict[str, object]:
    return health_service.runtime_info(runtime_platform, machine)


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


def verify_api_key(
    authorization: str | None = Header(None),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
) -> str:
    """Bearer (как у OpenAI SDK) или X-API-Key; сравнение хэшей constant-time."""
    # При прямом вызове (тесты) незаполненный аргумент — это объект Header, а не строка
    key = None
    if isinstance(authorization, str) and authorization.lower().startswith("bearer "):
        key = authorization[7:].strip()
    elif isinstance(x_api_key, str):
        key = x_api_key.strip()
    if not key:
        raise openai_error(401, "Missing API key. Send 'Authorization: Bearer <key>'.",
                           type_="authentication_error", code="invalid_api_key")
    candidate = _hash_key(key)
    if not any(hmac.compare_digest(candidate, valid) for valid in VALID_API_KEY_HASHES):
        raise openai_error(401, "Incorrect API key provided.", type_="authentication_error", code="invalid_api_key")
    return key


def is_supported_format(filename: str) -> bool:
    """Проверяет поддерживаемый формат файла"""
    return file_policy.is_supported_by_glob(filename, SUPPORTED_FORMATS[1])


def safe_filename(filename: str | None) -> str:
    """Защита от path traversal: оставляет только базовое имя без разделителей путей."""
    return file_policy.safe_filename(filename)


# ==================== МОДЕЛИ ====================

DEFAULT_MODEL = "v3_e2e_rnnt"
MODEL_ALIASES = {
    "whisper-1": DEFAULT_MODEL,
    "gpt-4o-transcribe": DEFAULT_MODEL,
    "gpt-4o-mini-transcribe": DEFAULT_MODEL,
    "gigaam": DEFAULT_MODEL,
}


def resolve_model(model: str | None) -> str:
    name = (model or DEFAULT_MODEL).strip()
    name = MODEL_ALIASES.get(name, name)
    if name not in ASR_MODELS:
        raise openai_error(404, f"The model '{model}' does not exist.", param="model", code="model_not_found")
    return name


def _model_object(model_id: str) -> dict[str, Any]:
    aliases = sorted(alias for alias, target in MODEL_ALIASES.items() if target == model_id)
    return {
        "id": model_id,
        "object": "model",
        "created": 0,
        "owned_by": "gigaam",
        "description": ASR_MODELS[model_id],
        "default": model_id == DEFAULT_MODEL,
        "aliases": aliases,
    }


def models_payload() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [_model_object(m) for m in ASR_MODELS],
        "gigaam": {
            "backends": transcription_service.available_asr_backends(),
            "onnx_providers": list(transcription_service.ONNX_PROVIDERS),
            "active": model_loader.diagnostics() if model_loader is not None else {},
        },
    }


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

    # Семафор для ограничения одновременных запросов
    processing_semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

    logger.info(f"API готов к работе (макс. {MAX_CONCURRENT_TASKS} запросов одновременно)")
    print("✅ API сервер успешно запущен!")
    print("="*60)

    yield

    # Очистка
    logger.info("Остановка API сервера...")
    print("\n👋 API сервер остановлен")


# ==================== ПРИЛОЖЕНИЕ ====================

# Rate limiter; headers_enabled — Retry-After / X-RateLimit-* для backoff в OpenAI SDK
limiter = Limiter(key_func=get_remote_address, headers_enabled=True)

app = FastAPI(
    title="GigaAM v3 Transcriber API",
    description="OpenAI-compatible speech-to-text API (POST /v1/audio/transcriptions).",
    version=__version__,
    lifespan=lifespan,
)
app.state.limiter = limiter


class _UploadGuard:
    """Отсекает загрузки по заголовкам ДО чтения тела (чистый ASGI, без BaseHTTPMiddleware —
    тот ломает стриминг и обрыв соединения).

    FastAPI разбирает multipart (и спулит файл во временную директорию) раньше,
    чем выполняются Depends(verify_api_key) и лимитер, поэтому без этой проверки
    клиент без ключа мог бы залить сколько угодно байт, а 401 не считались бы
    лимитером. Здесь только наличие ключа и Content-Length; сам ключ проверяет
    verify_api_key, точный размер — _save_upload по мере записи.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("method") == "POST" and scope.get("path", "").startswith("/v1/audio/"):
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
            error = _upload_guard_error(headers)
            if error is not None:
                await JSONResponse(error.payload(), status_code=error.status_code)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def _upload_guard_error(headers: dict[str, str]) -> OpenAIError | None:
    auth = headers.get("authorization", "")
    if not (auth.lower().startswith("bearer ") or headers.get("x-api-key")):
        return openai_error(401, "Missing API key. Send 'Authorization: Bearer <key>'.",
                            type_="authentication_error", code="invalid_api_key")
    content_length = headers.get("content-length", "")
    if content_length.isdigit() and int(content_length) > MAX_FILE_SIZE + _CONTENT_LENGTH_SLACK:
        return openai_error(413, f"File exceeds the maximum size of {MAX_FILE_SIZE} bytes.",
                            param="file", code="file_too_large")
    return None


app.add_middleware(_UploadGuard)

# CORS — список доменов задаётся через CORS_ORIGINS в .env (по умолчанию кросс-домен запрещён).
# Аутентификация по заголовку (Authorization: Bearer <key> или X-API-Key), поэтому credentials не нужны.
# Добавляется после _UploadGuard, т.е. снаружи него: ответы 401/413 из гарда тоже получают CORS-заголовки.
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_credentials=False,
                   allow_methods=["GET", "POST"], allow_headers=["Authorization", "X-API-Key", "Content-Type"],
                   expose_headers=["Retry-After", "X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"])


# ==================== ОБРАБОТЧИКИ ОШИБОК ====================
# Все ошибки — в конверте OpenAI: {"error": {"message", "type", "param", "code"}}.


@app.exception_handler(OpenAIError)
async def _openai_error_handler(_: Request, exc: OpenAIError):
    return JSONResponse(exc.payload(), status_code=exc.status_code)


@app.exception_handler(StarletteHTTPException)
async def _http_error_handler(_: Request, exc: StarletteHTTPException):
    err = openai_error(exc.status_code, str(exc.detail), type_=_type_for_status(exc.status_code))
    return JSONResponse(err.payload(), status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def _validation_handler(_: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    loc = [str(p) for p in first.get("loc", []) if p not in ("body", "query")]
    err = openai_error(422, first.get("msg", "Invalid request"), param=".".join(loc) or None)
    return JSONResponse(err.payload(), status_code=422)


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    err = openai_error(429, f"Rate limit exceeded: {exc.detail}", type_="rate_limit_error", code="rate_limit_exceeded")
    response = JSONResponse(err.payload(), status_code=429)
    # Retry-After / X-RateLimit-* — по ним OpenAI SDK делает backoff
    view_rate_limit = getattr(request.state, "view_rate_limit", None)
    if view_rate_limit is not None:
        response = request.app.state.limiter._inject_headers(response, view_rate_limit)
    return response


@app.exception_handler(Exception)
async def _unhandled_error_handler(_: Request, exc: Exception):
    if logger:
        logger.error(f"[api] unhandled error: {exc}", exc_info=True)
    message = "Internal server error."
    if API_DEBUG:
        message = f"{message} {exc}"
    err = openai_error(500, message, type_="server_error", code="internal_error")
    return JSONResponse(err.payload(), status_code=500)


# ==================== ЭНДПОИНТЫ ====================


@app.get("/")
async def root():
    return {
        "service": "GigaAM v3 Transcriber API",
        "version": __version__,
        "docs": "/docs",
        "openai_compatible": True,
        "endpoints": ["POST /v1/audio/transcriptions", "GET /v1/models", "GET /v1/models/{id}", "GET /health"],
    }


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "version": __version__,
        "model_loaded": model_loader is not None and model_loader.is_loaded(),
        "runtime": _runtime_info(),
        "asr": _asr_health(),
    }


@app.get("/v1/models", dependencies=[Depends(verify_api_key)])
async def list_models():
    return models_payload()


@app.get("/v1/models/{model_id}", dependencies=[Depends(verify_api_key)])
async def get_model(model_id: str):
    return _model_object(resolve_model(model_id))


@app.post("/v1/audio/translations", dependencies=[Depends(verify_api_key)])
async def create_translation():
    raise openai_error(400, "GigaAM transcribes speech but does not translate it; use /v1/audio/transcriptions.",
                       code="translation_not_supported")


_GRANULARITIES = {"segment", "word"}
_STREAM_FORMATS = {"json", "verbose_json"}


def _save_upload(file: UploadFile) -> tuple[Path, Path]:
    """Кладёт загрузку в свою временную директорию под UPLOAD_DIR; лимит размера — по мере записи."""
    filename = safe_filename(file.filename)
    if not is_supported_format(filename):
        raise openai_error(400, f"Unsupported file type: '{filename}'. Supported: {', '.join(SUPPORTED_FORMATS[1])}",
                           param="file", code="unsupported_file")
    work_dir = Path(tempfile.mkdtemp(prefix="req_", dir=UPLOAD_DIR))
    target = work_dir / filename
    written = 0
    too_large = False
    with open(target, "wb") as out:
        while chunk := file.file.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_FILE_SIZE:
                too_large = True
                break
            out.write(chunk)
    if too_large:
        # rmtree только после закрытия файла: на Windows открытый файл не даст удалить директорию
        shutil.rmtree(work_dir, ignore_errors=True)
        raise openai_error(413, f"File exceeds the maximum size of {MAX_FILE_SIZE} bytes.",
                           param="file", code="file_too_large")
    return work_dir, target


def _cleanup(work_dir: Path) -> None:
    shutil.rmtree(work_dir, ignore_errors=True)


def _parse_bool(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _run_processor(request_loader, file_path: Path, work_dir: Path, *, enable_diarization: bool,
                   diarization_backend: str, num_speakers: int | None, audio_preprocessing: str,
                   progress_callback) -> dict[str, Any]:
    """Синхронно: грузит модель под запрос (если нужна другая) и запускает процессор."""
    processor = transcription_service.build_processor(
        request_loader, stats_manager,
        logger=lambda msg: logger.debug(f"[api] {msg}") if logger else None,
        progress_callback=progress_callback,
    )
    result = processor.process_file(
        str(file_path), str(work_dir), 0, 1, file_path.name,
        enable_diarization=enable_diarization, num_speakers=num_speakers, output_formats=[],
        diarization_backend=diarization_backend, audio_preprocessing_mode=audio_preprocessing,
    )
    if not result.get("success"):
        raise RuntimeError("processing failed")
    return result


def _queue_progress(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue, event_or_stage, progress=None) -> None:
    """SSE-комментарий о прогрессе. Процессор шлёт ProgressEvent одним аргументом
    (или (stage, value) — legacy) из executor-потока — переключаемся в loop."""
    stage = getattr(event_or_stage, "stage", None) or (event_or_stage if isinstance(event_or_stage, str) else "processing")
    value = getattr(event_or_stage, "file_progress", None)
    if value is None:
        value = progress
    pct = f"{int(float(value) * 100)}%" if isinstance(value, (int, float)) else "…"
    try:
        loop.call_soon_threadsafe(queue.put_nowait, f": progress {stage} {pct}\n\n")
    except RuntimeError:
        pass  # loop закрыт (сервер останавливается) — прогресс уже некому отдавать


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.post("/v1/audio/transcriptions", dependencies=[Depends(verify_api_key)],
          summary="Transcribe audio (OpenAI-compatible)")
@limiter.limit(RATE_LIMIT_UPLOAD)
async def create_transcription(
    request: Request,
    file: UploadFile = File(..., description="Audio or video file"),
    model: str = Form(..., description="GigaAM model id or an OpenAI alias (whisper-1, gpt-4o-transcribe)"),
    language: str | None = Form(None),
    prompt: str | None = Form(None, description="Accepted and ignored"),
    response_format: str = Form("json"),
    temperature: float | None = Form(None, description="Accepted and ignored"),
    stream: str | None = Form(None),
    timestamp_granularities: list[str] | None = Form(None, alias="timestamp_granularities[]"),
    include: list[str] | None = Form(None, alias="include[]", description="Accepted and ignored"),
    chunking_strategy: str | None = Form(None, description="Accepted and ignored (VAD chunking is always on)"),
    known_speaker_names: list[str] | None = Form(None, alias="known_speaker_names[]"),
    known_speaker_references: list[str] | None = Form(None, alias="known_speaker_references[]"),
    # --- GigaAM extensions ---
    diarize: str | None = Form(None, description="GigaAM extension: speaker diarization"),
    diarization_backend: str = Form("pyannote", description="GigaAM extension: pyannote | sortformer"),
    num_speakers: int | None = Form(None, ge=1, description="GigaAM extension"),
    asr_backend: str | None = Form(None, description="GigaAM extension: auto | pytorch | onnx | mlx"),
    onnx_provider: str | None = Form(None, description="GigaAM extension"),
    audio_preprocessing: str | None = Form(None, description="GigaAM extension: off | auto | deepfilter"),
):
    if response_format not in transcript_formats.FORMATS:
        raise openai_error(400, f"Unsupported response_format '{response_format}'. Use one of: {', '.join(transcript_formats.FORMATS)}.",
                           param="response_format", code="unsupported_response_format")
    streaming = _parse_bool(stream)
    if streaming and response_format not in _STREAM_FORMATS:
        raise openai_error(400, "stream=true is only supported with response_format=json or verbose_json.",
                           param="stream", code="stream_not_supported")
    if known_speaker_names or known_speaker_references:
        raise openai_error(400, "known_speaker_names/known_speaker_references are not supported by GigaAM.",
                           param="known_speaker_names", code="unsupported_parameter")
    granularities = set(timestamp_granularities or ["segment"])
    bad = granularities - _GRANULARITIES
    if bad:
        raise openai_error(400, f"Unknown timestamp granularity: {', '.join(sorted(bad))}.",
                           param="timestamp_granularities", code="unsupported_parameter")
    model_id = resolve_model(model)
    enable_diarization = _parse_bool(diarize) or response_format == "diarized_json"
    try:
        diarization_backend = normalize_diarization_backend(diarization_backend)
    except ValueError as exc:
        raise openai_error(400, f"Unknown diarization_backend '{diarization_backend}'. Use pyannote or sortformer.",
                           param="diarization_backend", code="unsupported_parameter") from exc
    if enable_diarization and diarization_backend == "sortformer" and num_speakers is not None:
        raise openai_error(400, "num_speakers cannot be combined with diarization_backend=sortformer.",
                           param="num_speakers", code="unsupported_parameter")
    if enable_diarization and not (HF_TOKEN and HF_TOKEN.startswith("hf_")) and diarization_backend == "pyannote":
        raise openai_error(503, "Diarization is unavailable: HF_TOKEN is not configured on the server.",
                           type_="server_error", code="diarization_unavailable")
    if model_loader is None:
        raise openai_error(503, "ASR model is not loaded.", type_="server_error", code="model_not_loaded")
    try:
        asr_selection = transcription_service.normalize_asr_selection(
            model_loader, backend=asr_backend, model=model_id, onnx_provider=onnx_provider)
    except ValueError as exc:
        raise openai_error(400, str(exc), param="asr_backend", code="unsupported_parameter") from exc
    preprocessing = (audio_preprocessing or AUDIO_PREPROCESSING_MODE)

    loop = asyncio.get_running_loop()
    # Запись на диск — в executor, чтобы гигабайтная загрузка не блокировала loop
    work_dir, file_path = await loop.run_in_executor(None, _save_upload, file)
    progress_queue: asyncio.Queue[str] = asyncio.Queue()

    def progress_callback(event_or_stage, progress=None, **_):
        _queue_progress(loop, progress_queue, event_or_stage, progress)

    def blocking() -> dict[str, Any]:
        request_loader, owns = model_loader, False
        if asr_selection is not None:
            request_loader, owns = transcription_service.acquire_request_model_loader(
                model_loader, asr_selection, loader_factory=ModelLoader)
        try:
            if owns and not request_loader.load_model(logger=(logger.info if logger else None)):
                raise RuntimeError("could not load the requested ASR backend")
            return _run_processor(
                request_loader, file_path, work_dir, enable_diarization=enable_diarization,
                diarization_backend=diarization_backend, num_speakers=num_speakers,
                audio_preprocessing=preprocessing, progress_callback=progress_callback)
        finally:
            if owns:  # иначе модель под нестандартный backend/model живёт до остановки процесса
                request_loader.unload()

    async def run() -> dict[str, Any]:
        async with processing_semaphore:
            return await loop.run_in_executor(None, blocking)

    def render(result):
        return transcript_formats.render(
            response_format, result.get("utterances") or [], result.get("media_duration") or 0.0,
            language=language, granularities=granularities,
            diarized=bool(result.get("diarization", {}).get("applied")) or enable_diarization,
            subtitle_options=None)

    if not streaming:
        try:
            result = await run()
        except OpenAIError:
            _cleanup(work_dir)
            raise
        except Exception as exc:
            _cleanup(work_dir)
            if logger:
                logger.error(f"[api] transcription failed: {exc}", exc_info=True)
            raise openai_error(500, "Transcription failed on the server. See the server log.",
                               type_="server_error", code="processing_failed") from exc
        _cleanup(work_dir)
        body, media_type = render(result)
        if isinstance(body, dict):
            return JSONResponse(body)
        return Response(content=body, media_type=media_type)

    def finished(task: asyncio.Future) -> None:
        # Уборка привязана к завершению работы в executor, а не к закрытию генератора:
        # при обрыве соединения поток ещё пишет в work_dir. Результат забираем всегда,
        # иначе asyncio ругается «Task exception was never retrieved».
        exc = None if task.cancelled() else task.exception()
        if exc is not None and logger:
            logger.error(f"[api] streamed transcription failed: {exc}", exc_info=exc)
        _cleanup(work_dir)

    async def events():
        task = asyncio.ensure_future(run())
        task.add_done_callback(finished)
        getter = asyncio.ensure_future(progress_queue.get())
        try:
            while not task.done():
                done, _ = await asyncio.wait({task, getter}, timeout=5.0, return_when=asyncio.FIRST_COMPLETED)
                if getter in done:
                    yield getter.result()
                    getter = asyncio.ensure_future(progress_queue.get())
                elif not done:
                    yield ": keepalive\n\n"
            # Свежий getter мог забрать комментарий, пришедший вместе с завершением
            if getter.done() and not getter.cancelled():
                yield getter.result()
            while not progress_queue.empty():
                yield progress_queue.get_nowait()
            result = task.result()
            utts = result.get("utterances") or []
            # Все дельты, кроме последней, с пробелом на конце — конкатенация равна full_text
            parts = [t for t in (u.get("transcription", "").strip() for u in utts) if t]
            for index, text in enumerate(parts):
                delta = text if index == len(parts) - 1 else text + " "
                yield _sse({"type": "transcript.text.delta", "delta": delta})
            done_event = {"type": "transcript.text.done", "text": transcript_formats.full_text(utts),
                          "usage": transcript_formats.usage(result.get("media_duration") or 0.0)}
            if response_format == "verbose_json":
                done_event.update({k: v for k, v in render(result)[0].items() if k not in done_event})
            yield _sse(done_event)
        except Exception as exc:
            # Ошибку самой задачи уже залогировал finished; остальное (render) — здесь
            from_task = task.done() and not task.cancelled() and exc is task.exception()
            if logger and not from_task:
                logger.error(f"[api] streamed transcription failed: {exc}", exc_info=True)
            # Клиенту — SSE-событие без внутренностей
            err = openai_error(500, "Transcription failed on the server. See the server log.",
                               type_="server_error", code="processing_failed")
            yield _sse({"type": "error", "error": err.payload()["error"]})
        finally:
            getter.cancel()  # и при обрыве соединения клиентом (GeneratorExit)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


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
