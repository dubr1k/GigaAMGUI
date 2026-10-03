"""Страница панели, favicon, robots.txt и неавторизованный /health."""
from platform import machine
from platform import platform as runtime_platform

from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse

from src import __version__
from src.services import health as health_service
from web.state import STATIC_DIR, state
from web.task_registry import registry

router = APIRouter()


def _asr_health() -> dict[str, object]:
    # Публичный /health: без путей сервера (cache_root, repo)
    return health_service.public_asr_health(state.model_loader)


def _runtime_info() -> dict[str, object]:
    return health_service.runtime_info(runtime_platform, machine)


@router.get("/favicon.ico")
async def favicon():
    return FileResponse(STATIC_DIR / "icon.svg", media_type="image/svg+xml")


@router.get("/robots.txt")
async def robots_txt():
    return FileResponse(STATIC_DIR / "robots.txt", media_type="text/plain")


@router.get("/", response_class=HTMLResponse)
async def index():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return HTMLResponse(index_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>GigaAM Web GUI</h1><p>index.html not found</p>", status_code=404)


@router.get("/health")
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
