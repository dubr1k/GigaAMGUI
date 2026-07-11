# Фаза 1: Слой сервисов без дублей — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Создать слой `src/services/`, поглощающий продублированную service-логику (валидация файлов, health, LLM-диспетчер, task-store, обвязка транскрибации); монолиты `api.py`/`web_app.py`/`app_qt.py` начинают делегировать в сервисы. Поведение поверхностей строго 1:1.

**Architecture:** Каждый сервис — чистый модуль в `src/services/`, зависящий только вниз (`src/core`, `src/utils`). Для каждого: сначала характеризующий тест на текущее поведение → вынос кода в сервис → замена дублей в поверхностях на вызов сервиса (старые методы становятся тонкими обёртками-делегатами) → тест зелёный. Обнаруженные дивергенции между поверхностями сохраняются флагами, не «чинятся».

**Tech Stack:** Python 3.10+, pytest, ruff. FastAPI (api/web), PyQt6 (gui) — не модифицируем контракты, только источник логики.

## Global Constraints

- Поведение GUI / CLI / API / Web — **строго 1:1**. Каждый вынос сопровождается характеризующим тестом на текущее поведение.
- Тесты импортируют как `from src.services.<mod> import <name>` (соответствует конвенции `from src...` в существующих тестах; `testpaths=["tests"]`, conftest отсутствует).
- Ни одна фаза не завершается, пока `ruff check .` и `pytest -q` не зелёные (не хуже baseline Фазы 0).
- Дивергенции (см. roadmap): `is_supported_format` источник расширений; LLM `strict_empty`; нормализация имени провайдера; текст ошибки неизвестного провайдера. Сохранять, а не унифицировать.
- Ветка: `refactor/monolith-decomposition`. Коммит-сообщения без co-author/attribution трейлеров.

## File Structure

```
src/services/
  __init__.py            # пустой маркер пакета
  file_policy.py         # is_supported_format(*, extensions/source), safe_filename
  health.py              # asr_health(model_loader), runtime_info
  llm_service.py         # build_prompt_text, run_provider, UnknownLLMProvider, EmptyLLMResponse
  task_store.py          # TaskStore: register/get/persist/restore (Task-схема + web-расширение)
  transcription_service.py  # transcribe_file(...) — единая обвязка ModelLoader+Processor+stats
tests/
  test_file_policy.py
  test_health_service.py
  test_llm_service.py
  test_task_store.py
  test_transcription_service.py
```

---

### Task 1: Пакет `src/services/`

**Files:**
- Create: `src/services/__init__.py`

**Interfaces:**
- Produces: импортируемый пакет `src.services`.

- [ ] **Step 1: Создать пакет**

Создать `src/services/__init__.py` с содержимым:

```python
"""Слой сервисов: service-логика, общая для всех поверхностей (GUI/CLI/API/Web)."""
```

- [ ] **Step 2: Проверить импорт**

Run: `python -c "import src.services; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add src/services/__init__.py
git commit -m "feat(services): add services package scaffold"
```

---

### Task 2: `file_policy` — валидация формата и имени файла

**Files:**
- Create: `src/services/file_policy.py`
- Test: `tests/test_file_policy.py`
- Modify (после зелёного теста сервиса): `api.py:269-286`, `web/web_app.py:224-235`

**Interfaces:**
- Produces:
  - `safe_filename(filename: str | None) -> str` — идентична обеим поверхностям.
  - `is_supported_by_glob(filename: str, glob_exts: str) -> bool` — поведение `api.py` (`SUPPORTED_FORMATS[1]`, строка вида `"*.mp3 *.wav"`, срез `*`).
  - `is_supported_by_set(filename: str, extensions) -> bool` — поведение `web_app.py` (`MEDIA_EXTENSIONS`, членство суффикса).
- Consumes: ничего (чистые функции).

- [ ] **Step 1: Написать падающий тест** (`tests/test_file_policy.py`)

