"""Единый диспетчер LLM-провайдеров для GUI и Web (ранее был продублирован).

API-путь идёт через существующий LLMClient. CLI-провайдеры (Claude Code, Codex,
OpenCode, Pi, Other) запускаются через subprocess. Историческая дивергенция GUI/web
по пустому ответу Claude/Codex сохранена флагом strict_empty_cli:
  - GUI бросал ошибку на пустой ответ Claude Code/Codex  -> strict_empty_cli=True
  - web возвращал пустую строку                          -> strict_empty_cli=False
OpenCode/Pi/Other всегда строги к пустому ответу (обе поверхности совпадали).
Нормализацию имени провайдера ("Другое"->"Other") и текст ошибки неизвестного
провайдера формирует вызывающая поверхность (адаптер).
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
import time
from pathlib import Path

from src.utils.llm_client import LLMClient, LLMSettings

_TIMEOUT = 600


class UnknownLLMProvider(Exception):
    """Провайдер не распознан диспетчером; текст сообщения формирует адаптер."""

    def __init__(self, provider: str):
        super().__init__(provider)
        self.provider = provider


class EmptyLLMResponse(RuntimeError):
    """CLI-инструмент вернул пустой ответ (при включённой строгой проверке)."""

    def __init__(self, tool: str):
        super().__init__(f"{tool} вернул пустой ответ")
        self.tool = tool


class LLMCancelled(RuntimeError):
    """Пользователь отменил текущий запрос LLM."""


def _run_command(command: list[str], *, input_text: str | None = None, cancel_check=None):
    if cancel_check is None:
        return subprocess.run(command, input=input_text, capture_output=True, text=True, timeout=_TIMEOUT)
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE if input_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    first_input = input_text
    while True:
        if cancel_check():
            process.terminate()
            try:
                process.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
            raise LLMCancelled()
        try:
            stdout, stderr = process.communicate(input=first_input, timeout=0.1)
            return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            first_input = None
            time.sleep(0.01)


def build_prompt_text(transcript_text: str, prompt: str) -> str:
    return (
        "Ты обрабатываешь транскрипт на русском языке. "
        "Не выдумывай факты, явно помечай неясности.\n\n"
        f"Инструкция:\n{prompt.strip()}\n\n"
        f"Транскрипт:\n{transcript_text.strip()}\n"
    )


def _run_api(
    settings: dict,
    transcript_text: str,
    prompt: str,
    on_stream_chunk=None,
    cancel_check=None,
) -> str:
    client = LLMClient(LLMSettings(
        api_url=settings["api_url"],
        api_key=settings["api_key"],
        model=settings["model"],
        temperature=settings["temperature"],
    ))
    kwargs = {"stream_callback": on_stream_chunk}
    if cancel_check is not None:
        kwargs["cancel_check"] = cancel_check
    return client.process_transcript(transcript_text, prompt, **kwargs)


def _run_claude(settings: dict, prompt_text: str, strict_empty: bool, cancel_check=None) -> str:
    command = [settings["claude_path"], "-p", "--output-format", "text"]
    if settings.get("model"):
        command += ["--model", settings["model"]]
    if settings.get("claude_args"):
        command += shlex.split(settings["claude_args"])
    command.append(prompt_text)
    result = _run_command(command, cancel_check=cancel_check)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Claude Code завершился с ошибкой").strip())
    answer = (result.stdout or "").strip()
    if not answer and strict_empty:
        raise EmptyLLMResponse("Claude Code")
    return answer


def _run_codex(settings: dict, prompt_text: str, strict_empty: bool, cancel_check=None) -> str:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp:
        output_path = tmp.name
    try:
        command = [settings["codex_path"], "exec", "--json", "-o", output_path]
        if settings.get("codex_model"):
            command += ["-m", settings["codex_model"]]
        if settings.get("codex_args"):
            command += shlex.split(settings["codex_args"])
        command.append("-")
        result = _run_command(command, input_text=prompt_text, cancel_check=cancel_check)
        if result.returncode != 0:
            diagnostic = " ".join((result.stderr or result.stdout or "").split())
            detail = f": {diagnostic[:300]}" if diagnostic else ""
            raise RuntimeError(
                f"Codex failed (exit {result.returncode}){detail}. "
                "Run 'codex login' and check Codex settings."
            )
        answer = _codex_agent_message(result.stdout)
        if not answer:
            answer = Path(output_path).read_text(encoding="utf-8").strip()
        if not answer and strict_empty:
            raise EmptyLLMResponse("Codex")
        return answer
    finally:
        try:
            os.remove(output_path)
        except OSError:
            pass


def _codex_agent_message(output: str | None) -> str:
    for line in (output or "").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item", {})
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
    return ""


def _run_generic(command: list[str], error_name: str, cancel_check=None) -> str:
    result = _run_command(command, cancel_check=cancel_check)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or f"{error_name} завершился с ошибкой").strip())
    answer = (result.stdout or "").strip()
    if not answer:
        raise EmptyLLMResponse(error_name)
    return answer


def _opencode_command(settings: dict, prompt_text: str) -> list[str]:
    command = [settings["opencode_path"]]
    if settings.get("model"):
        command += ["--model", settings["model"]]
    if settings.get("opencode_args"):
        command += shlex.split(settings["opencode_args"])
    command.append(prompt_text)
    return command


def _pi_command(settings: dict, prompt_text: str) -> list[str]:
    command = [settings["pi_path"], "-p", "--mode", "text"]
    if settings.get("pi_provider"):
        command += ["--provider", settings["pi_provider"]]
    if settings.get("model"):
        command += ["--model", settings["model"]]
    if settings.get("pi_args"):
        command += shlex.split(settings["pi_args"])
    command.append(prompt_text)
    return command


def _other_command(settings: dict, prompt_text: str) -> list[str]:
    command = [settings["other_path"]]
    if settings.get("other_args"):
        command += shlex.split(settings["other_args"])
    command.append(prompt_text)
    return command


def run_provider(
    llm_settings: dict,
    transcript_text: str,
    prompt: str,
    *,
    provider: str,
    strict_empty_cli: bool,
    on_stream_chunk=None,
    cancel_check=None,
) -> str:
    """Запускает LLM-провайдера. `provider` — уже нормализованное каноническое имя."""
    if provider == "API":
        return _run_api(llm_settings, transcript_text, prompt, on_stream_chunk, cancel_check)
    prompt_text = build_prompt_text(transcript_text, prompt)
    if provider == "Claude Code":
        return _run_claude(llm_settings, prompt_text, strict_empty_cli, cancel_check)
    if provider == "Codex":
        return _run_codex(llm_settings, prompt_text, strict_empty_cli, cancel_check)
    if provider == "OpenCode":
        return _run_generic(_opencode_command(llm_settings, prompt_text), "OpenCode", cancel_check)
    if provider == "Pi":
        return _run_generic(_pi_command(llm_settings, prompt_text), "Pi", cancel_check)
    if provider == "Other":
        return _run_generic(_other_command(llm_settings, prompt_text), "Внешний CLI", cancel_check)
    raise UnknownLLMProvider(provider)
