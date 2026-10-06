import threading
import time

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


class SlowLoader(FakeLoader):
    def __init__(self):
        super().__init__(loads=True)
        self.loads = 0

    def load_model(self, logger=None):
        self.loads += 1
        time.sleep(0.05)
        return super().load_model(logger)


def test_warm_up_loads_the_model_and_runs_one_decode():
    loader = SlowLoader()
    backend = LazyModelBackend(loader, "load failed")

    backend.warm_up()

    assert loader.loads == 1
    assert len(loader.calls) == 1


def test_warm_up_failure_is_left_for_the_first_window_to_report():
    backend = LazyModelBackend(FakeLoader(loads=False), "load failed")

    backend.warm_up()

    with pytest.raises(RuntimeError, match="load failed"):
        backend.transcribe_window(b"", 16_000, 0)


def test_concurrent_first_windows_load_the_model_once():
    """Mic and system schedulers share one backend; both used to start a load."""
    loader = SlowLoader()
    backend = LazyModelBackend(loader, "load failed")
    threads = [threading.Thread(target=backend.transcribe_window, args=(b"", 16_000, 0)) for _ in range(2)]
    for thread in threads:
        thread.start()
    backend.warm_up()
    for thread in threads:
        thread.join()

    assert loader.loads == 1


def test_gui_no_longer_defines_private_backend():
    import src.gui.live_mixin as live_mixin

    assert not hasattr(live_mixin, "_LiveModelBackend")
    assert live_mixin.LazyModelBackend is LazyModelBackend