```python
from src.services import file_policy


def test_safe_filename_strips_path_and_nulls():
    assert file_policy.safe_filename("../../etc/passwd") == "passwd"
    assert file_policy.safe_filename("a\\b\\c.mp3") == "c.mp3"
    assert file_policy.safe_filename("x\x00y.wav") == "xy.wav"
    assert file_policy.safe_filename("") == "upload"
    assert file_policy.safe_filename(None) == "upload"


def test_is_supported_by_glob_matches_api_behavior():
    globs = "*.mp3 *.wav *.m4a"
    assert file_policy.is_supported_by_glob("song.MP3", globs) is True
    assert file_policy.is_supported_by_glob("clip.wav", globs) is True
    assert file_policy.is_supported_by_glob("doc.txt", globs) is False


def test_is_supported_by_set_matches_web_behavior():
    exts = {".mp3", ".wav", ".m4a"}
    assert file_policy.is_supported_by_set("song.MP3", exts) is True
    assert file_policy.is_supported_by_set("doc.txt", exts) is False
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `pytest tests/test_file_policy.py -q`
Expected: FAIL (`ModuleNotFoundError: src.services.file_policy`).

- [ ] **Step 3: Реализовать `src/services/file_policy.py`** (перенос логики verbatim из обеих поверхностей)

```python
"""Валидация формата файла и защита имени от path traversal.

Две функции проверки формата сохраняют РАЗНОЕ поведение поверхностей 1:1:
api.py работает по glob-строке SUPPORTED_FORMATS[1], web_app.py — по set MEDIA_EXTENSIONS.
"""
from __future__ import annotations

from pathlib import Path


def safe_filename(filename: str | None) -> str:
    """Отбрасывает компоненты пути (POSIX и Windows) и NUL; пустое -> 'upload'."""
    name = (filename or "").replace("\\", "/")
    name = name.split("/")[-1]
    name = name.replace("\x00", "").strip()
    return name or "upload"


def is_supported_by_glob(filename: str, glob_exts: str) -> bool:
    """Поведение api.py: glob_exts — строка вида '*.mp3 *.wav'."""
    extensions = glob_exts.split()
    file_ext = Path(filename).suffix.lower()
    return any(file_ext == ext.replace("*", "") for ext in extensions)


def is_supported_by_set(filename: str, extensions) -> bool:
    """Поведение web_app.py: extensions — коллекция суффиксов вида {'.mp3'}."""
    file_ext = Path(filename).suffix.lower()
    return file_ext in extensions
```

- [ ] **Step 4: Запустить тест — убедиться, что проходит**

Run: `pytest tests/test_file_policy.py -q`
Expected: PASS.

- [ ] **Step 5: Переключить `api.py` на сервис**

В `api.py` заменить тело `is_supported_format` и `safe_filename` (строки 269-286) на делегацию:

```python
from src.services import file_policy  # добавить к импортам src.*


def is_supported_format(filename: str) -> bool:
    return file_policy.is_supported_by_glob(filename, SUPPORTED_FORMATS[1])


def safe_filename(filename: str | None) -> str:
    return file_policy.safe_filename(filename)
```

- [ ] **Step 6: Переключить `web/web_app.py` на сервис**

В `web/web_app.py` заменить тело `is_supported_format` и `safe_filename` (строки 224-235):

```python
from src.services import file_policy  # добавить к импортам src.*


def is_supported_format(filename: str) -> bool:
    return file_policy.is_supported_by_set(filename, MEDIA_EXTENSIONS)


def safe_filename(filename: str | None) -> str:
    return file_policy.safe_filename(filename)
```

- [ ] **Step 7: Прогнать связанные тесты + линт**

Run: `pytest tests/test_file_policy.py tests/test_api_security.py tests/test_web_app_persistence.py -q && ruff check src/services/file_policy.py api.py web/web_app.py`
Expected: PASS, линт чистый по изменённым файлам.

- [ ] **Step 8: Commit**

```bash
git add src/services/file_policy.py tests/test_file_policy.py api.py web/web_app.py
git commit -m "refactor(services): extract file_policy; api/web delegate"
```

---

### Task 3: `health` — статус ASR и runtime

**Files:**
- Create: `src/services/health.py`
- Test: `tests/test_health_service.py`
- Modify: `api.py:179-215`, `web/web_app.py:159-197`

**Interfaces:**
- Produces:
  - `asr_health(model_loader) -> dict[str, object]` — тот же словарь, что оба `_asr_health` (они байт-в-байт идентичны).
  - `runtime_info(platform_fn, machine_fn) -> dict[str, object]` — `{"platform": platform_fn(), "machine": machine_fn()}`.
- Consumes: `model_loader` с методами `.diagnostics()`, `.is_loaded()` (может быть `None`).

- [ ] **Step 1: Написать падающий тест** (`tests/test_health_service.py`)

```python
from src.services import health


