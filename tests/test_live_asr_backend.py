import pytest

from src.live.asr_backend import LazyModelBackend


class FakeLoader:
    def __init__(self, loads: bool):
        self._loads = loads
        self._loaded = False
        self.calls = []

    def is_loaded(self):
        return self._loaded

    def load_model(self, logger=None):
        self._loaded = self._loads
        return self._loads

    def diagnostics(self):
        return {"error": "no weights"}

    def transcribe_window(self, audio, sample_rate, offset_samples):
        self.calls.append((sample_rate, offset_samples))
        return ["segment"]


def test_lazy_backend_loads_model_on_first_window():
    loader = FakeLoader(loads=True)
    backend = LazyModelBackend(loader, "load failed")

    assert backend.transcribe_window(b"", 16_000, 0) == ["segment"]
    assert loader.is_loaded()
    assert loader.calls == [(16_000, 0)]


def test_lazy_backend_raises_with_diagnostics_when_load_fails():
    backend = LazyModelBackend(FakeLoader(loads=False), "load failed")

    with pytest.raises(RuntimeError, match="load failed: no weights"):
        backend.transcribe_window(b"", 16_000, 0)


def test_gui_no_longer_defines_private_backend():
    import src.gui.live_mixin as live_mixin

    assert not hasattr(live_mixin, "_LiveModelBackend")
    assert live_mixin.LazyModelBackend is LazyModelBackend
