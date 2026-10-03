"""Статус ASR-загрузчика и runtime — единый источник для api.py и web_app.py.

Оба монолита содержали байт-в-байт идентичные _asr_health/_runtime_info; здесь
они объединены. runtime_info принимает platform/machine как колбэки, чтобы не
навязывать импорт `platform` вызывающей стороне.
"""
from __future__ import annotations

from collections.abc import Callable


def asr_health(model_loader) -> dict[str, object]:
    if model_loader is None:
        return {
            "requested_backend": None,
            "active_backend": None,
            "fallback_reason": None,
            "model": None,
            "device": "N/A",
            "segmentation_mode": None,
            "segmentation_fallback_reason": None,
            "requested_provider": None,
            "provider": None,
            "quantization": None,
            "provider_fallback_reason": None,
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
        "segmentation_mode": diagnostics.get("segmentation_mode"),
        "segmentation_fallback_reason": diagnostics.get("segmentation_fallback_reason"),
        "requested_provider": diagnostics.get("requested_provider"),
        "provider": diagnostics.get("provider"),
        "quantization": diagnostics.get("quantization"),
        "provider_fallback_reason": diagnostics.get("provider_fallback_reason"),
        "repo": diagnostics.get("repo"),
        "cache_root": diagnostics.get("cache_root"),
        "loader_loaded": model_loader.is_loaded(),
        "error": diagnostics.get("error"),
    }


# Пути и репозитории на сервере: нужны для диагностики владельцу, а не любому, кто
# дотянулся до неавторизованного /health (раскладка контейнера, домашний каталог).
_SERVER_PATH_FIELDS = ("repo", "cache_root")


def public_asr_health(model_loader) -> dict[str, object]:
    """`asr_health` для неавторизованного /health: без путей сервера."""
    return {key: value for key, value in asr_health(model_loader).items() if key not in _SERVER_PATH_FIELDS}


def runtime_info(
    platform_fn: Callable[[], str],
    machine_fn: Callable[[], str],
) -> dict[str, object]:
    return {"platform": platform_fn(), "machine": machine_fn()}
