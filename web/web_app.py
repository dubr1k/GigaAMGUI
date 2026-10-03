#!/usr/bin/env python3
"""
GigaAM v3 Transcriber - Web GUI
Веб-интерфейс с авторизацией, полностью дублирующий функционал desktop GUI.
"""

import asyncio
import logging
import warnings
from contextlib import asynccontextmanager
from platform import machine
from platform import platform as runtime_platform

from fastapi import (
    Depends,
    FastAPI,
    Request,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded

from src import __version__
from src.config import HF_TOKEN
from src.services import health as health_service
from src.services.api_keys import KeyStore
from src.services.mcp_backend import LocalBackend
from src.services.mcp_http import backend_options_from_env, mount_mcp
from src.utils.audio_converter import ffmpeg_available
from src.utils.media_downloader import MediaDownloader
from src.utils.processing_stats import ProcessingStats
from web import auth
from web.routes import auth as auth_routes
from web.routes import llm as llm_routes
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


def _asr_health() -> dict[str, object]:
    # Публичный /health: без путей сервера (cache_root, repo)
    return health_service.public_asr_health(state.model_loader)


def _runtime_info() -> dict[str, object]:
    return health_service.runtime_info(runtime_platform, machine)


# ==================== УТИЛИТЫ ====================



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

app.include_router(auth_routes.router)
app.include_router(transcribe_routes.router)
app.include_router(tasks_routes.router)
app.include_router(llm_routes.router)

mount_mcp(app, "/mcp", _mcp_backend, lambda: state.key_store)


# ==================== ЭНДПОИНТЫ АВТОРИЗАЦИИ ====================



# ==================== ЭНДПОИНТЫ GUI ====================


# ==================== ЭНДПОИНТЫ ЗАДАЧ ====================



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
        for tid, task in registry.tasks.items():
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
            self.log_lengths = {tid: len(registry.logs.get(tid, [])) for tid in current}
            return {'snapshot': True, 'tasks': current, 'logs': {}}

        changed = {tid: data for tid, data in current.items() if self.last.get(tid) != data}
        new_logs = {}
        for tid, logs in list(registry.logs.items()):
            task = registry.tasks.get(tid)
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
async def progress_stream(request: Request, user: str = Depends(auth.require_auth)):
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
        "version": __version__,
        "model_loaded": state.model_loader is not None and state.model_loader.is_loaded(),
        "runtime": _runtime_info(),
        "asr": _asr_health(),
        "active_tasks": sum(1 for t in registry.tasks.values() if t['status'] in ('processing', 'downloading')),
        "total_tasks": len(registry.tasks),
    }


# ==================== ЗАПУСК ====================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "web.web_app:app",
        host="0.0.0.0",
        port=state.port,
        reload=False,
    )
