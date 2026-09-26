"""Единый диспетчер LLM-провайдеров для GUI и Web (ранее был продублирован).

API-путь идёт через существующий LLMClient. CLI-провайдеры (Claude Code, Codex,
OpenCode, Pi, oh-my-pi, Other) запускаются через subprocess; список и бинари —
из реестра `cli_tools`. Историческая дивергенция GUI/web по пустому ответу
Claude/Codex сохранена флагом strict_empty_cli:
  - GUI бросал ошибку на пустой ответ Claude Code/Codex  -> strict_empty_cli=True
  - web возвращал пустую строку                          -> strict_empty_cli=False
OpenCode/Pi/oh-my-pi/Other всегда строги к пустому ответу (обе поверхности совпадали).
Нормализацию имени провайдера ("Другое"->"Other") и текст ошибки неизвестного
провайдера формирует вызывающая поверхность (адаптер).

Промпт передаётся CLI через stdin: транскрипт может быть длиннее лимита одного
argv-аргумента (Linux MAX_ARG_STRLEN = 128 KiB). Агентные CLI по умолчанию
запускаются без инструментов и без сохранения сессии (`llm_allow_tools=False`):
выжимка транскрипта — не задача coding-агенту, и она не должна засорять его
историю. Бинарь резолвится по расширенному PATH (`cli_tools.locate_tool`), а тот
же PATH передаётся дочернему процессу — npm-шимы claude/opencode ищут `node`.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import psutil

from src.services import cli_tools
from src.utils.llm_client import LLMClient, LLMSettings

_TIMEOUT = 600
STDIN_MARKER = "{stdin}"  # в other_args: «промпт только в stdin, не в argv»


class CLIToolNotFound(RuntimeError):
    """Бинарь CLI-провайдера не найден/не запускается; текст уже человекочитаемый."""

    def __init__(self, spec: cli_tools.ProviderSpec, requested: str):
        hint = f" Установка: {spec.install_hint}" if spec.install_hint else ""
        super().__init__(
            f"{spec.name}: не найдена команда «{requested}». Проверьте путь в настройках LLM.{hint}"
        )
        self.provider = spec.name


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


class LLMTerminationError(RuntimeError):
    """Остановка CLI не подтверждена; нельзя объявлять успешную отмену."""


def _remember_children(process, children):
    # Process хранит время создания: переиспользованный PID не станет целью сигнала.
    for parent in [process, *children]:
        try:
            children.update(parent.children(recursive=True))
        except psutil.NoSuchProcess:
            pass


def _stop_command_tree(popen, process, children):
    """Останавливаем только сохранённые процессы этого вызова, не группу worker.

    `process` — psutil-представление того же CLI (дерево, сигналы), `popen` —
    владелец его каналов: только он дочитывает вывод и освобождает трубы.
    """
    _remember_children(process, children)
    targets = [*children, process]
    errors = []
    for target in targets:
        try:
            target.terminate()
        except psutil.NoSuchProcess:
            pass
        except psutil.Error as error:
            errors.append(str(error))
    _, alive = psutil.wait_procs(targets, timeout=0.5)
    for target in alive:
        try:
            target.kill()
        except psutil.NoSuchProcess:
            pass
        except psutil.Error as error:
            errors.append(str(error))
    _, alive = psutil.wait_procs(alive, timeout=0.5)
    for target in alive:
        try:
            if target.is_running() and target.status() != psutil.STATUS_ZOMBIE:
                errors.append(f"CLI process {target.pid} is still running")
        except psutil.NoSuchProcess:
            pass
    try:
        popen.communicate(timeout=0.5)
    except subprocess.TimeoutExpired:
        errors.append("CLI output pipes did not close after cancellation")
    if errors:
        raise LLMTerminationError("; ".join(errors))


def _feed_stdin(stream, text: str) -> None:
    """Пишет промпт в CLI и закрывает stdin — EOF для `claude -p` и других.

    Не через communicate(input=...) с таймаутом: после TimeoutExpired повтор
    вызова без input в CPython уже не дописывает остаток и не закрывает stdin,
    и CLI ждал EOF вечно на любом промпте больше буфера канала (~64 КБ).
    """
    try:
        stream.write(text)
    except (BrokenPipeError, OSError, ValueError):
        pass  # CLI завершился или остановлен, не дочитав; итог даст его код выхода
    finally:
        try:
            stream.close()
        except (BrokenPipeError, OSError, ValueError):
            pass


def _run_command(command: list[str], *, input_text: str | None = None, cancel_check=None):
    env = cli_tools.child_environment()
    if cancel_check is None:
        # Без input_text stdin ребёнка — /dev/null, а не наш: унаследованная труба
        # JSONL-воркера превращалась в неблокирующую (см. cli_tools.resolve_tool).
        if input_text is None:
            return subprocess.run(
                command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=_TIMEOUT, env=env,
            )
        return subprocess.run(
            command, input=input_text, capture_output=True, text=True, timeout=_TIMEOUT, env=env,
        )
    # Не psutil.Popen: он лишь проксирует атрибуты во вложенный subprocess.Popen,
    # и отданный потоку записи stdin остался бы у того, кто вызывает communicate().
    popen = subprocess.Popen(
        command,
        stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    # Дочерний процесс не будет пожат до communicate(), так что PID ещё его.
    process = psutil.Process(popen.pid)
    writer = None
    if input_text is not None:
        # stdin отдан потоку записи: communicate() его не трогает и только читает
        # вывод, поэтому опрос отмены не ждёт, пока CLI прочитает промпт.
        stdin, popen.stdin = popen.stdin, None
        writer = threading.Thread(target=_feed_stdin, args=(stdin, input_text), daemon=True)
        writer.start()
    children = set()
    while True:
        _remember_children(process, children)
        if cancel_check():
            # Остановка закрывает канал со стороны CLI, и запись в потоке завершится.
            _stop_command_tree(popen, process, children)
            raise LLMCancelled()
        try:
            stdout, stderr = popen.communicate(timeout=0.1)
            if writer is not None:
                writer.join(timeout=1)
            return subprocess.CompletedProcess(command, popen.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
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


def _tool_binary(settings: dict, spec: cli_tools.ProviderSpec) -> str:
    """Абсолютный путь по расширенному PATH; если не нашли — то, что ввёл пользователь.

    Запуск «как есть» оставляет последнее слово ОС (PATHEXT, шимы), а
    FileNotFoundError ниже превращается в понятную ошибку с подсказкой установки.
    """
    requested = (settings.get(f"{spec.settings_prefix}_path") or "").strip() or spec.binary or ""
    return cli_tools.locate_tool(spec, requested) or requested


def _run_tool(spec: cli_tools.ProviderSpec, command: list[str], *, input_text=None, cancel_check=None):
    try:
        return _run_command(command, input_text=input_text, cancel_check=cancel_check)
    except FileNotFoundError as exc:
        raise CLIToolNotFound(spec, command[0]) from exc


def _allow_tools(settings: dict) -> bool:
    return bool(settings.get("llm_allow_tools", False))


def _extra_args(settings: dict, spec: cli_tools.ProviderSpec) -> list[str]:
    raw = settings.get(f"{spec.settings_prefix}_args") or ""
    return shlex.split(raw) if raw.strip() else []


def _run_claude(settings: dict, prompt_text: str, strict_empty: bool, cancel_check=None) -> str:
    spec = cli_tools.provider_by_name("Claude Code")
    command = [_tool_binary(settings, spec), "-p", "--output-format", "text"]
    if settings.get("model"):
        command += ["--model", settings["model"]]
    if not _allow_tools(settings):
        command += ["--tools", "", "--no-session-persistence"]
    command += _extra_args(settings, spec)
    result = _run_tool(spec, command, input_text=prompt_text, cancel_check=cancel_check)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Claude Code завершился с ошибкой").strip())
    answer = (result.stdout or "").strip()
    if not answer and strict_empty:
        raise EmptyLLMResponse("Claude Code")
    return answer


def _run_codex(settings: dict, prompt_text: str, strict_empty: bool, cancel_check=None) -> str:
    spec = cli_tools.provider_by_name("Codex")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp:
        output_path = tmp.name
    try:
        command = [_tool_binary(settings, spec), "exec", "--json", "-o", output_path]
        if settings.get("codex_model"):
            command += ["-m", settings["codex_model"]]
        command += _extra_args(settings, spec)
        command.append("-")
        result = _run_tool(spec, command, input_text=prompt_text, cancel_check=cancel_check)
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


def _run_generic(spec, command: list[str], error_name: str, *, input_text=None, cancel_check=None) -> str:
    result = _run_tool(spec, command, input_text=input_text, cancel_check=cancel_check)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or f"{error_name} завершился с ошибкой").strip())
    answer = (result.stdout or "").strip()
    if not answer:
        raise EmptyLLMResponse(error_name)
    return answer


def _opencode_command(settings: dict) -> list[str]:
    spec = cli_tools.provider_by_name("OpenCode")
    command = [_tool_binary(settings, spec), "run"]
    if not _allow_tools(settings):
        command.append("--pure")
    if settings.get("model"):
        command += ["-m", settings["model"]]
    command += _extra_args(settings, spec)
    return command


def _pi_like_command(settings: dict, spec: cli_tools.ProviderSpec) -> list[str]:
    """pi и oh-my-pi совместимы по флагам печатного режима."""
    command = [_tool_binary(settings, spec), "-p", "--mode", "text"]
    if settings.get(f"{spec.settings_prefix}_provider"):
        command += ["--provider", settings[f"{spec.settings_prefix}_provider"]]
    if settings.get("model"):
        command += ["--model", settings["model"]]
    if not _allow_tools(settings):
        command += ["--no-tools", "--no-session"]
    command += _extra_args(settings, spec)
    return command


def _other_command(settings: dict, prompt_text: str) -> list[str]:
    """Обратная совместимость: промпт остаётся последним аргументом, если в
    аргументах нет маркера {stdin}; при маркере — только stdin."""
    spec = cli_tools.provider_by_name("Other")
    command = [settings["other_path"]]
    args = _extra_args(settings, spec)
    if STDIN_MARKER in args:
        command += [arg for arg in args if arg != STDIN_MARKER]
    else:
        command += args
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
        spec = cli_tools.provider_by_name(provider)
        return _run_generic(spec, _opencode_command(llm_settings), "OpenCode",
                            input_text=prompt_text, cancel_check=cancel_check)
    if provider in ("Pi", "oh-my-pi"):
        spec = cli_tools.provider_by_name(provider)
        return _run_generic(spec, _pi_like_command(llm_settings, spec), spec.name,
                            input_text=prompt_text, cancel_check=cancel_check)
    if provider == "Other":
        spec = cli_tools.provider_by_name(provider)
        return _run_generic(spec, _other_command(llm_settings, prompt_text), "Внешний CLI",
                            input_text=prompt_text, cancel_check=cancel_check)
    raise UnknownLLMProvider(provider)
