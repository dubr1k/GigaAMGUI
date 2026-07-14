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
        "repo": diagnostics.get("repo"),
        "cache_root": diagnostics.get("cache_root"),
        "loader_loaded": model_loader.is_loaded(),
        "error": diagnostics.get("error"),
    }


def runtime_info(
    platform_fn: Callable[[], str],
    machine_fn: Callable[[], str],
) -> dict[str, object]:
    return {"platform": platform_fn(), "machine": machine_fn()}
