#!/usr/bin/env python3
"""
GigaAM v3 Transcriber - Web GUI
Веб-интерфейс с авторизацией, полностью дублирующий функционал desktop GUI.
"""

import asyncio
import contextlib
import logging
import uuid
import warnings
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from platform import machine
from platform import platform as runtime_platform
from typing import Final

import aiofiles
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
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded

from src import __version__
from src.config import HF_TOKEN, MEDIA_EXTENSIONS, OUTPUT_FORMATS
from src.core.asr.models import ASR_MODELS
from src.core.subtitles import SubtitleOptions
from src.services import cli_tools, file_policy, llm_service, transcription_service
from src.services import health as health_service
from src.services import llm_settings as llm_settings_service
from src.services.api_keys import KeyStore
from src.services.mcp_backend import LocalBackend
from src.services.mcp_http import backend_options_from_env, mount_mcp
from src.utils.atomic_json import load_json, save_json_atomic
from src.utils.audio_converter import ffmpeg_available
from src.utils.diarization import normalize_diarization_backend
from src.utils.media_downloader import MediaDownloader
from src.utils.output_naming import find_result_file
from src.utils.processing_stats import ProcessingStats
from web import auth, jobs
from web.routes import auth as auth_routes
from web.state import STATIC_DIR, state
from web.task_registry import ALL_TASK_STATUSES, registry

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

LLM_EXPORT_FORMATS: Final[tuple[str, ...]] = ("txt", "md", "docx")
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


def _asr_health() -> dict[str, object]:
    # Публичный /health: без путей сервера (cache_root, repo)
    return health_service.public_asr_health(state.model_loader)


def _runtime_info() -> dict[str, object]:
    return health_service.runtime_info(runtime_platform, machine)


# ==================== УТИЛИТЫ ====================

def is_supported_format(filename: str) -> bool:
    return file_policy.is_supported_by_set(filename, MEDIA_EXTENSIONS)


def safe_filename(filename: str | None) -> str:
    return file_policy.safe_filename(filename)


def _user_task_or_404(task_id: str, user: str) -> dict:
    task = registry.user_task(task_id, user)
    if task is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return task


async def _save_upload(file: UploadFile, request: Request) -> tuple:
    if not is_supported_format(file.filename):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Неподдерживаемый формат: {file.filename}. Поддерживаемые: {', '.join(MEDIA_EXTENSIONS)}",
        )

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > state.max_file_size:
                max_gb = state.max_file_size / 1024 / 1024 / 1024
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"Файл слишком большой (макс. {max_gb:.1f} GB)",
                )
        except ValueError:
            pass

    task_id = uuid.uuid4().hex
    filename = safe_filename(file.filename)
    file_path = state.upload_dir / f"{task_id}_{filename}"
    file_size = 0

    try:
        async with aiofiles.open(file_path, 'wb') as f:
            while chunk := await file.read(1024 * 1024):
                file_size += len(chunk)
                if file_size > state.max_file_size:
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

mount_mcp(app, "/mcp", _mcp_backend, lambda: state.key_store)


# ==================== ЭНДПОИНТЫ АВТОРИЗАЦИИ ====================


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
    if state.allow_client_llm_cli:
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



# ==================== ЭНДПОИНТЫ GUI ====================

@app.get("/api/formats")
async def get_formats(user: str = Depends(auth.require_auth)):
    return {"formats": OUTPUT_FORMATS}


@app.get("/api/device")
async def get_device(user: str = Depends(auth.require_auth)):
    if state.model_loader and state.model_loader.device:
        return {"device": state.model_loader.device.upper()}
    return {"device": "CPU"}


