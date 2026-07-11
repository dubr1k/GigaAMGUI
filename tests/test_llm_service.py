"""Характеризующие тесты llm_service — фиксируют диспетч и дивергенции GUI/web 1:1."""
import subprocess

import pytest

from src.services import llm_service


class _Proc:
    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_build_prompt_text_shape():
    text = llm_service.build_prompt_text("  привет  ", "  сделай саммари  ")
    assert text.startswith("Ты обрабатываешь транскрипт на русском языке. Не выдумывай факты")
    assert "Инструкция:\nсделай саммари" in text
    assert "Транскрипт:\nпривет" in text
    assert text.endswith("\n")


def test_build_prompt_text_matches_legacy_literal():
    # эталон, ранее продублированный в app_qt.py и web_app.py
    expected = (
        "Ты обрабатываешь транскрипт на русском языке. "
        "Не выдумывай факты, явно помечай неясности.\n\n"
        "Инструкция:\nP\n\n"
        "Транскрипт:\nT\n"
    )
    assert llm_service.build_prompt_text("T", "P") == expected


def test_run_provider_unknown_raises():
    with pytest.raises(llm_service.UnknownLLMProvider) as exc:
        llm_service.run_provider({}, "t", "p", provider="Nope", strict_empty_cli=True)
    assert exc.value.provider == "Nope"


def test_claude_empty_strict_raises(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(0, "", ""))
    with pytest.raises(llm_service.EmptyLLMResponse):
        llm_service.run_provider(
            {"claude_path": "claude"}, "t", "p", provider="Claude Code", strict_empty_cli=True,
        )


def test_claude_empty_nonstrict_returns_empty(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(0, "", ""))
    result = llm_service.run_provider(
        {"claude_path": "claude"}, "t", "p", provider="Claude Code", strict_empty_cli=False,
    )
    assert result == ""


def test_claude_nonempty_returns_stripped(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(0, "  ответ  ", ""))
    result = llm_service.run_provider(
        {"claude_path": "claude"}, "t", "p", provider="Claude Code", strict_empty_cli=True,
    )
    assert result == "ответ"


def test_claude_error_returncode_raises(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(1, "", "boom"))
    with pytest.raises(RuntimeError, match="boom"):
        llm_service.run_provider(
            {"claude_path": "claude"}, "t", "p", provider="Claude Code", strict_empty_cli=True,
        )


def test_opencode_empty_always_strict(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(0, "", ""))
    with pytest.raises(llm_service.EmptyLLMResponse):
        llm_service.run_provider(
            {"opencode_path": "opencode"}, "t", "p", provider="OpenCode", strict_empty_cli=False,
        )


def test_opencode_command_shape(monkeypatch):
    captured = {}

    def fake_run(command, *a, **k):
        captured["cmd"] = command
        return _Proc(0, "ok", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    llm_service.run_provider(
        {"opencode_path": "oc", "model": "m", "opencode_args": "--flag x"},
        "T", "P", provider="OpenCode", strict_empty_cli=True,
    )
    assert captured["cmd"][0] == "oc"
    assert "--model" in captured["cmd"] and "m" in captured["cmd"]
    assert "--flag" in captured["cmd"] and "x" in captured["cmd"]
    # последний аргумент — собранный prompt
    assert captured["cmd"][-1] == llm_service.build_prompt_text("T", "P")
