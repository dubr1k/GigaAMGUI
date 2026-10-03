"""Транскрибация для REST API (`api.py`) и MCP: параметры, модели, запуск, ответ.

`prepare_options` проверяет и нормализует параметры запроса (порядок ошибок —
как у REST), `run_transcription` — блокирующее ядро: загрузчик под запрос
(если просят другой backend/модель/провайдер) берётся и выгружается здесь,
процессор запускается с `output_formats=[]`, прогресс процессора приводится к
`progress(stage, fraction)`. `render_result` собирает ответ MCP; реестр
моделей и алиасов OpenAI (`whisper-1` …) — тоже здесь.

Ошибки — `BackendError(code, message, status)` с кодами REST-контракта;
`api.py` переводит их в конверт OpenAI, MCP-сервер — в текст `[code] message`.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.config import AUDIO_PREPROCESSING_MODE, HF_TOKEN
from src.core.asr.models import ASR_MODELS
from src.core.progress import coerce_progress
from src.services import transcript_formats, transcription_service
from src.utils.audio_preprocessing import normalize_preprocessing_mode
from src.utils.diarization import normalize_diarization_backend

ProgressFn = Callable[[str, "float | None"], None]

FORMATS = ("text", "json", "verbose", "diarized", "srt", "vtt")


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


def prepare_options(opts: TranscribeOptions, model_loader, *, hf_token: str | None = HF_TOKEN,
                    default_preprocessing: str | None = None) -> TranscribeOptions:
    """Проверяет и нормализует параметры (порядок ошибок — как у REST).

    Возвращает копию с уже включённой диаризацией для `diarized`, каноническим
    именем backend-а диаризации и подставленным режимом предобработки
    (`default_preprocessing`, иначе AUDIO_PREPROCESSING_MODE из конфига).
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
        preprocessing = normalize_preprocessing_mode(
            opts.audio_preprocessing or default_preprocessing or AUDIO_PREPROCESSING_MODE)
    except ValueError as exc:
        raise BackendError("unsupported_parameter",
                           f"Unknown audio_preprocessing '{opts.audio_preprocessing}'. Use off, auto, light or denoise.",
                           param="audio_preprocessing") from exc
    return dataclasses.replace(opts, model=selection.model, asr_backend=selection.backend,
                               onnx_provider=selection.onnx_provider, diarize=diarize,
                               diarization_backend=diarization_backend, audio_preprocessing=preprocessing)


# ==================== ЯДРО ====================


def _adapt_progress(progress: ProgressFn | None):
    """Колбэк для процессора: он шлёт `ProgressEvent` одним аргументом либо (stage, value) — legacy.

    Клиенту уходит id стадии (в MCP — текст уведомления) и доля файла 0..1 или None.
    """
    if progress is None:
        return None

    def callback(event_or_stage, value=None, **_):
        snapshot = coerce_progress(event_or_stage, value)
        progress(snapshot.stage or "processing", snapshot.file_progress)

    return callback


def failure_reason(result: dict[str, Any]) -> str | None:
    """Первая строка `result["error"]` процессора или None, если причины нет."""
    error = result.get("error")
    if not isinstance(error, str) or not error.strip():
        return None
    return error.strip().splitlines()[0]


def run_transcription(file_path: Path, work_dir: Path, opts: TranscribeOptions, *, model_loader, stats_manager,
                      loader_factory, logger, progress: ProgressFn | None) -> dict[str, Any]:
    """Блокирующая транскрибация `file_path`; `opts` — только из `prepare_options`
    (там уже проверен и нормализован ASR-выбор: backend/model/onnx_provider заполнены).

    Берёт загрузчик под запрос, если серверная модель не загружена или
    backend/модель/провайдер отличаются, и выгружает его в `finally`;
    результат процессора возвращается как есть (`utterances`, `media_duration`, `diarization`).
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
            # Процессор кладёт понятную пользователю причину в result["error"]; без неё — общий текст
            reason = failure_reason(result)
            message = f"Transcription failed: {reason}" if reason else \
                "Transcription failed on the server. See the server log."
            raise BackendError("processing_failed", message, 500)
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


def first_line(exc: BaseException) -> str:
    """Первая строка текста исключения: yt-dlp и CLI отдают многострочный stderr с путями сервера."""
    text = str(exc).strip()
    return text.splitlines()[0] if text else exc.__class__.__name__
