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


def test_api_provider_forwards_stream_callback(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, settings):
            captured["settings"] = settings

        def process_transcript(self, text, prompt, stream_callback=None):
            stream_callback("часть")
            return "ответ"

    monkeypatch.setattr(llm_service, "LLMClient", FakeClient)
    chunks = []

    result = llm_service.run_provider(
        {"api_url": "https://example.test", "api_key": "key", "model": "model", "temperature": 0.2},
        "текст", "промпт", provider="API", strict_empty_cli=True, on_stream_chunk=chunks.append,
    )

    assert result == "ответ"
    assert chunks == ["часть"]


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


def test_codex_uses_json_output_without_a_shared_model_and_reads_agent_message(monkeypatch):
    captured = {}

    def fake_run(command, *args, **kwargs):
        captured["command"] = command
        output_path = command[command.index("-o") + 1]
        with open(output_path, "w", encoding="utf-8") as output:
            output.write("fallback answer")
        return _Proc(
            0,
            '{"type":"item.completed","item":{"type":"agent_message","text":"final answer"}}\n',
            "Codex startup progress",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = llm_service.run_provider(
        {"codex_path": "codex", "model": "shared-api-model"},
        "T", "P", provider="Codex", strict_empty_cli=True,
    )

    assert result == "final answer"
    assert captured["command"][0].split("/")[-1] == "codex"  # может быть резолвлен в абсолютный путь
    assert captured["command"][1:4] == ["exec", "--json", "-o"]
    assert "-m" not in captured["command"]
    assert captured["command"][-1] == "-"


def test_codex_uses_output_file_when_json_has_no_agent_message(monkeypatch):
    def fake_run(command, *args, **kwargs):
        output_path = command[command.index("-o") + 1]
        with open(output_path, "w", encoding="utf-8") as output:
            output.write("  file answer  ")
        return _Proc(0, '{"type":"thread.started"}\n', "progress banner")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = llm_service.run_provider(
        {"codex_path": "codex"}, "T", "P", provider="Codex", strict_empty_cli=True,
    )

    assert result == "file answer"


def test_codex_failure_surfaces_concise_actionable_diagnostics(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: _Proc(2, "", "authentication failed"))

    with pytest.raises(RuntimeError, match=r"Codex failed \(exit 2\).*codex login"):
        llm_service.run_provider(
            {"codex_path": "codex"}, "T", "P", provider="Codex", strict_empty_cli=True,
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
    assert captured["cmd"][:2] == ["oc", "run"]
    assert "-m" in captured["cmd"] and "m" in captured["cmd"]
    assert "--flag" in captured["cmd"] and "x" in captured["cmd"]
    # промпт уходит в stdin, а не в argv
    assert llm_service.build_prompt_text("T", "P") not in captured["cmd"]


# --- 2.2: реестр, stdin, safe-флаги, oh-my-pi --------------------------------

def _capture_run(monkeypatch, stdout="ok"):
    captured = {}
    # На машине разработчика бинари реально стоят — не резолвим, чтобы cmd[0] был предсказуем.
    monkeypatch.setattr(llm_service.cli_tools, "search_dirs", lambda: [])

    def fake_run(command, *a, **k):
        captured["cmd"] = command
        captured["kwargs"] = k
        return _Proc(0, stdout, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return captured


def test_claude_prompt_goes_to_stdin_with_safe_flags(monkeypatch):
    captured = _capture_run(monkeypatch)
    llm_service.run_provider(
        {"claude_path": "claude", "model": "sonnet"}, "T", "P", provider="Claude Code", strict_empty_cli=True,
    )
    cmd = captured["cmd"]
    assert cmd[:4] == ["claude", "-p", "--output-format", "text"]
    assert "--model" in cmd and "sonnet" in cmd
    assert "--no-session-persistence" in cmd
    assert cmd[cmd.index("--tools") + 1] == ""
    assert captured["kwargs"]["input"] == llm_service.build_prompt_text("T", "P")
    assert llm_service.build_prompt_text("T", "P") not in cmd


def test_allow_tools_drops_safe_flags(monkeypatch):
    captured = _capture_run(monkeypatch)
    llm_service.run_provider(
        {"claude_path": "claude", "llm_allow_tools": True}, "T", "P", provider="Claude Code", strict_empty_cli=True,
    )
    assert "--tools" not in captured["cmd"]
    assert "--no-session-persistence" not in captured["cmd"]


def test_omp_command_shape(monkeypatch):
    captured = _capture_run(monkeypatch)
    llm_service.run_provider(
        {"omp_path": "omp", "omp_provider": "anthropic", "model": "opus", "omp_args": "--thinking low"},
        "T", "P", provider="oh-my-pi", strict_empty_cli=True,
    )
    cmd = captured["cmd"]
    assert cmd[:4] == ["omp", "-p", "--mode", "text"]
    assert cmd[cmd.index("--provider") + 1] == "anthropic"
    assert cmd[cmd.index("--model") + 1] == "opus"
    assert "--no-tools" in cmd and "--no-session" in cmd
    assert cmd[-2:] == ["--thinking", "low"]
    assert captured["kwargs"]["input"] == llm_service.build_prompt_text("T", "P")


def test_pi_command_uses_stdin_and_safe_flags(monkeypatch):
    captured = _capture_run(monkeypatch)
    llm_service.run_provider({"pi_path": "pi"}, "T", "P", provider="Pi", strict_empty_cli=True)
    cmd = captured["cmd"]
    assert cmd[:4] == ["pi", "-p", "--mode", "text"]
    assert "--no-tools" in cmd and "--no-session" in cmd
    assert captured["kwargs"]["input"]


def test_opencode_run_pure_by_default(monkeypatch):
    captured = _capture_run(monkeypatch)
    llm_service.run_provider({"opencode_path": "opencode"}, "T", "P", provider="OpenCode", strict_empty_cli=True)
    assert captured["cmd"][:3] == ["opencode", "run", "--pure"]

    llm_service.run_provider(
        {"opencode_path": "opencode", "llm_allow_tools": True}, "T", "P", provider="OpenCode", strict_empty_cli=True,
    )
    assert "--pure" not in captured["cmd"]


def test_other_keeps_prompt_as_last_argument_and_feeds_stdin(monkeypatch):
    captured = _capture_run(monkeypatch)
    llm_service.run_provider(
        {"other_path": "my-llm", "other_args": "--x 1"}, "T", "P", provider="Other", strict_empty_cli=True,
    )
    prompt = llm_service.build_prompt_text("T", "P")
    assert captured["cmd"] == ["my-llm", "--x", "1", prompt]
    assert captured["kwargs"]["input"] == prompt


def test_other_stdin_marker_removes_prompt_from_argv(monkeypatch):
    captured = _capture_run(monkeypatch)
    llm_service.run_provider(
        {"other_path": "my-llm", "other_args": "--x {stdin} --y"}, "T", "P", provider="Other", strict_empty_cli=True,
    )
    assert captured["cmd"] == ["my-llm", "--x", "--y"]
    assert captured["kwargs"]["input"] == llm_service.build_prompt_text("T", "P")


def test_cli_child_gets_extended_path(monkeypatch):
    captured = _capture_run(monkeypatch)
    monkeypatch.setattr(llm_service.cli_tools, "search_dirs", lambda: ["/only/this"])
    llm_service.run_provider({"claude_path": "claude"}, "T", "P", provider="Claude Code", strict_empty_cli=True)
    assert captured["kwargs"]["env"]["PATH"] == "/only/this"


def test_cli_binary_resolved_to_absolute_path(tmp_path, monkeypatch):
    exe = tmp_path / "omp"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    captured = _capture_run(monkeypatch)
    monkeypatch.setattr(llm_service.cli_tools, "search_dirs", lambda: [str(tmp_path)])
    llm_service.run_provider({"omp_path": "omp"}, "T", "P", provider="oh-my-pi", strict_empty_cli=True)
    assert captured["cmd"][0] == str(exe)


def test_missing_binary_raises_friendly_error_with_install_hint(monkeypatch):
    monkeypatch.setattr(llm_service.cli_tools, "search_dirs", lambda: [])

    def not_found(*a, **k):
        raise FileNotFoundError(2, "No such file", "omp")

    monkeypatch.setattr(subprocess, "run", not_found)
    with pytest.raises(RuntimeError) as exc:
        llm_service.run_provider({"omp_path": "omp"}, "T", "P", provider="oh-my-pi", strict_empty_cli=True)
    message = str(exc.value)
    assert "oh-my-pi" in message and "omp" in message
    assert "brew install" in message


def test_cancelled_cli_provider_terminates_its_subprocess():
    import sys
    with pytest.raises(llm_service.LLMCancelled):
        llm_service._run_command(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            input_text="prompt", cancel_check=lambda: True,
        )
