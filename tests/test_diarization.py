"""Tests for ML-backed diarization progress fallback behaviour."""

from src.utils.diarization import DiarizationManager


class _FakePipelineWithHook:
    def __init__(self):
        self.calls = []

    def __call__(self, path, hook=None, **_kwargs):
        self.calls.append((path, _kwargs))
        if hook is not None:
            hook("segmentation", None, file={"audio": path}, completed=2, total=4)
        return _FakeResult()


class _FakePipelineWithoutHook:
    def __call__(self, path, **_kwargs):
        return _FakeResult()


class _FakeResult:
    def itertracks(self, yield_label=True):
        return iter([])


def test_diarization_manager_detects_hook_support():
    manager = DiarizationManager(hf_token="hf_dummy", device="cpu")
    manager._pipeline = _FakePipelineWithHook()

    assert manager._supports_hook(manager._pipeline) is True


def test_diarization_manager_runs_hook_when_supported():
    manager = DiarizationManager(hf_token="hf_dummy", device="cpu")
    manager._pipeline = _FakePipelineWithHook()
    events = []

    manager._run_pipeline("/tmp/audio.wav", {}, lambda *args: events.append(args))

    # A pyannote hook reports one internal step, not the whole pipeline.
    assert events == [(None, 2.0, 4.0)]


def test_diarization_manager_detects_hook_on_pipeline_apply():
    class Pipeline:
        def __call__(self, path, **kwargs):
            return self.apply(path, **kwargs)

        def apply(self, path, hook=None):
            return _FakeResult()

    assert DiarizationManager._supports_hook(Pipeline()) is True


def test_diarization_manager_runs_without_hook_when_unsupported():
    manager = DiarizationManager(hf_token="hf_dummy", device="cpu")
    manager._pipeline = _FakePipelineWithoutHook()
    called = []

    manager._run_pipeline("/tmp/audio.wav", {"min_speakers": 1}, lambda *args: called.append(args))

    assert called == []
