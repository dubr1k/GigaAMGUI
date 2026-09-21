#!/usr/bin/env python3
"""GigaAM v3 Transcriber — REST API, совместимый с OpenAI Audio API.

POST /v1/audio/transcriptions, GET /v1/models, GET /health. Клиенты OpenAI SDK
работают, поменяв base_url и ключ. Формат ошибок — конверт OpenAI.
"""

import asyncio
import hashlib
import hmac
import os
import re
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
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.exceptions import HTTPException as StarletteHTTPException

warnings.filterwarnings("ignore", category=UserWarning)

# Импорты проекта
from src import __version__
from src.config import HF_TOKEN, SUPPORTED_FORMATS
from src.core.asr.models import ASR_MODELS
from src.core.model_loader import ModelLoader
from src.services import file_policy, transcription_service
from src.services import health as health_service
from src.utils.audio_converter import ffmpeg_available
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

# Rate limiter
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="GigaAM v3 Transcriber API",
    description="OpenAI-compatible speech-to-text API (POST /v1/audio/transcriptions).",
    version=__version__,
    lifespan=lifespan,
)
app.state.limiter = limiter

# CORS — список доменов задаётся через CORS_ORIGINS в .env (по умолчанию кросс-домен запрещён).
# Аутентификация по заголовку (Authorization: Bearer / X-API-Key), поэтому credentials не нужны.
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_credentials=False,
                   allow_methods=["GET", "POST"], allow_headers=["Authorization", "X-API-Key", "Content-Type"])


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
async def _rate_limit_handler(_: Request, exc: RateLimitExceeded):
    err = openai_error(429, f"Rate limit exceeded: {exc.detail}", type_="rate_limit_error", code="rate_limit_exceeded")
    return JSONResponse(err.payload(), status_code=429)


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


@app.post("/v1/audio/transcriptions", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
async def create_transcription(request: Request, file: UploadFile = File(...), model: str = Form(...)):
    raise openai_error(501, "not implemented yet", type_="server_error")  # Task 4


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
