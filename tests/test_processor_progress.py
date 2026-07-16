"""Progress orchestration tests for the transcription processor."""

from pathlib import Path

from src.core.processor import TranscriptionProcessor
from src.core.progress import ProgressEvent


class DummyStats:
    def estimate_processing_time(self, filepath, media_duration):
        return 0.0


class DummyLoader:
    def __init__(self, events):
        self.events = events

    def transcribe_longform(self, audio_path, progress_callback=None):
        for value in self.events:
            if progress_callback:
                progress_callback(value, value, value)
        return [{"transcription": "hi", "boundaries": (0.0, 1.0)}]


class DummyLoaderRegression:
    def transcribe_longform(self, audio_path, progress_callback=None):
        if progress_callback:
            progress_callback(0.6, 6.0, 10.0)
            progress_callback(0.2, 2.0, 10.0)
        return [{"transcription": "hi", "boundaries": (0.0, 1.0)}]


class DummyLoaderFailure:
    def transcribe_longform(self, audio_path, progress_callback=None):
        raise RuntimeError("boom")


class DummyLoaderEmpty:
    def transcribe_longform(self, audio_path, progress_callback=None):
        if progress_callback:
            progress_callback(0.75, 3.0, 4.0)
        return []


class DummyDiarizationManager:
    def __init__(self):
        self.calls = []

    def diarize(self, audio_path, num_speakers=None, progress_callback=None):
        self.calls.append(audio_path)
        if progress_callback:
            progress_callback(None, None, None)
            progress_callback(0.2, 1.0, 5.0)
            progress_callback(1.0, 5.0, 5.0)
        return []

    def map_speakers_to_transcription(self, utterances, speaker_segments):
        for item in utterances:
            item["speaker"] = "Спикер №1"
        return utterances


class DummyLoaderWithValue:
    def transcribe_longform(self, audio_path, progress_callback=None):
        if progress_callback:
            progress_callback(0.33, 0.0, 0.0)
            progress_callback(0.66, 2.0, 3.0)
            progress_callback(1.0, 3.0, 3.0)
        return [{"transcription": "x", "boundaries": (0.0, 3.0)}]


def _prepare_inputs(tmp_path: Path) -> Path:
    path = tmp_path / "in.wav"
    path.write_bytes(b"stub")
    return path


def _run_process(processor, path, output_formats=None, enable_diarization=False, loader=None):
    events: list[ProgressEvent] = []
    output_formats = output_formats or ["txt"]

    def progress_cb(event: ProgressEvent):
        events.append(event)

    if loader is not None:
        processor.model_loader = loader

    processor.progress_callback = progress_cb
    result = processor.process_file(
        filepath=str(path),
        output_dir=str(path.parent),
        file_index=0,
        total_files=1,
        original_filename=path.name,
        output_formats=output_formats,
        enable_diarization=enable_diarization,
    )
    return result, events


def _collect_stage_progress(events, stage):
    return [ev.file_progress for ev in events if ev.stage == stage]


def _collect_events(events, stage):
    return [ev for ev in events if ev.stage == stage]


def test_processor_progress_without_diarization(monkeypatch, tmp_path):
    path = _prepare_inputs(tmp_path)
    processor = TranscriptionProcessor(DummyLoader([0.0, 0.5, 1.0]), DummyStats())

    monkeypatch.setattr(processor.audio_converter, "convert_to_wav", lambda *args, **kwargs: str(path))
    monkeypatch.setattr("src.core.processor.AudioConverter.get_media_duration", lambda _path: 10.0)

    result, events = _run_process(processor, path, output_formats=["txt", "md"])

    assert result["success"]
    assert [ev.stage for ev in events][:2] == ["preparing", "conversion"]
    assert "export" in [ev.stage for ev in events]
    assert "finalizing" == events[-1].stage
    assert events[-1].file_progress == 1.0
    assert _collect_stage_progress(events, "conversion")[-1] == 0.12
    assert _collect_stage_progress(events, "preprocessing")[-1] == 0.15
    assert _collect_stage_progress(events, "transcription")[-1] >= 0.95
    assert _collect_stage_progress(events, "finalizing")[-1] == 1.0

    file_progress = [ev.file_progress for ev in events]
    assert file_progress == sorted(file_progress)


