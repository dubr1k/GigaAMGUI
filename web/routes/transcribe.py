"""Постановка транскрибации: загрузка файлов, ссылка на медиа и справочники формы.

/api/upload и /api/download-url разбирают одну и ту же форму
(`_parse_transcribe_form`), регистрируют задачи в `registry` и запускают
фоновые задачи из web.jobs; /api/formats, /api/device, /api/asr-options —
данные для формы.
"""
import uuid
from dataclasses import dataclass

import aiofiles
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status

from src.config import MEDIA_EXTENSIONS, OUTPUT_FORMATS
from src.core.asr.models import ASR_MODELS
from src.core.subtitles import SubtitleOptions
from src.services import file_policy, transcription_service
from src.utils.diarization import normalize_diarization_backend
from web import auth, jobs
from web.state import state
from web.task_registry import registry

router = APIRouter()


def is_supported_format(filename: str) -> bool:
    return file_policy.is_supported_by_set(filename, MEDIA_EXTENSIONS)


def safe_filename(filename: str | None) -> str:
    return file_policy.safe_filename(filename)


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


@router.get("/api/formats")
async def get_formats(user: str = Depends(auth.require_auth)):
    return {"formats": OUTPUT_FORMATS}


@router.get("/api/device")
async def get_device(user: str = Depends(auth.require_auth)):
    if state.model_loader and state.model_loader.device:
        return {"device": state.model_loader.device.upper()}
    return {"device": "CPU"}


@router.get("/api/asr-options")
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


@router.post("/api/upload")
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


@router.post("/api/download-url")
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
