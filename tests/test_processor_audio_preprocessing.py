from __future__ import annotations

from src.core.processor import TranscriptionProcessor
from src.utils.audio_preprocessing import (
    AudioPreprocessingReport,
    AudioQualityMetrics,
    PreprocessedAudio,
    PreprocessingDecision,
)


class _ModelLoader:
    def __init__(self):
        self.paths: list[str] = []

    def transcribe_longform(self, path, progress_callback=None):
        self.paths.append(path)
        if progress_callback:
            progress_callback(1.0, 1.0, 1.0)
        return [{"transcription": "тест", "boundaries": (0.0, 1.0)}]


class _Stats:
    pass


class _Converter:
    def __init__(self, canonical_path: str):
        self.canonical_path = canonical_path

    def convert_to_wav(self, *_args, progress_callback=None, **_kwargs):
        if progress_callback:
            progress_callback(1.0)
        return self.canonical_path


class _Preprocessor:
    def __init__(self, prepared: PreprocessedAudio):
        self.prepared = prepared
        self.calls: list[tuple[str, str, str]] = []

    def prepare(self, canonical_path: str, output_dir: str, *, mode: str):
        self.calls.append((canonical_path, output_dir, mode))
        return self.prepared


def _metrics() -> AudioQualityMetrics:
    return AudioQualityMetrics(
        duration_seconds=1.0,
        sample_rate=16000,
        channels=1,
        rms_dbfs=-20.0,
        peak_dbfs=-3.0,
        noise_floor_dbfs=-34.0,
        estimated_snr_db=12.0,
        clipping_ratio=0.0,
        silence_ratio=0.1,
        dc_offset=0.0,
        spectral_flatness=0.5,
        low_frequency_ratio=0.03,
        analyzed_fraction=1.0,
    )


def _prepared(asr_path: str, diarization_path: str) -> PreprocessedAudio:
    decision = PreprocessingDecision(
        action="light_cleanup",
        confidence=0.8,
        reasons=("moderate noise",),
        use_neural=False,
    )
    report = AudioPreprocessingReport(
        mode="auto",
        metrics=_metrics(),
        decision=decision,
        applied=True,
        backend="ffmpeg",
        elapsed_seconds=0.1,
    )
    return PreprocessedAudio(
        asr_path=asr_path,
        diarization_path=diarization_path,
        temporary_paths=(asr_path,),
        report=report,
    )


def test_processor_routes_enhanced_audio_to_asr_and_canonical_to_diarization(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    canonical = tmp_path / "canonical.wav"
    canonical.write_bytes(b"canonical")
    enhanced = tmp_path / "enhanced.wav"
    enhanced.write_bytes(b"enhanced")

    model = _ModelLoader()
    processor = TranscriptionProcessor(model, _Stats(), logger=lambda _message: None)
    processor.audio_converter = _Converter(str(canonical))
    fake_preprocessor = _Preprocessor(_prepared(str(enhanced), str(canonical)))
    processor.audio_preprocessor = fake_preprocessor
    diarization_paths: list[str] = []

    def fake_diarization(audio_path, utterances, **_kwargs):
        diarization_paths.append(audio_path)
        return [{**utterances[0], "speaker": "SPEAKER_00"}]

    monkeypatch.setattr(processor, "_apply_diarization", fake_diarization)
    monkeypatch.setattr("src.core.processor.AudioConverter.get_media_duration", lambda _path: 1.0)

    result = processor.process_file(
        str(source),
        str(tmp_path),
        0,
        1,
        enable_diarization=True,
        diarization_backend="sortformer",
        audio_preprocessing_mode="auto",
    )

    assert result["success"] is True
    assert model.paths == [str(enhanced)]
    assert diarization_paths == [str(canonical)]
    assert fake_preprocessor.calls == [(str(canonical), str(tmp_path), "auto")]
    assert result["audio_preprocessing"]["decision"]["action"] == "light_cleanup"
    assert not canonical.exists()
    assert not enhanced.exists()


def test_processor_default_mode_remains_off_and_backward_compatible(monkeypatch, tmp_path):
    source = tmp_path / "source.wav"
    source.write_bytes(b"source")
    canonical = tmp_path / "canonical.wav"
    canonical.write_bytes(b"canonical")

    model = _ModelLoader()
    processor = TranscriptionProcessor(model, _Stats(), logger=lambda _message: None)
    processor.audio_converter = _Converter(str(canonical))
    decision = PreprocessingDecision(
        action="none",
        confidence=1.0,
        reasons=("disabled",),
        use_neural=False,
    )
    report = AudioPreprocessingReport(
        mode="off",
        metrics=None,
        decision=decision,
        applied=False,
    )
    fake_preprocessor = _Preprocessor(
        PreprocessedAudio(str(canonical), str(canonical), (), report)
    )
    processor.audio_preprocessor = fake_preprocessor
    monkeypatch.setattr("src.core.processor.AudioConverter.get_media_duration", lambda _path: 1.0)

    result = processor.process_file(str(source), str(tmp_path), 0, 1)

    assert result["success"] is True
    assert model.paths == [str(canonical)]
    assert fake_preprocessor.calls == [(str(canonical), str(tmp_path), "off")]
    assert result["audio_preprocessing"]["mode"] == "off"
    assert not canonical.exists()
