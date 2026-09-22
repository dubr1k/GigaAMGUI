"""Один путь транскрибации и суммаризации для REST API (`api.py`) и MCP-сервера.

`run_transcription` — блокирующее ядро, вынесенное из `api.py`: загрузчик под
запрос (если просят другой backend/модель/провайдер) берётся и выгружается
здесь, процессор запускается с `output_formats=[]`, прогресс процессора
приводится к `progress(stage, fraction)`. `LocalBackend` оборачивает его для
MCP: разбирает источник (`url` / `path` / `audio_base64`), кладёт файл в
`mkdtemp(prefix="mcp_", dir=upload_dir)`, крутит блокирующую часть в executor
под общим семафором и убирает рабочую директорию в `finally`.

Ошибки — `BackendError(code, message, status)` с кодами REST-контракта;
`api.py` переводит их в конверт OpenAI, MCP-сервер — в текст `[code] message`.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import dataclasses
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src import __version__
from src.config import AUDIO_PREPROCESSING_MODE, HF_TOKEN, SUPPORTED_FORMATS
from src.core.asr.models import ASR_MODELS
from src.services import cli_tools, file_policy, llm_service, llm_settings, transcript_formats, transcription_service
from src.services import health as health_service
from src.services.llm_worker_service import PROMPTS
from src.utils.audio_preprocessing import normalize_preprocessing_mode
from src.utils.diarization import normalize_diarization_backend

ProgressFn = Callable[[str, "float | None"], None]

FORMATS = ("text", "json", "verbose", "diarized", "srt", "vtt")
SUMMARY_MODES = tuple(PROMPTS) + ("custom",)
_MiB = 1024 * 1024


class BackendError(Exception):
    """Ошибка с REST-кодом; `param` — имя параметра для конверта OpenAI."""

    def __init__(self, code: str, message: str, status: int = 400, *, param: str | None = None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.status = status
        self.param = param


DEFAULT_MODEL = "v3_e2e_rnnt"


@dataclass
class TranscribeOptions:
    model: str = DEFAULT_MODEL            # id модели или алиас; resolve_model приводит к id
    language: str | None = None
    format: str = "json"                  # text | json | verbose | diarized | srt | vtt
    word_timestamps: bool = False
    diarize: bool = False
    diarization_backend: str = "pyannote"
    num_speakers: int | None = None
    audio_preprocessing: str | None = None
    asr_backend: str | None = None
    onnx_provider: str | None = None


# ==================== МОДЕЛИ ====================

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
        raise BackendError("model_not_found", f"The model '{model}' does not exist.", 404, param="model")
    return name


def model_object(model_id: str) -> dict[str, Any]:
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


def models_payload(model_loader) -> dict[str, Any]:
    return {
        "object": "list",
        "data": [model_object(m) for m in ASR_MODELS],
        "gigaam": {
            "backends": transcription_service.available_asr_backends(),
            "onnx_providers": list(transcription_service.ONNX_PROVIDERS),
            "active": model_loader.diagnostics() if model_loader is not None else {},
        },
    }


# ==================== ПАРАМЕТРЫ ====================


def prepare_options(opts: TranscribeOptions, model_loader, *, hf_token: str | None = HF_TOKEN) -> TranscribeOptions:
    """Проверяет и нормализует параметры (порядок ошибок — как у REST).

    Возвращает копию с уже включённой диаризацией для `diarized`, каноническим
    именем backend-а диаризации и подставленным режимом предобработки.
    """
    if opts.format not in FORMATS:
        raise BackendError("invalid_request", f"Unsupported format '{opts.format}'. Use one of: {', '.join(FORMATS)}.",
                           param="format")
    model = resolve_model(opts.model)
    diarize = bool(opts.diarize) or opts.format == "diarized"
    try:
        diarization_backend = normalize_diarization_backend(opts.diarization_backend)
    except ValueError as exc:
        raise BackendError("unsupported_parameter",
                           f"Unknown diarization_backend '{opts.diarization_backend}'. Use pyannote or sortformer.",
                           param="diarization_backend") from exc
    if opts.num_speakers is not None and (isinstance(opts.num_speakers, bool)
                                          or not isinstance(opts.num_speakers, int) or opts.num_speakers < 1):
        raise BackendError("unsupported_parameter", "num_speakers must be an integer >= 1.", param="num_speakers")
    if diarize and diarization_backend == "sortformer" and opts.num_speakers is not None:
        raise BackendError("unsupported_parameter", "num_speakers cannot be combined with diarization_backend=sortformer.",
                           param="num_speakers")
    if diarize and diarization_backend == "pyannote" and not (hf_token and hf_token.startswith("hf_")):
        raise BackendError("diarization_unavailable", "Diarization is unavailable: HF_TOKEN is not configured on the server.",
                           503)
    if model_loader is None:
        raise BackendError("model_not_loaded", "ASR model is not loaded.", 503)
    try:
        selection = transcription_service.normalize_asr_selection(
            model_loader, backend=opts.asr_backend, model=model, onnx_provider=opts.onnx_provider)
    except ValueError as exc:
        raise BackendError("unsupported_parameter", str(exc), param="asr_backend") from exc
    try:
        preprocessing = normalize_preprocessing_mode(opts.audio_preprocessing or AUDIO_PREPROCESSING_MODE)
    except ValueError as exc:
        raise BackendError("unsupported_parameter",
                           f"Unknown audio_preprocessing '{opts.audio_preprocessing}'. Use off, auto, light or denoise.",
                           param="audio_preprocessing") from exc
    return dataclasses.replace(opts, model=selection.model, asr_backend=selection.backend,
                               onnx_provider=selection.onnx_provider, diarize=diarize,
                               diarization_backend=diarization_backend, audio_preprocessing=preprocessing)


# ==================== ЯДРО ====================


def _adapt_progress(progress: ProgressFn | None):
    """Колбэк для процессора: он шлёт `ProgressEvent` одним аргументом либо (stage, value) — legacy."""
    if progress is None:
        return None

    def callback(event_or_stage, value=None, **_):
        stage = getattr(event_or_stage, "stage", None) or (event_or_stage if isinstance(event_or_stage, str) else "processing")
        fraction = getattr(event_or_stage, "file_progress", None)
        if fraction is None:
            fraction = value
        progress(stage, float(fraction) if isinstance(fraction, (int, float)) else None)

    return callback


def run_transcription(file_path: Path, work_dir: Path, opts: TranscribeOptions, *, model_loader, stats_manager,
                      loader_factory, logger, progress: ProgressFn | None) -> dict[str, Any]:
    """Блокирующая транскрибация `file_path`; `opts` — только из `prepare_options`
    (там уже проверен и нормализован ASR-выбор: backend/model/onnx_provider заполнены).

    Берёт загрузчик под запрос, если backend/модель/провайдер отличаются от
    серверного, и выгружает его в `finally`; результат процессора возвращается
    как есть (`utterances`, `media_duration`, `diarization`).
    """
    if not (opts.asr_backend and opts.onnx_provider):
        raise ValueError("run_transcription() expects options prepared by prepare_options()")
    asr_selection = transcription_service.AsrSelection(opts.asr_backend, opts.model, opts.onnx_provider)
    request_loader, owns = transcription_service.acquire_request_model_loader(
        model_loader, asr_selection, loader_factory=loader_factory)
    try:
        if owns and not request_loader.load_model(logger=(logger.info if logger else None)):
            raise BackendError("processing_failed", "Could not load the requested ASR backend.", 500)
        processor = transcription_service.build_processor(
            request_loader, stats_manager,
            logger=(lambda msg: logger.debug(f"[transcribe] {msg}")) if logger else None,
            progress_callback=_adapt_progress(progress),
        )
        result = processor.process_file(
            str(file_path), str(work_dir), 0, 1, file_path.name,
            enable_diarization=opts.diarize, num_speakers=opts.num_speakers, output_formats=[],
            diarization_backend=opts.diarization_backend, audio_preprocessing_mode=opts.audio_preprocessing,
        )
        if not result.get("success"):
            raise BackendError("processing_failed", "Transcription failed on the server. See the server log.", 500)
        return result
    finally:
        if owns:  # иначе модель под нестандартный backend/model живёт до остановки процесса
            request_loader.unload()


def render_result(result: dict[str, Any], opts: TranscribeOptions) -> dict[str, Any]:
    """Результат MCP: `text/duration/language/usage` плюс `segments`/`words`/`subtitles` по формату."""
    utts = result.get("utterances") or []
    duration = float(result.get("media_duration") or 0.0)
    diarization = result.get("diarization") or {}
    applied = bool(diarization.get("applied"))
    diarized = applied or bool(opts.diarize)
    out: dict[str, Any] = {
        "text": transcript_formats.full_text(utts),
        "duration": duration,
        "language": opts.language or transcript_formats.DEFAULT_LANGUAGE,
        "usage": transcript_formats.usage(duration),
    }
    if opts.format == "verbose":
        granularities = {"segment", "word"} if opts.word_timestamps else {"segment"}
        verbose = transcript_formats.build_verbose(utts, duration, opts.language, granularities, diarized)
        out["segments"] = verbose["segments"]
        if "words" in verbose:
            out["words"] = verbose["words"]
    elif opts.format == "diarized":
        out["segments"] = transcript_formats.build_diarized(utts, duration)["segments"]
    if opts.format in ("verbose", "diarized"):
        # applied=False при diarized — спикеры-заглушки ("A" у всех), агенту важно это видеть
        out["diarization"] = {"requested": bool(diarization.get("requested")) or bool(opts.diarize), "applied": applied}
    elif opts.format == "srt":
        out["subtitles"] = transcript_formats.build_srt(utts)
    elif opts.format == "vtt":
        out["subtitles"] = transcript_formats.build_vtt(utts)
    return out


# ==================== БЭКЕНД ====================


def _is_supported(filename: str) -> bool:
    return file_policy.is_supported_by_glob(filename, SUPPORTED_FORMATS[1])


def _unsupported(filename: str) -> BackendError:
    return BackendError("unsupported_file",
                        f"Unsupported file type: '{filename}'. Supported: {', '.join(SUPPORTED_FORMATS[1])}",
                        param="file")


def _too_large(limit: int) -> BackendError:
    return BackendError("file_too_large", f"File exceeds the maximum size of {limit} bytes.", 413, param="file")


class _Job:
    """Рабочая директория одного запроса + разрешение семафора.

    Отмена MCP-задачи (`notifications/cancelled`) не останавливает поток в executor:
    процессор продолжает писать в `work_dir`. Поэтому уборка и освобождение
    семафора привязаны к done-callback future, а корутина ждёт через
    `asyncio.shield` — как `finished()` в `api.py`.
    """

    def __init__(self, work_dir: Path, backend: LocalBackend):
        self.work_dir = work_dir
        self.backend = backend
        self.running = False        # в executor есть незавершённый шаг
        self.cancelled = False      # ожидающую корутину отменили
        self.holds_permit = False
        self.finalized = False

    def finalize(self) -> None:
        if self.finalized:
            return
        self.finalized = True
        shutil.rmtree(self.work_dir, ignore_errors=True)
        if self.holds_permit:
            self.holds_permit = False
            self.backend._active -= 1
            self.backend.semaphore.release()

    async def run(self, loop: asyncio.AbstractEventLoop, fn):
        """Один блокирующий шаг в executor; результат/ошибка — как у `fn`, но
        `BackendError`-неизвестные исключения переводятся в `processing_failed`."""
        self.running = True
        future = loop.run_in_executor(None, fn)

        def done(f: asyncio.Future) -> None:
            self.running = False
            exc = None if f.cancelled() else f.exception()  # забираем всегда — иначе «never retrieved»
            if exc is not None and not isinstance(exc, BackendError) and self.backend.logger:
                self.backend.logger.error(f"[transcribe] failed: {exc}", exc_info=exc)
            if exc is not None or self.cancelled:
                self.finalize()

        future.add_done_callback(done)
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            self.cancelled = True
            if future.done():  # callback уже отработал, не зная об отмене
                self.finalize()
            raise
        except BackendError:
            raise
        except Exception as exc:
            raise BackendError("processing_failed", "Transcription failed on the server. See the server log.", 500) from exc


class LocalBackend:
    """Реализация на процесс: тот же `model_loader` и семафор, что у REST/веб."""

    def __init__(self, *, model_loader, stats_manager, semaphore: asyncio.Semaphore | None, upload_dir: Path,
                 media_downloader, loader_factory, logger, http_mode: bool, allow_paths: bool,
                 path_root: Path | None, max_file_size: int, max_inline_bytes: int,
                 hf_token: str | None = HF_TOKEN, llm_config_dir: Path | None = None,
                 max_concurrent: int | None = None):
        self.model_loader = model_loader
        self.stats_manager = stats_manager
        self.semaphore = semaphore
        self.upload_dir = Path(upload_dir)
        self.media_downloader = media_downloader
        self.loader_factory = loader_factory
        self.logger = logger
        self.http_mode = http_mode
        self.allow_paths = allow_paths
        self.path_root = Path(path_root).resolve() if path_root is not None else None
        self.max_file_size = max_file_size
        self.max_inline_bytes = max_inline_bytes
        self.hf_token = hf_token
        self.llm_config_dir = llm_config_dir
        self.max_concurrent = max_concurrent  # ёмкость семафора — asyncio её не отдаёт, задаёт вызывающий
        self._active = 0  # задач в executor под семафором

    # ---------- transcribe ----------

    async def transcribe(self, *, url: str | None = None, path: str | None = None, audio_base64: str | None = None,
                         filename: str | None = None, opts: TranscribeOptions, progress: ProgressFn | None) -> dict[str, Any]:
        sources = [kind for kind, value in (("url", url), ("path", path), ("inline", audio_base64)) if value]
        if len(sources) != 1:
            raise BackendError("invalid_request", "Provide exactly one of url, path or audio_base64.", param="url")
        kind = sources[0]
        opts = prepare_options(opts, self.model_loader, hf_token=self.hf_token)
        if kind == "path":
            self._check_path_policy(path)
        elif kind == "inline":
            self._check_inline(audio_base64, filename)

        loop = asyncio.get_running_loop()
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        work_dir = Path(await loop.run_in_executor(None, lambda: tempfile.mkdtemp(prefix="mcp_", dir=self.upload_dir)))
        job = _Job(work_dir, self)

        if kind == "url":
            def resolve() -> Path:
                if progress:
                    progress("downloading", None)
                return self._download(url, work_dir, progress)
        elif kind == "path":
            def resolve() -> Path:
                return self._local_file(path)
        else:
            def resolve() -> Path:
                return self._write_inline(audio_base64, filename, work_dir)

        def blocking() -> dict[str, Any]:
            return run_transcription(file_path, work_dir, opts, model_loader=self.model_loader,
                                     stats_manager=self.stats_manager, loader_factory=self.loader_factory,
                                     logger=self.logger, progress=progress)

        try:
            file_path = await job.run(loop, resolve)
            if self.semaphore is not None:
                await self.semaphore.acquire()
                job.holds_permit = True
                self._active += 1
            result = await job.run(loop, blocking)
        except BaseException:
            # Отмена или ошибка, пока в executor ничего не крутится — убираем сразу; если поток
            # ещё работает, уборка и освобождение семафора привязаны к его done-callback (job.run)
            if not job.running:
                job.finalize()
            raise
        job.finalize()
        out = render_result(result, opts)
        out["source"] = {"kind": kind, "name": file_path.name}
        return out

    def _check_path_policy(self, path: str) -> None:
        if self.http_mode and not self.allow_paths:
            raise BackendError("paths_not_allowed",
                               "Local paths are disabled on this server; send a url or audio_base64.", 403, param="path")

    def _local_file(self, path: str) -> Path:
        resolved = Path(path).expanduser().resolve()
        if self.path_root is not None and not resolved.is_relative_to(self.path_root):
            raise BackendError("path_outside_root", f"Path is outside the allowed root: '{path}'.", 403, param="path")
        if not resolved.is_file():
            raise BackendError("file_not_found", f"File not found: '{path}'.", 404, param="path")
        if not _is_supported(resolved.name):
            raise _unsupported(resolved.name)
        if resolved.stat().st_size > self.max_file_size:
            raise _too_large(self.max_file_size)
        return resolved

    def _check_inline(self, audio_base64: str, filename: str | None) -> None:
        if not filename:
            raise BackendError("invalid_request", "filename is required with audio_base64.", param="filename")
        name = file_policy.safe_filename(filename)
        if not _is_supported(name):
            raise _unsupported(name)
        # 4 символа base64 = 3 байта; проверяем до декодирования, чтобы не держать лишнее в памяти
        if len(audio_base64) * 3 // 4 - 2 > self.max_inline_bytes:
            raise _too_large(self.max_inline_bytes)

    def _write_inline(self, audio_base64: str, filename: str, work_dir: Path) -> Path:
        try:
            data = base64.b64decode(audio_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise BackendError("invalid_request", "audio_base64 is not valid base64.", param="audio_base64") from exc
        if len(data) > self.max_inline_bytes:
            raise _too_large(self.max_inline_bytes)
        target = work_dir / file_policy.safe_filename(filename)
        target.write_bytes(data)
        return target

    def _download(self, url: str, work_dir: Path, progress: ProgressFn | None) -> Path:
        def on_percent(percent) -> None:  # MediaDownloader отдаёт 0..100
            if progress and isinstance(percent, (int, float)):
                progress("downloading", max(0.0, min(float(percent) / 100.0, 1.0)))

        try:
            downloaded = self.media_downloader.download(url, str(work_dir), progress_callback=on_percent,
                                                        max_filesize=self.max_file_size)
        except Exception as exc:
            if self.logger:
                self.logger.error(f"[transcribe] download failed: {exc}", exc_info=True)
            raise BackendError("download_failed", f"Could not download '{url}': {_first_line(exc)}", 502,
                               param="url") from exc
        files = list(getattr(downloaded, "files", None) or [])
        if not files:
            raise BackendError("download_failed", f"Nothing was downloaded from '{url}'.", 502, param="url")
        target = Path(files[0])
        if not _is_supported(target.name):
            raise _unsupported(target.name)
        if target.stat().st_size > self.max_file_size:
            raise _too_large(self.max_file_size)
        return target

    # ---------- summarize ----------

    async def summarize(self, text: str, mode: str, prompt: str | None, provider: str | None,
                        model: str | None) -> dict[str, Any]:
        if not (text or "").strip():
            raise BackendError("invalid_request", "text must not be empty.", param="text")
        if mode not in SUMMARY_MODES:
            raise BackendError("unsupported_parameter", f"Unknown mode '{mode}'. Use one of: {', '.join(SUMMARY_MODES)}.",
                               param="mode")
        if mode == "custom":
            if not (prompt or "").strip():
                raise BackendError("prompt_required", "prompt is required when mode is 'custom'.", param="prompt")
            prompt_text = prompt.strip()
        else:
            prompt_text = PROMPTS[mode]
        overrides = {k: v for k, v in (("provider", provider), ("model", model)) if v}

        def prepare() -> tuple[dict[str, Any], str]:
            # В executor: первый resolve() зовёт cli_tools.scan() (`<tool> --version`, до 10 с)
            # и читает файлы настроек — на loop это стопорило бы SSE REST и прогресс веб-панели
            settings = llm_settings.resolve(overrides, config_dir=self.llm_config_dir)
            try:
                canonical = cli_tools.provider_by_name(settings.get("provider") or "API").name
            except KeyError as exc:
                raise BackendError("unsupported_parameter",
                                   f"Unknown provider '{settings.get('provider')}'. Use one of: {', '.join(cli_tools.canonical_provider_names())}.",
                                   param="provider") from exc
            settings["provider"] = canonical
            if self.http_mode:
                # Удалённый держатель ключа не должен получать CLI-агента с инструментами
                # через summarize(prompt=...), даже если чекбокс включён в настройках хоста
                settings["llm_allow_tools"] = False
            return settings, canonical

        loop = asyncio.get_running_loop()
        settings, canonical = await loop.run_in_executor(None, prepare)
        try:
            answer = await loop.run_in_executor(
                None, lambda: llm_service.run_provider(settings, text, prompt_text, provider=canonical, strict_empty_cli=True))
        except llm_service.UnknownLLMProvider as exc:
            raise BackendError("unsupported_parameter", f"Unknown provider '{exc.provider}'.", param="provider") from exc
        except Exception as exc:
            if self.logger:
                self.logger.error(f"[summarize] {canonical} failed: {exc}", exc_info=True)
            raise BackendError("llm_failed", f"LLM provider '{canonical}' failed: {_first_line(exc)}", 502) from exc
        return {"mode": mode, "provider": canonical, "model": settings.get("model") or "", "answer": answer}

    # ---------- introspection ----------

    def models(self) -> dict[str, Any]:
        return models_payload(self.model_loader)

    def llm_providers(self) -> dict[str, Any]:
        settings = llm_settings.resolve(config_dir=self.llm_config_dir)
        statuses = cli_tools.scan(cli_tools.overrides_from_settings(settings))
        return {
            "providers": [{"name": s.provider, "available": s.status == "found", "version": s.version, "path": s.path}
                          for s in statuses],
            "api": {"configured": bool(settings.get("api_key")), "model": settings.get("model") or ""},
        }

    def active_jobs(self) -> int:
        """Занятые слоты семафора — включая REST и веб-панель, с которыми он общий.

        `asyncio.Semaphore` ёмкость не отдаёт, поэтому считаем от `max_concurrent`
        через `_value` (стабилен с 3.4); без семафора или ёмкости — только MCP-задачи.
        """
        value = getattr(self.semaphore, "_value", None)
        if self.max_concurrent is not None and isinstance(value, int):
            return max(0, self.max_concurrent - value)
        return self._active

    def status(self) -> dict[str, Any]:
        return {
            "version": __version__,
            "runtime": health_service.runtime_info(_platform, _machine),
            "asr": health_service.asr_health(self.model_loader),
            "busy": {"active": self.active_jobs(), "max": self.max_concurrent},
            "limits": {
                "max_file_mb": self.max_file_size / _MiB,
                "max_inline_mb": self.max_inline_bytes / _MiB,
                "max_concurrent": self.max_concurrent,
            },
        }


def _first_line(exc: BaseException) -> str:
    """Первая строка текста исключения: yt-dlp и CLI отдают многострочный stderr с путями сервера."""
    text = str(exc).strip()
    return text.splitlines()[0] if text else exc.__class__.__name__


def _platform() -> str:
    from platform import platform
    return platform()


def _machine() -> str:
    from platform import machine
    return machine()
