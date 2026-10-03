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


class _Turn:
    def __init__(self, start, end):
        self.start = start
        self.end = end


class _Annotation:
    def __init__(self, tracks):
        self._tracks = tracks

    def itertracks(self, yield_label=True):
        for start, end, label in self._tracks:
            yield _Turn(start, end), None, label


class _DiarizeOutput:
    """Форма результата pyannote.audio 4.x (dataclass DiarizeOutput)."""

    def __init__(self, regular, exclusive):
        self.speaker_diarization = regular
        self.exclusive_speaker_diarization = exclusive
        self.speaker_embeddings = None


class _Pipeline4:
    def __init__(self, output):
        self.output = output

    def __call__(self, path, **_kwargs):
        return self.output


def test_pyannote4_diarize_output_uses_exclusive_annotation_for_word_mapping():
    # pyannote 4 возвращает DiarizeOutput вместо Annotation; прежде это
    # падало «ожидался pyannote.core.Annotation», и диаризация не работала.
    regular = _Annotation([(0.0, 2.0, "SPEAKER_00"), (1.5, 3.0, "SPEAKER_01")])
    exclusive = _Annotation([(0.0, 1.5, "SPEAKER_00"), (1.5, 3.0, "SPEAKER_01")])
    manager = DiarizationManager(hf_token="hf_dummy", device="cpu")
    manager._pipeline = _Pipeline4(_DiarizeOutput(regular, exclusive))

    segments = manager.diarize("/tmp/audio.wav")

    assert [(s.start, s.end, s.speaker) for s in segments] == [
        (0.0, 1.5, "Спикер №1"),
        (1.5, 3.0, "Спикер №2"),
    ]


def test_pyannote4_output_without_exclusive_annotation_uses_regular_one():
    regular = _Annotation([(0.0, 2.0, "SPEAKER_00")])
    manager = DiarizationManager(hf_token="hf_dummy", device="cpu")
    manager._pipeline = _Pipeline4(_DiarizeOutput(regular, None))

    segments = manager.diarize("/tmp/audio.wav")

    assert [(s.start, s.end, s.speaker) for s in segments] == [(0.0, 2.0, "Спикер №1")]


def test_unknown_pipeline_output_keeps_clear_error():
    import pytest

    manager = DiarizationManager(hf_token="hf_dummy", device="cpu")
    manager._pipeline = _Pipeline4(object())

    with pytest.raises(ValueError, match="Annotation"):
        manager.diarize("/tmp/audio.wav")
