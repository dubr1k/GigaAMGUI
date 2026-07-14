"""Характеризующие тесты health-сервиса — фиксируют текущее поведение api/web 1:1."""
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
    # ровно 11 ключей контракта
    assert set(result) == {
        "requested_backend", "active_backend", "fallback_reason", "model",
        "device", "repo", "cache_root", "loader_loaded", "error",
        "segmentation_mode", "segmentation_fallback_reason",
    }


def test_asr_health_with_diagnostics():
    loader = _Loader({
        "active_backend": "mlx",
        "device": "gpu",
        "model": "v3",
        "segmentation_mode": "vad",
        "segmentation_fallback_reason": None,
    }, True)
    result = health.asr_health(loader)
    assert result["active_backend"] == "mlx"
    assert result["device"] == "gpu"
    assert result["loader_loaded"] is True
    assert result["segmentation_mode"] == "vad"
    assert result["segmentation_fallback_reason"] is None


def test_asr_health_device_fallback_when_empty():
    loader = _Loader({"device": None}, True)
    assert health.asr_health(loader)["device"] == "N/A"


def test_asr_health_swallows_diagnostics_error():
    class Boom(_Loader):
        def diagnostics(self):
            raise RuntimeError("x")

    result = health.asr_health(Boom(None, False))
    assert result["device"] == "N/A"
    assert result["loader_loaded"] is False


def test_runtime_info_uses_callables():
    assert health.runtime_info(lambda: "Darwin", lambda: "arm64") == {
        "platform": "Darwin",
        "machine": "arm64",
    }
