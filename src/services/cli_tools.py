"""Реестр LLM-провайдеров и резолвер CLI-инструментов.

Единственный источник правды о том, какие провайдеры есть, как называется их
бинарь, где его искать и как проверить, что он запускается. Все фронтенды
(PyQt, web, TUI, Liquid) берут список отсюда — напрямую или через воркер.

Зачем свой поиск вместо `shutil.which()`: GUI-приложение, запущенное из
Finder/Dock/Проводника, получает системный PATH без homebrew/bun/nvm/npm-global,
и голое `which("claude")` там пусто. Поэтому к PATH процесса добавляются
известные каталоги установки CLI, и этот же расширенный PATH передаётся дочернему
процессу (npm-шимы `claude`/`opencode` сами ищут `node` по PATH).
"""
from __future__ import annotations

import glob
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass

PROBE_TIMEOUT = 10  # сек на `<tool> --version`
_VERSION_RE = re.compile(r"\b(\d+\.\d+(?:\.\d+)?)\b")


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    name: str
    binary: str | None
    settings_prefix: str
    install_hint: str
    has_provider_field: bool = False
    version_args: tuple[str, ...] = ("--version",)

    @property
    def is_cli(self) -> bool:
        return self.binary is not None


PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec("api", "API", None, "api", ""),
    ProviderSpec("claude", "Claude Code", "claude", "claude", "npm install -g @anthropic-ai/claude-code"),
    ProviderSpec("codex", "Codex", "codex", "codex", "npm install -g @openai/codex"),
    ProviderSpec("opencode", "OpenCode", "opencode", "opencode", "brew install opencode  /  npm install -g opencode-ai"),
    ProviderSpec("pi", "Pi", "pi", "pi", "npm install -g @mariozechner/pi-coding-agent", has_provider_field=True),
    ProviderSpec("omp", "oh-my-pi", "omp", "omp", "brew install can1357/tap/omp  /  bun install -g @oh-my-pi/pi-coding-agent", has_provider_field=True),
    ProviderSpec("other", "Other", None, "other", ""),
)

_NAME_ALIASES = {"Другое": "Other", "Внешний CLI": "Other"}


def canonical_provider_names() -> list[str]:
    return [spec.name for spec in PROVIDERS]


def cli_specs() -> list[ProviderSpec]:
    return [spec for spec in PROVIDERS if spec.is_cli]


def provider_by_name(name: str) -> ProviderSpec:
    """Спека по каноническому имени из настроек («Другое»/«Other» → other)."""
    wanted = _NAME_ALIASES.get(name, name)
    for spec in PROVIDERS:
        if spec.name == wanted or spec.id == wanted:
            return spec
    raise KeyError(name)


@dataclass(frozen=True)
class ToolStatus:
    id: str
    provider: str
    status: str  # found | missing | broken | not_applicable
    path: str | None
    version: str | None
    detail: str | None
    install_hint: str

    def to_dict(self) -> dict:
        return asdict(self)


# --- поиск ----------------------------------------------------------------

def _home() -> str:
    if os.name == "nt":
        return os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return os.environ.get("HOME") or os.path.expanduser("~")


def _known_dirs() -> list[str]:
    home = _home()
    if os.name == "nt":
        appdata = os.environ.get("APPDATA", "")
        local = os.environ.get("LOCALAPPDATA", "")
        return [
            os.path.join(appdata, "npm"),
            os.path.join(local, "pnpm"),
            os.path.join(home, ".bun", "bin"),
            os.path.join(home, "scoop", "shims"),
            os.path.join(home, ".cargo", "bin"),
            os.path.join(home, ".volta", "bin"),
        ]
    nvm = sorted(
        glob.glob(os.path.join(home, ".nvm", "versions", "node", "*", "bin")),
        key=_nvm_sort_key,
        reverse=True,
    )
    dirs = [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        os.path.join(home, ".local", "bin"),
        os.path.join(home, ".bun", "bin"),
        os.path.join(home, ".npm-global", "bin"),
        os.path.join(home, ".volta", "bin"),
        os.path.join(home, ".cargo", "bin"),
        os.path.join(home, ".omp", "bin"),
        os.path.join(home, ".pi", "bin"),
        os.path.join(home, ".local", "share", "pnpm"),
    ]
    if sys.platform == "darwin":
        dirs.append(os.path.join(home, "Library", "pnpm"))
    else:
        dirs.append("/snap/bin")
    return dirs + nvm