class _Loader:
    def __init__(self, diag, loaded):
        self._diag = diag
        self._loaded = loaded

    def diagnostics(self):
        return self._diag

    def is_loaded(self):
        return self._loaded


def test_asr_health_none_loader():
    result = health.asr_health(None)
    assert result["device"] == "N/A"
    assert result["loader_loaded"] is False
    assert result["active_backend"] is None


def test_asr_health_with_diagnostics():
    loader = _Loader({"active_backend": "mlx", "device": "gpu", "model": "v3"}, True)
    result = health.asr_health(loader)
    assert result["active_backend"] == "mlx"
    assert result["device"] == "gpu"
    assert result["loader_loaded"] is True


def test_asr_health_swallows_diagnostics_error():
    class Boom(_Loader):
        def diagnostics(self):
            raise RuntimeError("x")
    result = health.asr_health(Boom(None, False))
    assert result["device"] == "N/A"
    assert result["loader_loaded"] is False


def test_runtime_info_uses_callables():
    assert health.runtime_info(lambda: "Darwin", lambda: "arm64") == {
        "platform": "Darwin", "machine": "arm64"
    }
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `pytest tests/test_health_service.py -q`
Expected: FAIL (нет модуля).

- [ ] **Step 3: Реализовать `src/services/health.py`** (verbatim-перенос тела `_asr_health`)

```python
"""Статус ASR-загрузчика и runtime — единый источник для api.py и web_app.py."""
from __future__ import annotations

from typing import Callable


def asr_health(model_loader) -> dict[str, object]:
    if model_loader is None:
        return {
            "requested_backend": None,
            "active_backend": None,
            "fallback_reason": None,
            "model": None,
            "device": "N/A",
            "repo": None,
            "cache_root": None,
            "loader_loaded": False,
            "error": None,
        }

    diagnostics = {}
    try:
        diagnostics = model_loader.diagnostics()
    except Exception:
        pass

    return {
        "requested_backend": diagnostics.get("requested_backend"),
        "active_backend": diagnostics.get("active_backend"),
        "fallback_reason": diagnostics.get("fallback_reason"),
        "model": diagnostics.get("model"),
        "device": diagnostics.get("device") or "N/A",
        "repo": diagnostics.get("repo"),
        "cache_root": diagnostics.get("cache_root"),
        "loader_loaded": model_loader.is_loaded(),
        "error": diagnostics.get("error"),
    }


def runtime_info(platform_fn: Callable[[], str], machine_fn: Callable[[], str]) -> dict[str, object]:
    return {"platform": platform_fn(), "machine": machine_fn()}
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `pytest tests/test_health_service.py -q`
Expected: PASS.

- [ ] **Step 5: Делегировать из `api.py`**

Заменить тела `_asr_health` и `_runtime_info` (строки 179-215):

```python
from src.services import health  # к импортам src.*


def _asr_health() -> dict[str, object]:
    return health.asr_health(model_loader)


def _runtime_info() -> dict[str, object]:
    return health.runtime_info(runtime_platform, machine)
```

(`runtime_platform` и `machine` — уже импортированные в api.py функции; передаём их как колбэки.)

- [ ] **Step 6: Делегировать из `web/web_app.py`**

Аналогично заменить тела `_asr_health`/`_runtime_info` (строки 159-197) на те же две делегирующие функции с `from src.services import health`.

- [ ] **Step 7: Тесты + линт**

Run: `pytest tests/test_health_service.py tests/test_app_asr_smoke.py -q && ruff check src/services/health.py api.py web/web_app.py`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/services/health.py tests/test_health_service.py api.py web/web_app.py
git commit -m "refactor(services): extract asr/runtime health; api/web delegate"
```

