"""LLM-вкладка: обработка транскриптов провайдером, скачивание результатов, статусы CLI.

Бинари CLI-провайдеров выбирает сервер (`_server_llm_settings`), вызовы идут
под своим семафором (`_llm_answer`), результаты лежат в
`state.llm_results_dir/<job_id>` с meta.json владельца.
"""
import asyncio
import contextlib
import uuid
from datetime import datetime
from pathlib import Path
from typing import Final

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse

from src.services import cli_tools, llm_service
from src.services import llm_settings as llm_settings_service
from src.services.llm_prompts import SUMMARY_PROMPT, TASKS_PROMPT
from src.utils.atomic_json import load_json, save_json_atomic
from web import auth
from web.state import state

router = APIRouter()


LLM_EXPORT_FORMATS: Final[tuple[str, ...]] = ("txt", "md", "docx")


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


@router.get("/api/llm/tools")
async def llm_tools(fresh: bool = False, user: str = Depends(auth.require_auth)):
    """Реестр LLM-провайдеров и статусы CLI-инструментов на сервере (скан — в пуле потоков)."""
    statuses = await asyncio.to_thread(cli_tools.scan, None, fresh=fresh)
    return {
        "providers": cli_tools.canonical_provider_names(),
        "tools": [status.to_dict() for status in statuses],
        # false — пути/аргументы CLI и инструменты агента из формы сервер игнорирует
        "client_cli": state.allow_client_llm_cli,
    }


@router.get("/api/llm/prompts")
async def llm_prompts(user: str = Depends(auth.require_auth)):
    """Промпты режимов по умолчанию: форма веб-панели показывает тот же текст,
    что у PyQt, воркера и MCP (src/services/llm_prompts.py), а не свою копию."""
    return {"summary": SUMMARY_PROMPT, "tasks": TASKS_PROMPT}


@router.post("/api/llm/tools/check")
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


@router.post("/api/llm/process")
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


@router.get("/api/llm/download/{job_id}/{filename}")
async def llm_download(job_id: str, filename: str, user: str = Depends(auth.require_auth)):
    job_dir = state.llm_results_dir / job_id
    meta = load_json(str(job_dir / "meta.json"), {})
    if not job_dir.exists() or not isinstance(meta, dict) or meta.get("user") != user:
        raise HTTPException(status_code=404, detail="LLM-результат не найден")
    path = job_dir / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Файл не найден")
    return FileResponse(path=str(path), filename=path.name, media_type="application/octet-stream")