def _nvm_sort_key(path: str) -> tuple[int, ...]:
    version = os.path.basename(os.path.dirname(path)).lstrip("v")
    parts = []
    for piece in version.split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def search_dirs() -> list[str]:
    """PATH процесса + известные каталоги установки; только существующие, без дублей."""
    seen: set[str] = set()
    result: list[str] = []
    raw = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
    for directory in raw + _known_dirs():
        if directory in seen:
            continue
        seen.add(directory)
        if os.path.isdir(directory):
            result.append(directory)
    return result


def child_environment(base: dict | None = None) -> dict:
    """Окружение для дочернего CLI: тот же расширенный PATH, что и у поиска."""
    env = dict(os.environ if base is None else base)
    env["PATH"] = os.pathsep.join(search_dirs())
    return env


def _is_executable(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def _candidates_for(name: str) -> list[str]:
    if os.name == "nt":
        exts = [e for e in os.environ.get("PATHEXT", ".EXE;.CMD;.BAT").split(";") if e]
        if os.path.splitext(name)[1]:
            return [name]
        return [name + ext.lower() for ext in exts] + [name + ext for ext in exts]
    return [name]


def locate_tool(spec: ProviderSpec, override: str | None = None) -> str | None:
    """Абсолютный путь к бинарю или None. Без запуска — дёшево, для момента вызова.

    `override` — то, что ввёл пользователь: путь проверяется как есть, голое имя
    ищется по search_dirs().
    """
    name = (override or "").strip() or spec.binary
    if not name:
        return None
    expanded = os.path.expanduser(name)
    if os.sep in expanded or (os.altsep and os.altsep in expanded):
        return expanded if _is_executable(expanded) else None
    for directory in search_dirs():
        for candidate in _candidates_for(expanded):
            full = os.path.join(directory, candidate)
            if _is_executable(full):
                return full
    return None


def _windows_startupinfo():
    if os.name != "nt":
        return None
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return info


def _parse_version(text: str) -> str | None:
    match = _VERSION_RE.search(text or "")
    return match.group(1) if match else None


def resolve_tool(spec: ProviderSpec, override: str | None = None) -> ToolStatus:
    """Найти и проверить запуском (`--version`). Для UI/скана, не для каждого вызова."""
    if not spec.is_cli:
        return ToolStatus(spec.id, spec.name, "not_applicable", None, None, None, spec.install_hint)
    path = locate_tool(spec, override)
    if path is None:
        return ToolStatus(spec.id, spec.name, "missing", None, None, None, spec.install_hint)
    try:
        result = subprocess.run(
            [path, *spec.version_args],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT,
            env=child_environment(),
            startupinfo=_windows_startupinfo(),
        )
    except subprocess.TimeoutExpired:
        return ToolStatus(spec.id, spec.name, "broken", path, None, f"timeout after {PROBE_TIMEOUT}s", spec.install_hint)
    except OSError as exc:
        return ToolStatus(spec.id, spec.name, "broken", path, None, str(exc), spec.install_hint)
    output = f"{result.stdout or ''}\n{result.stderr or ''}"
    if result.returncode != 0:
        detail = " ".join(output.split())[:300] or f"exit {result.returncode}"
        return ToolStatus(spec.id, spec.name, "broken", path, None, detail, spec.install_hint)
    return ToolStatus(spec.id, spec.name, "found", path, _parse_version(output), None, spec.install_hint)


# --- скан с кэшем -----------------------------------------------------------

_cache: dict[tuple, list[ToolStatus]] = {}
_cache_lock = threading.Lock()


def invalidate_cache() -> None:
    with _cache_lock:
        _cache.clear()


def scan(overrides: dict[str, str] | None = None, *, fresh: bool = False) -> list[ToolStatus]:
    """Статусы всех CLI-провайдеров. `overrides`: {spec.id: путь из настроек}."""
    overrides = {k: v for k, v in (overrides or {}).items() if v}
    key = tuple(sorted(overrides.items()))
    if not fresh:
        with _cache_lock:
            cached = _cache.get(key)
        if cached is not None:
            return cached
    specs = cli_specs()
    with ThreadPoolExecutor(max_workers=min(6, len(specs))) as pool:
        statuses = list(pool.map(lambda spec: resolve_tool(spec, overrides.get(spec.id)), specs))
    with _cache_lock:
        _cache[key] = statuses
    return statuses


def overrides_from_settings(settings: dict) -> dict[str, str]:
    """{spec.id: путь} из плоского словаря настроек (`claude_path`, `omp_path`, …)."""
    result = {}
    for spec in cli_specs():
        value = settings.get(f"{spec.settings_prefix}_path")
        if isinstance(value, str) and value.strip() and value.strip() != spec.binary:
            result[spec.id] = value.strip()
    return result