---

### Task 4: `llm_service` — единый диспетчер LLM-провайдеров

**Files:**
- Create: `src/services/llm_service.py`
- Test: `tests/test_llm_service.py`
- Modify: `src/gui/app_qt.py:3015-3128` (методы `_build_llm_prompt_text`, `_run_*_llm`, `_run_llm_provider`), `web/web_app.py:810-903`

**Interfaces:**
- Produces:
  - `build_prompt_text(transcript_text: str, prompt: str) -> str` — идентичен обеим поверхностям.
  - `run_provider(llm_settings: dict, transcript_text: str, prompt: str, *, provider: str, strict_empty_cli: bool) -> str`
    - `provider` — уже нормализованное каноническое имя: `"API" | "Claude Code" | "Codex" | "OpenCode" | "Pi" | "Other"`.
    - `strict_empty_cli` — если True, пустой ответ Claude Code/Codex → `EmptyLLMResponse` (поведение GUI). Если False — пустой ответ возвращается как есть (поведение web). OpenCode/Pi/Other всегда строгие (обе поверхности совпадают).
    - неизвестный `provider` → `UnknownLLMProvider(provider)`.
  - `class UnknownLLMProvider(Exception)` — несёт атрибут `.provider`.
  - `class EmptyLLMResponse(RuntimeError)` — несёт атрибут `.tool` (имя инструмента).
- Consumes: `LLMClient`, `LLMSettings` из `src/utils/llm_client.py`; stdlib `subprocess`, `shlex`, `tempfile`, `os`, `pathlib`.

**Обоснование дивергенций (roadmap п.2–4):** GUI строг к пустому ответу Claude/Codex, web — нет; поэтому флаг `strict_empty_cli`. Нормализацию имени провайдера («Other» vs «Другое») делает адаптер до вызова. Текст ошибки неизвестного провайдера формирует адаптер из `UnknownLLMProvider.provider`.

- [ ] **Step 1: Написать падающий тест** (`tests/test_llm_service.py`) — покрывает prompt, диспетч, флаг strict, unknown

