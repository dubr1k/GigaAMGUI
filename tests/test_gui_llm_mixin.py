"""Характеризующие тесты LlmMixin — логика LLM-обработки GUI, вынесенная из app_qt.

Используем лёгкий stub вместо полной инициализации QMainWindow.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from src.gui.llm_mixin import LlmMixin
from src.services import llm_service


class _Stub(LlmMixin):
    """Минимальный носитель mixin с зависимостями, которые обычно даёт главное окно."""

    def _t(self, ru, en):
        return ru

    def _normalize_llm_provider(self, provider):
        return "Other" if provider in {"Другое", "Other"} else provider


def test_compact_llm_error_friendly_rule():
    stub = _Stub()
    msg = stub._compact_llm_error("HTTP 401 Unauthorized from provider")
    assert "авторизаци" in msg.lower()


def test_compact_llm_error_truncates_long_text():
    stub = _Stub()
    long = "x" * 500
    out = stub._compact_llm_error(long, limit=180)
    assert len(out) == 180
    assert out.endswith("…")


def test_compact_llm_error_passthrough_short():
    stub = _Stub()
    assert stub._compact_llm_error("короткая ошибка") == "короткая ошибка"


def test_run_llm_provider_delegates_and_normalizes_other(monkeypatch):
    stub = _Stub()
    captured = {}

    def fake_run(settings, text, prompt, *, provider, strict_empty_cli, on_stream_chunk=None):
        captured["provider"] = provider
        captured["strict"] = strict_empty_cli
        captured["stream_callback"] = on_stream_chunk
        return "ok"

    monkeypatch.setattr(llm_service, "run_provider", fake_run)
    result = stub._run_llm_provider({"provider": "Другое"}, "t", "p")
    assert result == "ok"
    assert captured == {"provider": "Other", "strict": True, "stream_callback": None}


def test_run_llm_provider_unknown_maps_to_runtimeerror(monkeypatch):
    stub = _Stub()

    def fake_run(*a, provider, strict_empty_cli, on_stream_chunk=None, **k):
        raise llm_service.UnknownLLMProvider(provider)

    monkeypatch.setattr(llm_service, "run_provider", fake_run)
    with pytest.raises(RuntimeError, match="Неизвестный LLM-провайдер: Zzz"):
        stub._run_llm_provider({"provider": "Zzz"}, "t", "p")