def test_processor_passes_probed_duration_to_converter(monkeypatch, tmp_path):
    path = _prepare_inputs(tmp_path)
    processor = TranscriptionProcessor(DummyLoader([1.0]), DummyStats())
    captured = {}

    def convert(*args, **kwargs):
        captured.update(kwargs)
        return str(path)

    monkeypatch.setattr(processor.audio_converter, "convert_to_wav", convert)
    monkeypatch.setattr("src.core.processor.AudioConverter.get_media_duration", lambda _path: 42.0)

    result, _events = _run_process(processor, path)

    assert result["success"]
    assert captured["media_duration"] == 42.0


def test_processor_progress_with_diarization(monkeypatch, tmp_path):
    path = _prepare_inputs(tmp_path)
    processor = TranscriptionProcessor(DummyLoaderWithValue(), DummyStats())
    processor._diarization_manager = DummyDiarizationManager()

    monkeypatch.setattr(processor.audio_converter, "convert_to_wav", lambda *args, **kwargs: str(path))
    monkeypatch.setattr("src.core.processor.AudioConverter.get_media_duration", lambda _path: 10.0)

    result, events = _run_process(processor, path, output_formats=["txt"], enable_diarization=True)

    assert result["success"]
    assert _collect_events(events, "diarization")
    assert _collect_stage_progress(events, "diarization")
    assert any(ev.stage_progress is None for ev in _collect_events(events, "diarization"))
    assert max(_collect_stage_progress(events, "diarization")) <= 1.0

    stages = [ev.stage for ev in events]
    assert stages.index("conversion") < stages.index("transcription") < stages.index("diarization") < stages.index("export")


def test_processor_transcription_progress_regression_is_clamped_to_monotonic(monkeypatch, tmp_path):
    path = _prepare_inputs(tmp_path)
    processor = TranscriptionProcessor(DummyLoaderRegression(), DummyStats())

    monkeypatch.setattr(processor.audio_converter, "convert_to_wav", lambda *args, **kwargs: str(path))
    monkeypatch.setattr("src.core.processor.AudioConverter.get_media_duration", lambda _path: 10.0)

    result, events = _run_process(processor, path)

    assert result["success"]

    trans = _collect_stage_progress(events, "transcription")
    assert trans == sorted(trans)
    assert trans[0] > 0.15


def test_processor_failure_does_not_emit_completion_progress(monkeypatch, tmp_path):
    path = _prepare_inputs(tmp_path)
    processor = TranscriptionProcessor(DummyLoaderFailure(), DummyStats())

    monkeypatch.setattr(processor.audio_converter, "convert_to_wav", lambda *args, **kwargs: str(path))
    monkeypatch.setattr("src.core.processor.AudioConverter.get_media_duration", lambda _path: 10.0)

    result, events = _run_process(processor, path, loader=DummyLoaderFailure())

    assert not result["success"]
    assert all(ev.file_progress < 1.0 for ev in events)


def test_processor_empty_speech_exports_and_finishes(monkeypatch, tmp_path):
    path = _prepare_inputs(tmp_path)
    processor = TranscriptionProcessor(DummyLoaderEmpty(), DummyStats())

    monkeypatch.setattr(processor.audio_converter, "convert_to_wav", lambda *args, **kwargs: str(path))
    monkeypatch.setattr("src.core.processor.AudioConverter.get_media_duration", lambda _path: 5.0)

    result, events = _run_process(processor, path, output_formats=["txt"])

    assert result["success"]
    assert result["saved_files"]
    assert events[-1].file_progress == 1.0


def test_processor_multiple_output_formats_progresses_export_deterministically(monkeypatch, tmp_path):
    path = _prepare_inputs(tmp_path)
    processor = TranscriptionProcessor(DummyLoader([0.5]), DummyStats())

    monkeypatch.setattr(processor.audio_converter, "convert_to_wav", lambda *args, **kwargs: str(path))
    monkeypatch.setattr("src.core.processor.AudioConverter.get_media_duration", lambda _path: 10.0)

    result, events = _run_process(processor, path, output_formats=["txt", "txt_timecodes", "md"])

    export_values = _collect_stage_progress(events, "export")
    assert export_values == sorted(export_values)
    assert export_values[0] == 0.95
    assert export_values[1:] == [0.9633333333333333, 0.9766666666666667, 0.99]
    assert all(value < 1.0 for value in export_values)
    assert result["success"]