```python
import subprocess
import pytest
from src.services import llm_service


def test_build_prompt_text_shape():
    text = llm_service.build_prompt_text("  привет  ", "  сделай саммари  ")
    assert text.startswith("Ты обрабатываешь транскрипт на русском языке.")
    assert "Инструкция:\nсделай саммари" in text
    assert "Транскрипт:\nпривет" in text
    assert text.endswith("\n")


def test_run_provider_unknown_raises():
    with pytest.raises(llm_service.UnknownLLMProvider) as exc:
        llm_service.run_provider({}, "t", "p", provider="Nope", strict_empty_cli=True)
    assert exc.value.provider == "Nope"


def test_claude_empty_strict_raises(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _proc(0, "", ""))
    settings = {"claude_path": "claude"}
    with pytest.raises(llm_service.EmptyLLMResponse):
        llm_service.run_provider(settings, "t", "p", provider="Claude Code", strict_empty_cli=True)


def test_claude_empty_nonstrict_returns_empty(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _proc(0, "", ""))
    settings = {"claude_path": "claude"}
    result = llm_service.run_provider(settings, "t", "p", provider="Claude Code", strict_empty_cli=False)
    assert result == ""


def test_claude_error_returncode_raises(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _proc(1, "", "boom"))
    with pytest.raises(RuntimeError, match="boom"):
        llm_service.run_provider({"claude_path": "claude"}, "t", "p", provider="Claude Code", strict_empty_cli=True)


class _proc:
    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `pytest tests/test_llm_service.py -q`
Expected: FAIL (нет модуля).

- [ ] **Step 3: Реализовать `src/services/llm_service.py`**

```python
"""Единый диспетчер LLM-провайдеров для GUI и Web (ранее продублирован).

API-путь идёт через существующий LLMClient. CLI-провайдеры (Claude Code, Codex,
OpenCode, Pi, Other) запускаются через subprocess. Дивергенция GUI/web по пустому
ответу Claude/Codex управляется флагом strict_empty_cli.
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
    def __init__(self, provider: str):
        super().__init__(provider)
        self.provider = provider


class EmptyLLMResponse(RuntimeError):
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


def _run_api(settings: dict, transcript_text: str, prompt: str) -> str:
    client = LLMClient(LLMSettings(
        api_url=settings["api_url"],
        api_key=settings["api_key"],
        model=settings["model"],
        temperature=settings["temperature"],
    ))
    return client.process_transcript(transcript_text, prompt)


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
        result = subprocess.run(command, input=prompt_text, capture_output=True, text=True, timeout=_TIMEOUT)
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
) -> str:
    if provider == "API":
        return _run_api(llm_settings, transcript_text, prompt)
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
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `pytest tests/test_llm_service.py -q`
Expected: PASS.

- [ ] **Step 5: Делегировать из `web/web_app.py`**

Заменить `_build_llm_prompt_text` и `_run_llm_provider` (строки 810-903), удалив `_run_api_llm`/`_run_claude_code_llm`/`_run_codex_llm`/`_run_generic_llm`:

```python
from src.services import llm_service  # к импортам src.*


def _build_llm_prompt_text(transcript_text: str, prompt: str) -> str:
    return llm_service.build_prompt_text(transcript_text, prompt)


def _run_llm_provider(llm_settings: dict, transcript_text: str, prompt: str) -> str:
    provider = llm_settings.get("provider", "API")
    if provider == "Другое":
        provider = "Other"
    try:
        return llm_service.run_provider(
            llm_settings, transcript_text, prompt, provider=provider, strict_empty_cli=False,
        )
    except llm_service.UnknownLLMProvider as exc:
        raise RuntimeError(f"Неизвестный провайдер: {exc.provider}")
```

- [ ] **Step 6: Делегировать из `src/gui/app_qt.py`**

Заменить методы `_build_llm_prompt_text`, `_run_api_llm`, `_run_claude_code_llm`, `_run_codex_llm`, `_run_generic_cli_prompt`, `_run_opencode_llm`, `_run_pi_llm`, `_run_other_llm`, `_run_llm_provider` (строки 3015-3128) на:

```python
    def _build_llm_prompt_text(self, transcript_text: str, prompt: str) -> str:
        return llm_service.build_prompt_text(transcript_text, prompt)

    def _run_llm_provider(self, llm_settings: dict, transcript_text: str, prompt: str) -> str:
        raw = llm_settings.get("provider", "API")
        provider = "Other" if self._normalize_llm_provider(raw) == "Other" else raw
        try:
            return llm_service.run_provider(
                llm_settings, transcript_text, prompt, provider=provider, strict_empty_cli=True,
            )
        except llm_service.UnknownLLMProvider as exc:
            raise RuntimeError(self._t(
                f"Неизвестный LLM-провайдер: {exc.provider}",
                f"Unknown LLM provider: {exc.provider}",
            ))
```

Добавить `from src.services import llm_service` к импортам gui; убедиться, что `shlex`, `tempfile`, `subprocess` больше не нужны в app_qt.py только если их не используют другие методы (проверить перед удалением импортов).

- [ ] **Step 7: Характеризующий GUI-тест диспетча (headless)** — новый тест, что gui зовёт сервис с strict=True и нормализует «Other»

```python
# tests/test_llm_service.py — дописать
def test_gui_and_web_normalize_other(monkeypatch):
    calls = {}
    monkeypatch.setattr(llm_service, "run_provider",
                        lambda *a, provider, strict_empty_cli, **k: calls.update(provider=provider, strict=strict_empty_cli) or "ok")
    # эмулируем web-делегацию
    from web import web_app
    web_app._run_llm_provider({"provider": "Другое"}, "t", "p")
    assert calls == {"provider": "Other", "strict": False}
```

(Если импорт `web.web_app` тянет тяжёлые зависимости на сборе — пометить тест `@pytest.mark.skipif` по доступности, или изолировать делегацию в отдельную чистую функцию. Решение принять при реализации, зафиксировать в PR.)

- [ ] **Step 8: Прогон + линт**

Run: `QT_QPA_PLATFORM=offscreen pytest tests/test_llm_service.py -q && ruff check src/services/llm_service.py web/web_app.py src/gui/app_qt.py`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/services/llm_service.py tests/test_llm_service.py web/web_app.py src/gui/app_qt.py
git commit -m "refactor(services): unify LLM provider dispatch; gui/web delegate"
```

---

### Task 5: `task_store` — единая модель задач и persistence

**Files:**
- Create: `src/services/task_store.py`
- Test: `tests/test_task_store.py`
- Modify: `api.py` (`tasks_storage`, `_register_task:307-323`, restore-функции `restore_tasks_from_results:404+`), `web/web_app.py` (`_register_task:312-335`, `_persist_tasks_index`, `_restore_*`)

**Interfaces:**
- Produces:
  - `def new_task_record(task_id, filename, file_size, *, message, extra=None) -> dict` — базовая схема задачи (общие 13 полей из обеих поверхностей), `extra` домешивает web-специфичные поля (`user`, `output_formats`, `enable_diarization`, `num_speakers`, `stage`).
  - `class TaskStore` — обёртка над `dict[str, dict]` + persistence-колбэки (index/tombstone), инкапсулирующая `register`, `get`, `visible_copy`, restore-из-results/meta.
- Consumes: `src.utils.atomic_json.load_json/save_json_atomic`, `datetime`.

**Метод извлечения (relocate, не переписывать):** тела `_register_task`, `_persist_tasks_index`, `_restore_completed_task_from_meta`, `_restore_tasks_from_index`, `_restore_tasks_from_results`, `_cleanup_deleted_task_tombstones` из `web/web_app.py` и `restore_tasks_from_results` из `api.py` переносятся в `TaskStore` verbatim; различие схем (api без user-полей, web с ними) выражается параметром `extra`/подклассом. Общий инвариант: 13 базовых полей (`task_id, status, created_at, started_at, completed_at, progress, stage_progress, processed_seconds, total_seconds, progress_indeterminate, filename, file_size, message`).

- [ ] **Step 1: Характеризующий тест на базовую схему** (`tests/test_task_store.py`) — фиксирует ровно текущие поля

```python
from src.services import task_store


def test_new_task_record_base_fields():
    rec = task_store.new_task_record("t1", "a.mp3", 123, message="В очереди")
    assert rec["task_id"] == "t1"
    assert rec["filename"] == "a.mp3"
    assert rec["file_size"] == 123
    assert rec["status"] == "pending"
    assert rec["progress"] == 0
    assert rec["progress_indeterminate"] is False
    assert rec["message"] == "В очереди"
    assert rec["started_at"] is None and rec["completed_at"] is None
    # created_at присутствует и является ISO-строкой
    assert isinstance(rec["created_at"], str) and "T" in rec["created_at"]


def test_new_task_record_web_extra():
    rec = task_store.new_task_record(
        "t2", "b.wav", 5, message="В очереди",
        extra={"user": "alice", "output_formats": [], "enable_diarization": False,
               "num_speakers": None, "stage": ""},
    )
    assert rec["user"] == "alice"
    assert rec["stage"] == ""
    # базовые поля тоже на месте
    assert rec["task_id"] == "t2"
```

- [ ] **Step 2: Запустить — падает**

Run: `pytest tests/test_task_store.py -q`
Expected: FAIL (нет модуля).

- [ ] **Step 3: Реализовать `new_task_record` + каркас `TaskStore`**

```python
"""Единая модель задачи и persistence для api.py и web_app.py.

