"""MCP-бэкенд: источник файла, рабочая директория, семафор и суммаризация.

`LocalBackend` оборачивает ядро транскрибации (`transcription_api`) для MCP:
разбирает источник (`url` / `path` / `audio_base64`), кладёт файл в
`mkdtemp(prefix="mcp_", dir=upload_dir)`, крутит блокирующую часть в executor
под общим с REST/веб семафором и убирает рабочую директорию в `finally`;
`summarize` запускает LLM-провайдера с настройками сервера.

Ошибки — `BackendError(code, message, status)` с кодами REST-контракта;
MCP-сервер переводит их в текст `[code] message`.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import shutil
import tempfile
from pathlib import Path
from typing import Any

from src import __version__
from src.config import AUDIO_PREPROCESSING_MODE, HF_TOKEN, SUPPORTED_FORMATS
from src.services import cli_tools, file_policy, llm_service, llm_settings
from src.services import health as health_service
from src.services.llm_worker_service import PROMPTS

# Ядро транскрибации — transcription_api; имена остаются доступны отсюда
# (mcp_server.py, тесты и внешний код импортируют их из mcp_backend).
from src.services.transcription_api import (  # noqa: F401
    DEFAULT_MODEL,
    FORMATS,
    MODEL_ALIASES,
    BackendError,
    ProgressFn,
    TranscribeOptions,
    failure_reason,
    first_line,
    model_object,
    models_payload,
    prepare_options,
    render_result,
    resolve_model,
    run_transcription,
)

SUMMARY_MODES = tuple(PROMPTS) + ("custom",)
_MiB = 1024 * 1024


# ==================== БЭКЕНД ====================


def _is_supported(filename: str) -> bool:
    return file_policy.is_supported_media(filename)


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
        opts = prepare_options(opts, self.model_loader, hf_token=self.hf_token,
                       default_preprocessing=AUDIO_PREPROCESSING_MODE)
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
            raise BackendError("download_failed", f"Could not download '{url}': {first_line(exc)}", 502,
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
            if canonical == "Codex":
                # Codex берёт модель только из codex_model: общий `model` — модель API-провайдера
                # (так же у PyQt и TUI). Явный model из вызова — для Codex, иначе его дефолт.
                settings["codex_model"] = model or ""
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
            raise BackendError("llm_failed", f"LLM provider '{canonical}' failed: {first_line(exc)}", 502) from exc
        used_model = settings.get("codex_model") if canonical == "Codex" else settings.get("model")
        return {"mode": mode, "provider": canonical, "model": used_model or "", "answer": answer}

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


def _platform() -> str:
    from platform import platform
    return platform()


def _machine() -> str:
    from platform import machine
    return machine()
