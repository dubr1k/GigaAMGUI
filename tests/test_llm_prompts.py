"""Промпты LLM по умолчанию — один источник (src/services/llm_prompts.py).

Раньше их было четыре: воркер TUI/Liquid и MCP слали по одной строке, веб —
урезанную копию PyQt, а app.js подменял и её своей. Один и тот же «summary»
давал разный результат в зависимости от клиента.
"""
from pathlib import Path

import pytest

from src.services import llm_prompts

ROOT = Path(__file__).resolve().parent.parent


def test_worker_and_mcp_use_the_shared_prompts():
    # Веб — в test_web_app_api (GET /api/llm/prompts и запасной промпт /api/llm/process):
    # web.routes требует WEB_SECRET при импорте.
    from src.services import llm_worker_service, mcp_backend

    assert llm_worker_service.PROMPTS is llm_prompts.PROMPTS
    assert mcp_backend.PROMPTS is llm_prompts.PROMPTS
    assert mcp_backend.SUMMARY_MODES == ("summary", "tasks", "terms", "custom")


def test_pyqt_uses_the_shared_prompts():
    pytest.importorskip("PyQt6")
    from src.gui import llm_mixin

    assert llm_mixin.SUMMARY_PROMPT is llm_prompts.SUMMARY_PROMPT
    assert llm_mixin.TASKS_PROMPT is llm_prompts.TASKS_PROMPT


def test_prompt_text_lives_only_in_llm_prompts():
    """Новая копия текста где-то ещё — снова расхождение клиентов."""
    marker = "Ты аналитик встреч"
    holders = sorted(
        str(path.relative_to(ROOT))
        for folder in ("src", "web")
        for path in (ROOT / folder).rglob("*")
        if path.suffix in {".py", ".js", ".html"} and "gigaam" not in path.relative_to(ROOT).parts
        and marker in path.read_text(encoding="utf-8", errors="ignore")
    )
    assert holders == ["src/services/llm_prompts.py"]


def test_summary_and_tasks_cover_what_the_old_worker_prompts_asked_for():
    """Канон — развёрнутые промпты PyQt: они включают всё из прежних однострочных
    промптов воркера/MCP (факты, решения, риски, открытые вопросы; задачи,
    ответственные, сроки)."""
    summary = llm_prompts.SUMMARY_PROMPT.lower()
    for item in ("факты", "решения", "риски", "открытые вопросы"):
        assert item in summary
    tasks = llm_prompts.TASKS_PROMPT.lower()
    for item in ("задач", "ответственный", "срок", "открытые вопросы"):
        assert item in tasks
