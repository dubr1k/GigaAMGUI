#!/usr/bin/env python3
"""
GigaAM v3 Transcriber - Web GUI
Веб-интерфейс с авторизацией, полностью дублирующий функционал desktop GUI.

Здесь только сборка приложения: lifespan (модель, семафоры, ключи /mcp,
восстановление задач), middleware, /mcp и подключение маршрутов из
web/routes/*. Настройки и общее состояние — web/state.py, задачи —
web/task_registry.py, фоновые задачи — web/jobs.py, вход — web/auth.py.
Docker запускает `web.web_app:app`.
"""

import asyncio
import logging
import warnings
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded

from src import __version__
from src.config import HF_TOKEN
from src.services.api_keys import KeyStore
from src.services.mcp_backend import LocalBackend
from src.services.mcp_http import backend_options_from_env, mount_mcp
from src.utils.audio_converter import ffmpeg_available
from src.utils.media_downloader import MediaDownloader
from src.utils.processing_stats import ProcessingStats
from web import auth
from web.routes import auth as auth_routes
from web.routes import llm as llm_routes
from web.routes import pages as pages_routes
from web.routes import progress as progress_routes
from web.routes import tasks as tasks_routes
from web.routes import transcribe as transcribe_routes
from web.state import STATIC_DIR, state
from web.task_registry import registry

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


# ==================== LIFESPAN ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("=" * 60)
    print("GigaAM v3 Transcriber - Web GUI")
    print("=" * 60)

    # Ключи /mcp; при первом старте создаётся и печатается (один раз) первый ключ
    state.key_store = KeyStore(state.api_keys_file).load()

    if not ffmpeg_available():
        print("ВНИМАНИЕ: ffmpeg/ffprobe не найдены в PATH!")

    if not state.hf_token or not state.hf_token.startswith("hf_"):
        print("ВНИМАНИЕ: HF_TOKEN не настроен!")
        print("Диаризация будет недоступна без HF_TOKEN.")

    print("Загрузка модели GigaAM-v3...")
    state.model_loader = state.loader_factory()
    success = state.model_loader.load_model(logger=print)
    if not success:
        print("ОШИБКА загрузки модели!")
        raise RuntimeError("Ошибка загрузки модели")
    device_name = state.model_loader.device.upper() if state.model_loader.device else "N/A"
    print(f"Модель загружена. Устройство: {device_name}")

    state.stats_manager = ProcessingStats(state.stats_file or str(state.results_dir / "processing_stats.json"))
    state.media_downloader = MediaDownloader()
    state.processing_semaphore = asyncio.Semaphore(state.max_concurrent_tasks)
    state.llm_semaphore = asyncio.Semaphore(state.max_concurrent_llm)

    registry.restore()

    print(f"Web GUI готов (порт {state.port}, макс. {state.max_concurrent_tasks} задач)")
    print("=" * 60)

    yield

    print("Остановка Web GUI...")


# ==================== MCP ====================
# /mcp — Streamable HTTP MCP-сервера над тем же model_loader и семафором; вход только по
# API-ключу (сессии веб-панели здесь не действуют). Бэкенд строится после lifespan.


def _mcp_backend() -> LocalBackend:
    return LocalBackend(
        model_loader=state.model_loader, stats_manager=state.stats_manager, semaphore=state.processing_semaphore,
        upload_dir=state.upload_dir, media_downloader=state.media_downloader, loader_factory=state.loader_factory,
        logger=logging.getLogger("GigaAM"),  # веб-панель логгер не настраивает: ошибки уходят в stderr
        http_mode=True, max_file_size=state.max_file_size, hf_token=state.hf_token, max_concurrent=state.max_concurrent_tasks,
        **backend_options_from_env())


# ==================== ПРИЛОЖЕНИЕ ====================

app = FastAPI(
    title="GigaAM v3 Transcriber - Web GUI",
    version=__version__,
    lifespan=lifespan,
)
app.state.limiter = auth.limiter

app.add_middleware(auth.BodyGuard)
app.add_exception_handler(RateLimitExceeded, auth.login_rate_limited)

# Панель и её API — один origin, CORS ей не нужен. Отдельно поднятый фронтенд
# (разработка) перечисляется в WEB_TRUSTED_ORIGINS; раньше здесь был зашитый
# localhost:8001 с credentials — любая страница на этом порту читала API панели.
# Добавляется после BodyGuard, т.е. снаружи: его 401/403/413 тоже с CORS-заголовками.
if state.trusted_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(state.trusted_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Маршруты — до mount_mcp: он оборачивает итоговый lifespan приложения, а
# include_router в FastAPI сливает lifespan-ы подключаемых роутеров.
app.include_router(auth_routes.router)
app.include_router(transcribe_routes.router)
app.include_router(tasks_routes.router)
app.include_router(llm_routes.router)
app.include_router(progress_routes.router)
app.include_router(pages_routes.router)

mount_mcp(app, "/mcp", _mcp_backend, lambda: state.key_store)


# ==================== ЗАПУСК ====================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "web.web_app:app",
        host="0.0.0.0",
        port=state.port,
        reload=False,
    )
