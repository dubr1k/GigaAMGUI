"""Настройки LLM для Python-слоя без GUI (MCP-сервер, CLI): тот же словарь, что
`llm_service.run_provider` получает от PyQt и TUI.

Приоритет (выше — сильнее):

1. `overrides` — явные значения вызывающего кода (ключи выходного словаря:
   `provider`, `model`, `api_key`, …);
2. переменные окружения `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_URL`,
   `LLM_API_KEY`, `LLM_TEMPERATURE`;
3. `<config_dir>/user_settings.json` десктопного приложения — плоские ключи
   `llm_provider`, `llm_api_url`, `llm_model`, `llm_temperature` (строка или
   число), `llm_allow_tools`, `llm_<prefix>_path` (голое имя бинаря или пустая
   строка — не override, а сброс), `llm_<prefix>_args`, `llm_<prefix>_provider`;
4. `<config_dir>/.env` — только `LLM_API_KEY` (десктоп хранит секрет там,
   а не в JSON); действует, когда переменной окружения нет;
5. `<config_dir>/tui_settings.json` — поля `TuiSettings` из `tui/src/settings.rs`:
   `llm_provider`, `llm_api_url`, `llm_model`, `llm_temperature`,
   `llm_allow_tools` и словари по префиксам `llm_tool_paths`,
   `llm_internal_providers`, `llm_extra_args`;
6. значения по умолчанию: `provider="API"`, `api_url=src.config.LLM_API_URL`,
   `temperature=0.2`, пути CLI — из `cli_tools.scan()` либо имя бинаря.

`config_dir()` совпадает с TUI: `GIGAAM_CONFIG_DIR`, иначе каталог
`user_settings.json` десктопного приложения.
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

from dotenv import dotenv_values

from src.config import LLM_API_URL
from src.services import cli_tools
from src.utils.user_settings import _default_settings_file

DEFAULT_TEMPERATURE = 0.2

_ENV_KEYS = {
    "LLM_PROVIDER": "provider",
    "LLM_MODEL": "model",
    "LLM_API_URL": "api_url",
    "LLM_API_KEY": "api_key",
}


def config_dir() -> Path:
    """Каталог настроек: `GIGAAM_CONFIG_DIR`, иначе каталог десктопного приложения."""
    override = os.environ.get("GIGAAM_CONFIG_DIR")
    if override:
        return Path(override)
    return Path(_default_settings_file()).parent


_default_config_dir = config_dir  # параметр resolve(config_dir=...) затеняет функцию


def _read_json_object(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _text(value) -> str | None:
    return value if isinstance(value, str) else None


def _temperature(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _defaults() -> dict:
    out = {
        "provider": "API",
        "api_url": LLM_API_URL,
        "api_key": "",
        "model": "",
        "temperature": DEFAULT_TEMPERATURE,
    }
    found = {tool.id: tool.path for tool in cli_tools.scan()}
    for spec in cli_tools.PROVIDERS:
        if spec.id == "api":
            continue
        out[f"{spec.settings_prefix}_path"] = found.get(spec.id) or spec.binary or ""
        out[f"{spec.settings_prefix}_args"] = ""
        if spec.has_provider_field:
            out[f"{spec.settings_prefix}_provider"] = ""
    out["llm_allow_tools"] = False
    return out


def _apply_tui_settings(out: dict, data: dict) -> None:
    """tui_settings.json: поля TuiSettings как есть."""
    for key in ("provider", "api_url", "api_key", "model"):
        value = _text(data.get(f"llm_{key}"))
        if value is not None:
            out[key] = value
    temperature = _temperature(data.get("llm_temperature"))
    if temperature is not None:
        out["temperature"] = temperature
    if isinstance(data.get("llm_allow_tools"), bool):
        out["llm_allow_tools"] = data["llm_allow_tools"]
    for json_key, suffix in (("llm_tool_paths", "path"), ("llm_internal_providers", "provider"),
                             ("llm_extra_args", "args")):
        table = data.get(json_key)
        if not isinstance(table, dict):
            continue
        for prefix, value in table.items():
            if isinstance(value, str) and f"{prefix}_{suffix}" in out:
                out[f"{prefix}_{suffix}"] = value


def _apply_user_settings(out: dict, data: dict, defaults: dict) -> None:
    """user_settings.json десктопа: правила `shared_settings_from_main_app` из tui/src/settings.rs."""
    for key in ("provider", "api_url", "model"):
        value = _text(data.get(f"llm_{key}"))
        if value is not None:
            out[key] = value
    temperature = _temperature(data.get("llm_temperature"))
    if temperature is not None:
        out["temperature"] = temperature
    if isinstance(data.get("llm_allow_tools"), bool):
        out["llm_allow_tools"] = data["llm_allow_tools"]
    for spec in cli_tools.PROVIDERS:
        if spec.id == "api":
            continue
        prefix = spec.settings_prefix
        path = _text(data.get(f"llm_{prefix}_path"))
        if path is not None:
            path = path.strip()
            # Голое имя бинаря (или пусто) — не override: возвращаемся к найденному пути
            out[f"{prefix}_path"] = path if path and path != spec.binary else defaults[f"{prefix}_path"]
        args = _text(data.get(f"llm_{prefix}_args"))
        if args is not None:
            out[f"{prefix}_args"] = args if args.strip() else ""
        if spec.has_provider_field:
            provider = _text(data.get(f"llm_{prefix}_provider"))
            if provider is not None:
                out[f"{prefix}_provider"] = provider if provider.strip() else ""


def _apply_env(out: dict, env: Mapping[str, str]) -> None:
    for env_key, out_key in _ENV_KEYS.items():
        value = env.get(env_key)
        if value:
            out[out_key] = value
    temperature = _temperature(env.get("LLM_TEMPERATURE"))
    if temperature is not None:
        out["temperature"] = temperature


def resolve(overrides: dict | None = None, *, config_dir: Path | None = None,
            env: Mapping[str, str] | None = None) -> dict:
    """Словарь настроек LLM в формате `llm_service.run_provider` (см. docstring модуля)."""
    directory = Path(config_dir) if config_dir is not None else _default_config_dir()
    env = os.environ if env is None else env

    defaults = _defaults()
    out = dict(defaults)
    _apply_tui_settings(out, _read_json_object(directory / "tui_settings.json"))
    dotenv_key = (dotenv_values(directory / ".env").get("LLM_API_KEY") or "") if (directory / ".env").is_file() else ""
    if dotenv_key:
        out["api_key"] = dotenv_key
    _apply_user_settings(out, _read_json_object(directory / "user_settings.json"), defaults)
    _apply_env(out, env)
    out.update(overrides or {})
    return out
