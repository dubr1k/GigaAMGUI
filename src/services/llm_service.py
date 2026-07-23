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

import os
import shlex
import subprocess
import tempfile
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
) -> str:
    client = LLMClient(LLMSettings(
        api_url=settings["api_url"],
        api_key=settings["api_key"],
        model=settings["model"],
        temperature=settings["temperature"],
    ))
    return client.process_transcript(transcript_text, prompt, stream_callback=on_stream_chunk)


def _run_claude(settings: dict, prompt_text: str, strict_empty: bool) -> str:
    command = [settings["claude_path"], "-p", "--output-format", "text"]
    if settings.get("model"):
        command += ["--model", settings["model"]]
    if settings.get("claude_args"):
        command += shlex.split(settings["claude_args"])
    command.append(prompt_text)
    result = subprocess.run(command, capture_output=True, text=True, timeout=_TIMEOUT)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Claude Code завершился с ошибкой").strip())
    answer = (result.stdout or "").strip()
    if not answer and strict_empty:
        raise EmptyLLMResponse("Claude Code")
    return answer


def _run_codex(settings: dict, prompt_text: str, strict_empty: bool) -> str:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp:
        output_path = tmp.name
    try:
        command = [settings["codex_path"], "exec", "-o", output_path]
        if settings.get("model"):
            command += ["-m", settings["model"]]
        if settings.get("codex_args"):
            command += shlex.split(settings["codex_args"])
        command.append("-")
        result = subprocess.run(
            command, input=prompt_text, capture_output=True, text=True, timeout=_TIMEOUT,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "Codex завершился с ошибкой").strip())
        answer = Path(output_path).read_text(encoding="utf-8").strip()
        if not answer and strict_empty:
            raise EmptyLLMResponse("Codex")
        return answer
    finally:
        try:
            os.remove(output_path)
        except OSError:
            pass


def _run_generic(command: list[str], error_name: str) -> str:
    result = subprocess.run(command, capture_output=True, text=True, timeout=_TIMEOUT)
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
) -> str:
    """Запускает LLM-провайдера. `provider` — уже нормализованное каноническое имя."""
    if provider == "API":
        return _run_api(llm_settings, transcript_text, prompt, on_stream_chunk)
    prompt_text = build_prompt_text(transcript_text, prompt)
    if provider == "Claude Code":
        return _run_claude(llm_settings, prompt_text, strict_empty_cli)
    if provider == "Codex":
        return _run_codex(llm_settings, prompt_text, strict_empty_cli)
    if provider == "OpenCode":
        return _run_generic(_opencode_command(llm_settings, prompt_text), "OpenCode")
    if provider == "Pi":
        return _run_generic(_pi_command(llm_settings, prompt_text), "Pi")
    if provider == "Other":
        return _run_generic(_other_command(llm_settings, prompt_text), "Внешний CLI")
    raise UnknownLLMProvider(provider)