Базовая схема задачи (13 полей) идентична обеим поверхностям; web добавляет
поля через параметр extra. Перенос функций persistence/restore выполняется
verbatim из web_app.py на последующих шагах.
"""
from __future__ import annotations

from datetime import datetime


_BASE_MESSAGE = "Задача в очереди на обработку"


def new_task_record(
    task_id: str,
    filename: str,
    file_size: int,
    *,
    message: str = _BASE_MESSAGE,
    extra: dict | None = None,
) -> dict:
    record = {
        "task_id": task_id,
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "started_at": None,
        "completed_at": None,
        "progress": 0,
        "stage_progress": None,
        "processed_seconds": None,
        "total_seconds": None,
        "progress_indeterminate": False,
        "filename": filename,
        "file_size": file_size,
        "message": message,
    }
    if extra:
        record.update(extra)
    return record
```

- [ ] **Step 4: Запустить — проходит**

Run: `pytest tests/test_task_store.py -q`
Expected: PASS.

- [ ] **Step 5: Делегировать `_register_task` в `api.py`**

```python
from src.services import task_store  # к импортам


def _register_task(task_id: str, filename: str, file_size: int):
    tasks_storage[task_id] = task_store.new_task_record(task_id, filename, file_size)
```

- [ ] **Step 6: Делегировать `_register_task` в `web/web_app.py`** (сохранить `message="В очереди"` и web-поля через `extra`, плюс побочные `log_queues`/`_persist_tasks_index`)

```python
from src.services import task_store  # к импортам


def _register_task(task_id: str, filename: str, file_size: int, user: str):
    tasks_storage[task_id] = task_store.new_task_record(
        task_id, filename, file_size, message="В очереди",
        extra={"stage": "", "output_formats": [], "enable_diarization": False,
               "num_speakers": None, "user": user},
    )
    log_queues[task_id] = []
    _persist_tasks_index()
```

- [ ] **Step 7: Перенести restore/persist-функции в `TaskStore` (relocate verbatim)**

Прочитать текущие тела `_persist_tasks_index`, `_restore_completed_task_from_meta`, `_restore_tasks_from_index`, `_restore_tasks_from_results`, `_cleanup_deleted_task_tombstones` (`web/web_app.py`) и `restore_tasks_from_results` (`api.py`); перенести как методы `TaskStore`, поверхности вызывают методы. Написать характеризующий тест на восстановление из `meta.json` во временном каталоге (round-trip: `save_json_atomic` → `TaskStore.restore_*` → ожидаемая запись).

- [ ] **Step 8: Прогон persistence-тестов + линт**

Run: `pytest tests/test_task_store.py tests/test_web_app_persistence.py tests/test_api_integration.py -q && ruff check src/services/task_store.py api.py web/web_app.py`
Expected: PASS (не хуже baseline).

- [ ] **Step 9: Commit**

```bash
git add src/services/task_store.py tests/test_task_store.py api.py web/web_app.py
git commit -m "refactor(services): extract task_store; api/web delegate registration+restore"
```

---

### Task 6: `transcription_service` — единая обвязка транскрибации

**Files:**
- Create: `src/services/transcription_service.py`
- Test: `tests/test_transcription_service.py`
- Modify: `api.py:609`, `web/web_app.py:615`, `cli.py:366`, `src/gui/app_qt.py` (места создания `TranscriptionProcessor`)

**Interfaces:**
- Produces:
  - `def build_processor(model_loader, stats_manager, *, logger=None, progress_callback=None) -> TranscriptionProcessor` — единая точка сборки процессора (сейчас конструктор зовётся вручную в 4 местах с одинаковыми аргументами).
- Consumes: `src.core.processor.TranscriptionProcessor`, `src.core.model_loader.ModelLoader`, `src.utils.processing_stats.ProcessingStats`.

**Примечание по 1:1:** это тонкая фабрика, НЕ меняющая сигнатуру `TranscriptionProcessor(model_loader, stats_manager, logger=..., progress_callback=...)`. Каждая поверхность продолжает сама управлять форматами/диаризацией через существующий вызов `process_file(...)`; сервис лишь убирает дублирование конструктора. Более глубокая унификация `process_file`-обвязки — вне Фазы 1 (риск для 1:1), рассматривается в отдельной фазе при необходимости.

- [ ] **Step 1: Характеризующий тест** (`tests/test_transcription_service.py`)

```python
from src.services import transcription_service
from src.core.processor import TranscriptionProcessor


class _Loader: ...
class _Stats: ...


def test_build_processor_returns_processor_with_deps():
    loader, stats = _Loader(), _Stats()
    logs = []
    proc = transcription_service.build_processor(
        loader, stats, logger=logs.append, progress_callback=None,
    )
    assert isinstance(proc, TranscriptionProcessor)
    assert proc.model_loader is loader
```

(Проверить фактические имена атрибутов процессора по `src/core/processor.py:25` перед финализацией теста; при необходимости заменить `proc.model_loader` на реальное имя поля.)

- [ ] **Step 2: Запустить — падает**

Run: `pytest tests/test_transcription_service.py -q`
Expected: FAIL.

- [ ] **Step 3: Реализовать**

```python
"""Единая фабрика TranscriptionProcessor — устраняет ручную сборку в 4 поверхностях."""
from __future__ import annotations

from typing import Callable, Optional

from src.core.processor import TranscriptionProcessor


def build_processor(
    model_loader,
    stats_manager,
    *,
    logger: Optional[Callable] = None,
    progress_callback: Optional[Callable] = None,
) -> TranscriptionProcessor:
    return TranscriptionProcessor(
        model_loader,
        stats_manager,
        logger=logger,
        progress_callback=progress_callback,
    )
```

- [ ] **Step 4: Запустить — проходит**

Run: `pytest tests/test_transcription_service.py -q`
Expected: PASS.

- [ ] **Step 5: Переключить 4 поверхности на `build_processor`**

В `api.py:609`, `web/web_app.py:615`, `cli.py:366`, `app_qt.py` заменить прямой `TranscriptionProcessor(...)` на `transcription_service.build_processor(...)` с теми же аргументами. Добавить импорт `from src.services import transcription_service`.

- [ ] **Step 6: Прогон progress-тестов всех поверхностей**

Run: `pytest tests/test_processor_progress.py tests/test_cli_progress.py tests/test_api_progress.py tests/test_transcription_service.py -q && ruff check src/services/transcription_service.py`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/services/transcription_service.py tests/test_transcription_service.py api.py web/web_app.py cli.py src/gui/app_qt.py
git commit -m "refactor(services): centralize TranscriptionProcessor construction"
```

---

### Task 7: Гейт завершения Фазы 1

- [ ] **Step 1: Полный прогон тестов**

Run: `QT_QPA_PLATFORM=offscreen pytest -q`
Expected: не хуже baseline Фазы 0 (то же число passed, 0 новых failed).

- [ ] **Step 2: Линт всего нового слоя**

Run: `ruff check src/services/ api.py web/web_app.py src/gui/app_qt.py cli.py`
Expected: чисто.

- [ ] **Step 3: Smoke каждой поверхности**

Run:
```bash
python -c "import api; import web.web_app; import cli; print('backends import ok')"
QT_QPA_PLATFORM=offscreen python -c "import src.gui.app_qt; print('gui import ok')"
python cli.py --help >/dev/null && echo "cli ok"
```
Expected: все печатают ok без трейсбеков.

- [ ] **Step 4: Итоговый коммит фазы**

```bash
git commit --allow-empty -m "chore: phase 1 gate — services layer green, surfaces delegate"
```

---

## Self-Review

- **Spec coverage:** Реализует «Фаза 1 — Общий сервис-слой без дублей»: `file_policy` (Task 2), `health` (Task 3), `llm_service` (Task 4), `task_store` (Task 5), `transcription_service` (Task 6). Каждый — характеризующий тест → extract → делегация. ✅
- **Дивергенции из roadmap:** п.1 (`is_supported_by_glob`/`by_set`), п.2 (`strict_empty_cli`), п.3 (нормализация «Другое»→«Other» в адаптере), п.4 (`UnknownLLMProvider`, текст в адаптере) — все зафиксированы тестами. ✅
- **Placeholder scan:** реальный код в каждом шаге, где он нужен; Task 5 Step 7 и Task 6 Step 1 явно требуют сверки текущих имён/тел перед финализацией — это инструкция, а не заглушка (код существует и указан по `file:line`). ✅
- **Type consistency:** `run_provider(..., provider=, strict_empty_cli=)`, `new_task_record(..., extra=)`, `build_processor(..., logger=, progress_callback=)` — имена совпадают между определением и вызовами в адаптерах. ✅
- **1:1:** каждый вынос под характеризующим тестом; расхождения сохранены флагами, не унифицированы. ✅