@app.get("/api/asr-options")
async def get_asr_options(user: str = Depends(auth.require_auth)):
    if state.model_loader is None:
        raise HTTPException(status_code=503, detail="ASR model loader не инициализирован")
    return {
        "backends": transcription_service.available_asr_backends(),
        "models": ASR_MODELS,
        "onnx_providers": list(transcription_service.ONNX_PROVIDERS),
        "defaults": transcription_service.normalize_asr_selection(state.model_loader).as_dict(),
        "active": state.model_loader.diagnostics(),
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
            state.model_loader,
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
    user: str = Depends(auth.require_auth),
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
        registry.register(task_id, filename, file_size, user, form.asr_selection)
        registry.tasks[task_id].update(form.task_fields())
        registry.persist()

        jobs.start_background(
            jobs.process_transcription(
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
    user: str = Depends(auth.require_auth),
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
    registry.register(task_id, url.split("/")[-1][:80], 0, user, form.asr_selection)
    registry.tasks[task_id].update(form.task_fields())
    registry.tasks[task_id]['status'] = 'downloading'
    registry.tasks[task_id]['stage'] = 'Загрузка медиа...'
    registry.tasks[task_id]['message'] = 'Загрузка по URL'
    registry.persist()

    jobs.start_background(
        jobs.download_and_process(
            task_id, url, form.output_formats, form.enable_diarization,
            form.diarization_backend, form.num_speakers,
            form.asr_selection,
            form.subtitle_options,
        )
    )

    return {"task_id": task_id, "url": url}


# ==================== ЭНДПОИНТЫ ЗАДАЧ ====================

@app.get("/api/tasks")
async def list_tasks(user: str = Depends(auth.require_auth)):
    tasks = [registry.visible_copy(task) for task in registry.tasks.values() if task.get('user') == user]
    tasks.sort(key=lambda x: x.get('created_at') or '', reverse=True)
    return {"total": len(tasks), "tasks": tasks}


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str, user: str = Depends(auth.require_auth)):
    # Без result_files: там абсолютные пути сервера; файлы отдают /result и /download
    return registry.visible_copy(_user_task_or_404(task_id, user))


@app.get("/api/tasks/{task_id}/logs")
async def get_task_logs(task_id: str, user: str = Depends(auth.require_auth)):
    _user_task_or_404(task_id, user)
    return {"logs": registry.logs.get(task_id, [])}


@app.get("/api/tasks/{task_id}/result")
async def get_task_result(task_id: str, user: str = Depends(auth.require_auth)):
    task = _user_task_or_404(task_id, user)
    if task['status'] != 'completed':
        raise HTTPException(status_code=400, detail=f"Задача не завершена (статус: {task['status']})")

    result_dir = registry.result_dir(task_id)
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
    user: str = Depends(auth.require_auth),
):
    task = _user_task_or_404(task_id, user)
    if task['status'] != 'completed':
        raise HTTPException(status_code=400, detail="Задача не завершена")

    result_dir = registry.result_dir(task_id)
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
async def delete_task(task_id: str, user: str = Depends(auth.require_auth)):
    task = _user_task_or_404(task_id, user)
    if task['status'] == 'processing':
        raise HTTPException(status_code=400, detail="Нельзя удалить задачу в процессе обработки")

    registry.delete_data(task_id, task)

    registry.tasks.pop(task_id, None)
    registry.logs.pop(task_id, None)
    registry.persist()

    return {"ok": True, "message": "Задача удалена"}


@app.get("/api/llm/tools")
async def llm_tools(fresh: bool = False, user: str = Depends(auth.require_auth)):
    """Реестр LLM-провайдеров и статусы CLI-инструментов на сервере (скан — в пуле потоков)."""
    statuses = await asyncio.to_thread(cli_tools.scan, None, fresh=fresh)
    return {
        "providers": cli_tools.canonical_provider_names(),
        "tools": [status.to_dict() for status in statuses],
        # false — пути/аргументы CLI и инструменты агента из формы сервер игнорирует
        "client_cli": state.allow_client_llm_cli,
    }


@app.post("/api/llm/tools/check")
async def llm_tool_check(
    provider: str = Form(...),
    path: str = Form(""),
    user: str = Depends(auth.require_auth),
):
    try:
        spec = cli_tools.provider_by_name(provider)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Неизвестный провайдер: {provider}") from exc

    def check():
        # Присланный путь запускается (`<path> --version`) только с разрешения оператора
        override = (path.strip() or None) if state.allow_client_llm_cli else _server_tool_override(spec)
        return cli_tools.resolve_tool(spec, override)

    status = await asyncio.to_thread(check)
    return {"tool": status.to_dict()}


async def _llm_answer(settings: dict, text: str, prompt: str) -> str:
    """Один вызов LLM-провайдера в пуле потоков под state.llm_semaphore.

    Запрос /api/llm/process — это items×modes вызовов CLI/API по 10 минут
    таймаута каждый; без ограничения несколько вкладок запускали их сколько
    угодно параллельно (процессы агентных CLI, потоки executor-а). Семафор свой,
    не общий с транскрибацией: длинная выжимка не должна держать очередь ASR.
    """
    gate = state.llm_semaphore if state.llm_semaphore is not None else contextlib.nullcontext()
    async with gate:
        return await asyncio.get_running_loop().run_in_executor(None, _run_llm_provider, settings, text, prompt)


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
    user: str = Depends(auth.require_auth),
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

    if len(transcript_files) > 20:
        raise HTTPException(status_code=400, detail="Максимум 20 транскриптов за раз")
    items = []
    manual_text = (manual_text or "").strip()
    uploaded_names = []
    if manual_text:
        items.append({"name": "manual_transcript", "text": manual_text})
    total_bytes = 0
    for upload in transcript_files:
        # Не больше лимита за чтение: тело без Content-Length гард не меряет,
        # а upload.read() без аргумента держал бы в памяти любой объём
        raw = await upload.read(state.max_llm_body_size + 1)
        total_bytes += len(raw)
        if total_bytes > state.max_llm_body_size:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Транскрипты больше лимита ({state.max_llm_body_size / 1024 / 1024:.0f} MB)",
            )
        text = raw.decode("utf-8", errors="ignore").strip()
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
    unknown = [fmt for fmt in formats if fmt not in LLM_EXPORT_FORMATS]
    if unknown:
        # Формат идёт в имя файла: без проверки он попадал в saved_files как есть
        raise HTTPException(
            status_code=400,
            detail=f"Неизвестный формат вывода: {', '.join(unknown)}. Доступны: {', '.join(LLM_EXPORT_FORMATS)}",
        )

    job_id = uuid.uuid4().hex
    job_dir = state.llm_results_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    results = []
    saved_files = []
    for item in items:
        blocks = []
        for mode_suffix, mode_label, prompt in modes:
            answer = await _llm_answer(llm_settings, item["text"], prompt)
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
                # Без абсолютного пути на сервере: скачивание — /api/llm/download/{job_id}/{name}
                saved_files.append({"name": save_path.name, "format": fmt})
        results.append("\n\n".join(blocks))

    result_text = "\n\n".join(results)
    meta = {"job_id": job_id, "provider": provider, "created_at": datetime.now().isoformat(), "user": user, "files": uploaded_names}
    save_json_atomic(str(job_dir / "meta.json"), meta)
    return {"job_id": job_id, "provider": provider, "result_text": result_text, "saved_files": saved_files}

@app.get("/api/llm/download/{job_id}/{filename}")
async def llm_download(job_id: str, filename: str, user: str = Depends(auth.require_auth)):
    job_dir = state.llm_results_dir / job_id
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
    user: str = Depends(auth.require_auth),
):
    statuses = {status.strip() for status in status_filter.split(",") if status.strip()}
    if "all" in statuses:
        statuses = set(ALL_TASK_STATUSES)

    removed = 0
    for tid in list(registry.tasks.keys()):
        task = registry.tasks[tid]
        if task.get('user') != user:
            continue
        if task['status'] in statuses:
            registry.delete_data(tid, task)
            registry.tasks.pop(tid, None)
            registry.logs.pop(tid, None)
            removed += 1

    registry.persist()

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
