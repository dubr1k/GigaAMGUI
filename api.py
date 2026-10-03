#!/usr/bin/env python3
"""GigaAM v3 Transcriber — REST API, совместимый с OpenAI Audio API.

POST /v1/audio/transcriptions, GET /v1/models, GET /health. Клиенты OpenAI SDK
работают, поменяв base_url и ключ. Формат ошибок — конверт OpenAI.
"""

import asyncio
import hmac
import os
import shutil
import tempfile

# Подавляем предупреждения
import warnings
from contextlib import asynccontextmanager
from pathlib import Path
from platform import machine
from platform import platform as runtime_platform
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Header, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from limits import parse_many
from slowapi import Limiter
from slowapi.util import get_remote_address

warnings.filterwarnings("ignore", category=UserWarning)

# Импорты проекта
from src import __version__
from src.config import HF_TOKEN, SUPPORTED_FORMATS
from src.core.model_loader import ModelLoader
from src.services import (  # noqa: I001
    file_policy,
    openai_errors,
    openai_stream,
    transcript_formats,
    transcription_api,
    transcription_service,  # noqa: F401  (тесты подменяют api.transcription_service.build_processor)
)
from src.services import health as health_service
from src.services.api_keys import KeyStore, hash_key, key_from_headers
from src.services.mcp_backend import LocalBackend
from src.services.mcp_http import backend_options_from_env, mount_mcp
from src.services.openai_errors import OpenAIError, openai_error
from src.services.transcription_api import BackendError
from src.utils.audio_converter import ffmpeg_available
from src.utils.logger import setup_logger
from src.utils.media_downloader import MediaDownloader
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



def _validated_rate_limit(value: str | None) -> str:
    """Проверяет строку лимита при импорте: slowapi при неразборной строке молча
    отключает лимит (ловит ValueError и пишет пустой список), а нам нужен громкий отказ."""
    limit = (value or "").strip() or "10/minute"
    try:
        parse_many(limit)
    except ValueError as exc:
        raise ValueError(f"RATE_LIMIT_UPLOAD={limit!r} is not a valid rate limit (e.g. '10/minute').") from exc
    return limit


# Лимит запросов на транскрибацию с одного IP (формат slowapi: "10/minute", "100/hour")
RATE_LIMIT_UPLOAD = _validated_rate_limit(os.getenv("RATE_LIMIT_UPLOAD"))

# Запас над MAX_FILE_SIZE для multipart-обвязки при проверке Content-Length до чтения тела
_CONTENT_LENGTH_SLACK = 1024 * 1024

# Глобальные переменные
model_loader = None
stats_manager = None
logger = None

# Семафор для ограничения одновременных запросов на транскрибацию
processing_semaphore = None


# ==================== КЛЮЧИ ====================
# Само хранилище — src/services/api_keys.KeyStore (общее с веб-панелью и MCP).
# Модульные VALID_API_KEY_HASHES / API_KEYS_FILE / load_api_keys / save_api_keys
# остаются как тонкие обёртки: их подменяют тесты и внешний код.

_hash_key = hash_key


def _asr_health() -> dict[str, object]:
    # Публичный /health: без путей сервера (cache_root, repo)
    return health_service.public_asr_health(model_loader)


def _runtime_info() -> dict[str, object]:
    return health_service.runtime_info(runtime_platform, machine)


def load_api_keys():
    """Загружает хэши API-ключей из файла (с миграцией старых plaintext-ключей, первый ключ — при отсутствии файла)"""
    # KeyStore создаётся здесь, а не на импорте: API_KEYS_FILE подменяют тесты
    store = KeyStore(API_KEYS_FILE).load()
    VALID_API_KEY_HASHES.clear()
    VALID_API_KEY_HASHES.update(store.hashes)


def save_api_keys():
    """Сохраняет хэши API-ключей в файл"""
    store = KeyStore(API_KEYS_FILE)
    store.hashes = set(VALID_API_KEY_HASHES)
    store.save()


def verify_api_key(
    authorization: str | None = Header(None),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
) -> str:
    """Bearer (как у OpenAI SDK) или X-API-Key; сравнение хэшей constant-time."""
    # При прямом вызове (тесты) незаполненный аргумент — это объект Header, а не строка
    headers = {name: value for name, value in (("authorization", authorization), ("x-api-key", x_api_key))
               if isinstance(value, str)}
    key = key_from_headers(headers)
    if not key:
        raise openai_error(401, "Missing API key. Send 'Authorization: Bearer <key>'.",
                           type_="authentication_error", code="invalid_api_key")
    candidate = hash_key(key)
    if not any(hmac.compare_digest(candidate, valid) for valid in VALID_API_KEY_HASHES):
        raise openai_error(401, "Incorrect API key provided.", type_="authentication_error", code="invalid_api_key")
    return key


def is_supported_format(filename: str) -> bool:
    """Проверяет поддерживаемый формат файла"""
    return file_policy.is_supported_media(filename)


def safe_filename(filename: str | None) -> str:
    """Защита от path traversal: оставляет только базовое имя без разделителей путей."""
    return file_policy.safe_filename(filename)


# ==================== МОДЕЛИ ====================

# Реестр моделей и алиасов живёт в transcription_api (общий с MCP); здесь — реэкспорт.
DEFAULT_MODEL = transcription_api.DEFAULT_MODEL
MODEL_ALIASES = transcription_api.MODEL_ALIASES
resolve_model = transcription_api.resolve_model
_model_object = transcription_api.model_object


def models_payload() -> dict[str, Any]:
    return transcription_api.models_payload(model_loader)


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
    if key_from_headers(headers) is None:
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
# Обработчики — src/services/openai_errors.py; здесь только необработанные
# исключения: им нужны логгер и API_DEBUG этого модуля.

openai_errors.install_handlers(app)


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
# response_format REST → format общего слоя (нужен только для нормализации параметров;
# сам ответ по-прежнему рендерит transcript_formats.render)
_BACKEND_FORMATS = {"json": "json", "text": "text", "srt": "srt", "vtt": "vtt",
                    "verbose_json": "verbose", "diarized_json": "diarized"}


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
    try:
        with open(target, "wb") as out:
            while chunk := file.file.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_FILE_SIZE:
                    too_large = True
                    break
                out.write(chunk)
    except BaseException:
        # Нет места, обрыв чтения тела: без этого req_* оставалась навсегда — уборка
        # вызывающего (_cleanup) знает work_dir только после успешного возврата
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
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
    # Проверка и нормализация параметров — общий с MCP код; BackendError → конверт OpenAI в обработчике
    opts = transcription_api.prepare_options(
        transcription_api.TranscribeOptions(
            model=model, language=language, format=_BACKEND_FORMATS[response_format],
            word_timestamps="word" in granularities, diarize=_parse_bool(diarize),
            diarization_backend=diarization_backend, num_speakers=num_speakers,
            audio_preprocessing=audio_preprocessing, asr_backend=asr_backend, onnx_provider=onnx_provider),
        model_loader, hf_token=HF_TOKEN)

    loop = asyncio.get_running_loop()
    # Запись на диск — в executor, чтобы гигабайтная загрузка не блокировала loop
    work_dir, file_path = await loop.run_in_executor(None, _save_upload, file)
    progress_queue: asyncio.Queue[str] = asyncio.Queue()

    def blocking() -> dict[str, Any]:
        return transcription_api.run_transcription(
            file_path, work_dir, opts, model_loader=model_loader, stats_manager=stats_manager,
            loader_factory=ModelLoader, logger=logger,
            progress=lambda stage, fraction: openai_stream.queue_progress(loop, progress_queue, stage, fraction))

    async def run() -> dict[str, Any]:
        async with processing_semaphore:
            return await loop.run_in_executor(None, blocking)

    def render(result):
        return transcript_formats.render(
            response_format, result.get("utterances") or [], result.get("media_duration") or 0.0,
            language=language, granularities=granularities,
            diarized=bool(result.get("diarization", {}).get("applied")) or opts.diarize,
            subtitle_options=None)

    if not streaming:
        try:
            result = await run()
        except (OpenAIError, BackendError):
            # BackendError несёт код и причину (processing_failed: «Transcription failed: …») —
            # её конверт строит свой обработчик; общий ответ ниже — только для непредвиденного
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

    events = openai_stream.transcription_events(run, finished, progress_queue, render=render,
                                                verbose=response_format == "verbose_json", logger=logger)
    return StreamingResponse(events, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ==================== MCP ====================
# /mcp — Streamable HTTP того же MCP-сервера, что у `python -m src.mcp_server`: общий
# model_loader и семафор. Бэкенд строится после lifespan (модель к тому моменту
# загружена); ключи — те же, что у verify_api_key.


def _mcp_backend() -> LocalBackend:
    return LocalBackend(
        model_loader=model_loader, stats_manager=stats_manager, semaphore=processing_semaphore,
        upload_dir=UPLOAD_DIR, media_downloader=MediaDownloader(), loader_factory=ModelLoader, logger=logger,
        http_mode=True, max_file_size=MAX_FILE_SIZE, hf_token=HF_TOKEN, max_concurrent=MAX_CONCURRENT_TASKS,
        **backend_options_from_env())


def _mcp_key_store() -> KeyStore:
    """Тот же набор хэшей, что у verify_api_key (тесты подменяют VALID_API_KEY_HASHES до старта)."""
    store = KeyStore(API_KEYS_FILE)
    store.hashes = VALID_API_KEY_HASHES
    return store


mount_mcp(app, "/mcp", _mcp_backend, _mcp_key_store)


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
