from __future__ import annotations

import json
import math
import shutil
import wave
from dataclasses import replace
from pathlib import Path

import numpy as np

from src.utils.audio_preprocessing import (
    AudioPreprocessingPolicy,
    AudioPreprocessor,
    AudioQualityAnalyzer,
    AudioQualityMetrics,
    FFmpegAudioPreprocessingBackend,
    PreprocessingDecision,
    normalize_preprocessing_mode,
)


def _metrics(**overrides) -> AudioQualityMetrics:
    base = AudioQualityMetrics(
        duration_seconds=60.0,
        sample_rate=16000,
        channels=1,
        rms_dbfs=-19.0,
        peak_dbfs=-3.0,
        noise_floor_dbfs=-48.0,
        estimated_snr_db=29.0,
        clipping_ratio=0.0,
        silence_ratio=0.12,
        dc_offset=0.0,
        spectral_flatness=0.12,
        low_frequency_ratio=0.03,
        analyzed_fraction=1.0,
    )
    return replace(base, **overrides)


def _write_pcm16(path: Path, samples: np.ndarray, sample_rate: int = 16000, channels: int = 1) -> None:
    clipped = np.clip(samples, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm.tobytes())


def test_normalize_preprocessing_mode_preserves_supported_values_and_aliases():
    assert normalize_preprocessing_mode(" AUTO ") == "auto"
    assert normalize_preprocessing_mode("disabled") == "off"
    assert normalize_preprocessing_mode("neural") == "denoise"


def test_normalize_preprocessing_mode_rejects_unknown_value():
    try:
        normalize_preprocessing_mode("studio-magic")
    except ValueError as exc:
        assert "studio-magic" in str(exc)
    else:
        raise AssertionError("unknown mode must be rejected")


def test_auto_policy_leaves_clean_audio_untouched():
    decision = AudioPreprocessingPolicy().decide(_metrics(), neural_available=True)

    assert decision.action == "none"
    assert decision.confidence >= 0.8
    assert any("clean" in reason.lower() for reason in decision.reasons)


def test_auto_policy_normalizes_audio_that_is_only_too_quiet():
    decision = AudioPreprocessingPolicy().decide(
        _metrics(rms_dbfs=-37.0, peak_dbfs=-15.0, estimated_snr_db=31.0),
        neural_available=True,
    )

    assert decision.action == "normalize"
    assert decision.use_neural is False


def test_auto_policy_does_not_amplify_quiet_audio_with_uncertain_snr():
    decision = AudioPreprocessingPolicy().decide(
        _metrics(
            rms_dbfs=-39.0,
            estimated_snr_db=12.0,
            noise_floor_dbfs=-55.0,
            spectral_flatness=0.02,
        ),
        neural_available=True,
    )

    assert decision.action == "none"
    assert decision.refused is True


def test_auto_policy_uses_light_cleanup_for_moderate_noise():
    decision = AudioPreprocessingPolicy().decide(
        _metrics(
            rms_dbfs=-22.0,
            noise_floor_dbfs=-34.0,
            estimated_snr_db=12.0,
            spectral_flatness=0.46,
            low_frequency_ratio=0.18,
        ),
        neural_available=True,
    )

    assert decision.action == "light_cleanup"
    assert decision.use_neural is False
    assert len(decision.reasons) >= 1


def test_auto_policy_uses_neural_denoise_only_for_severe_noise_when_available():
    decision = AudioPreprocessingPolicy().decide(
        _metrics(
            rms_dbfs=-23.0,
            noise_floor_dbfs=-29.0,
            estimated_snr_db=6.0,
            spectral_flatness=0.68,
        ),
        neural_available=True,
    )

    assert decision.action == "neural_denoise"
    assert decision.use_neural is True


def test_auto_policy_falls_back_to_light_cleanup_without_neural_backend():
    decision = AudioPreprocessingPolicy().decide(
        _metrics(
            noise_floor_dbfs=-29.0,
            estimated_snr_db=6.0,
            spectral_flatness=0.68,
        ),
        neural_available=False,
    )

    assert decision.action == "light_cleanup"
    assert decision.fallback is True
    assert any("unavailable" in reason.lower() for reason in decision.reasons)


def test_auto_policy_refuses_processing_when_clipping_is_severe():
    decision = AudioPreprocessingPolicy().decide(
        _metrics(clipping_ratio=0.04, peak_dbfs=0.0, estimated_snr_db=8.0),
        neural_available=True,
    )

    assert decision.action == "none"
    assert decision.refused is True
    assert any("clipping" in reason.lower() for reason in decision.reasons)


def test_auto_policy_refuses_processing_when_there_is_almost_no_detected_signal():
    decision = AudioPreprocessingPolicy().decide(
        _metrics(rms_dbfs=-70.0, peak_dbfs=-55.0, silence_ratio=0.99, estimated_snr_db=0.0),
        neural_available=True,
    )

    assert decision.action == "none"
    assert decision.refused is True


def test_decision_and_metrics_are_json_safe():
    metrics = _metrics()
    decision = PreprocessingDecision(
        action="none",
        confidence=0.9,
        reasons=("clean input",),
        use_neural=False,
    )

    payload = {"metrics": metrics.to_dict(), "decision": decision.to_dict()}
    roundtrip = json.loads(json.dumps(payload, ensure_ascii=False))

    assert roundtrip["decision"]["action"] == "none"
    assert math.isfinite(roundtrip["metrics"]["estimated_snr_db"])


def test_analyzer_measures_pcm_wav_without_loading_external_models(tmp_path):
    sample_rate = 16000
    t = np.arange(sample_rate * 2, dtype=np.float32) / sample_rate
    samples = 0.25 * np.sin(2.0 * np.pi * 440.0 * t)
    path = tmp_path / "tone.wav"
    _write_pcm16(path, samples, sample_rate=sample_rate)

    metrics = AudioQualityAnalyzer().analyze(str(path))

    assert metrics.sample_rate == sample_rate
    assert metrics.channels == 1
    assert 1.99 <= metrics.duration_seconds <= 2.01
    assert -17.0 < metrics.rms_dbfs < -13.0
    assert metrics.clipping_ratio == 0.0
    assert metrics.analyzed_fraction == 1.0


def test_analyzer_detects_silence_and_clipping(tmp_path):
    sample_rate = 16000
    silence = np.zeros(sample_rate, dtype=np.float32)
    clipped = np.ones(sample_rate, dtype=np.float32)
    path = tmp_path / "damaged.wav"
    _write_pcm16(path, np.concatenate([silence, clipped]), sample_rate=sample_rate)

    metrics = AudioQualityAnalyzer().analyze(str(path))

    assert 0.45 <= metrics.silence_ratio <= 0.55
    assert 0.45 <= metrics.clipping_ratio <= 0.55


def test_analyzer_rejects_empty_or_unsupported_audio(tmp_path):
    path = tmp_path / "empty.wav"
    path.write_bytes(b"")

    try:
        AudioQualityAnalyzer().analyze(str(path))
    except ValueError as exc:
        assert "audio" in str(exc).lower() or "wav" in str(exc).lower()
    else:
        raise AssertionError("empty input must be rejected")


class _FakeAnalyzer:
    def __init__(
        self,
        metrics: AudioQualityMetrics,
        processed_metrics: AudioQualityMetrics | None = None,
    ):
        self.metrics = metrics
        self.processed_metrics = processed_metrics or metrics
        self.calls: list[str] = []

    def analyze(self, path: str) -> AudioQualityMetrics:
        self.calls.append(path)
        return self.metrics if len(self.calls) == 1 else self.processed_metrics


class _FakeDspBackend:
    def __init__(self, output: str | None):
        self.output = output
        self.calls: list[tuple[str, str, str]] = []

    def process(self, input_path: str, output_dir: str, action: str) -> str | None:
        self.calls.append((input_path, output_dir, action))
        return self.output


class _FakeNeuralBackend:
    def __init__(self, *, available: bool, output: str | None):
        self.available = available
        self.output = output
        self.calls: list[tuple[str, str]] = []

    def is_available(self) -> bool:
        return self.available

    def process(self, input_path: str, output_dir: str) -> str | None:
        self.calls.append((input_path, output_dir))
        return self.output


def test_preprocessor_off_preserves_legacy_track_without_analysis(tmp_path):
    analyzer = _FakeAnalyzer(_metrics())
    dsp = _FakeDspBackend(str(tmp_path / "unused.wav"))
    preprocessor = AudioPreprocessor(analyzer=analyzer, dsp_backend=dsp)

    result = preprocessor.prepare("canonical.wav", str(tmp_path), mode="off")

    assert result.asr_path == "canonical.wav"
    assert result.diarization_path == "canonical.wav"
    assert result.temporary_paths == ()
    assert result.report.decision.action == "none"
    assert analyzer.calls == []
    assert dsp.calls == []


def test_preprocessor_auto_routes_only_asr_through_selected_cleanup(tmp_path):
    enhanced = str(tmp_path / "enhanced.wav")
    source_metrics = _metrics(estimated_snr_db=12.0, spectral_flatness=0.46)
    improved_metrics = _metrics(
        estimated_snr_db=25.0,
        noise_floor_dbfs=-45.0,
        spectral_flatness=0.12,
    )
    analyzer = _FakeAnalyzer(source_metrics, improved_metrics)
    dsp = _FakeDspBackend(enhanced)
    preprocessor = AudioPreprocessor(analyzer=analyzer, dsp_backend=dsp)

    result = preprocessor.prepare("canonical.wav", str(tmp_path), mode="auto")

    assert result.asr_path == enhanced
    assert result.diarization_path == "canonical.wav"
    assert result.temporary_paths == (enhanced,)
    assert result.report.decision.action == "light_cleanup"
    assert result.report.processed_metrics == improved_metrics
    assert dsp.calls == [("canonical.wav", str(tmp_path), "light_cleanup")]


def test_preprocessor_auto_keeps_clean_audio_as_single_track(tmp_path):
    preprocessor = AudioPreprocessor(
        analyzer=_FakeAnalyzer(_metrics()),
        dsp_backend=_FakeDspBackend(str(tmp_path / "unused.wav")),
    )

    result = preprocessor.prepare("canonical.wav", str(tmp_path), mode="auto")

    assert result.asr_path == result.diarization_path == "canonical.wav"
    assert result.temporary_paths == ()
    assert result.report.applied is False


def test_preprocessor_uses_neural_backend_for_severe_noise(tmp_path):
    enhanced = str(tmp_path / "neural.wav")
    neural = _FakeNeuralBackend(available=True, output=enhanced)
    preprocessor = AudioPreprocessor(
        analyzer=_FakeAnalyzer(
            _metrics(estimated_snr_db=5.0, spectral_flatness=0.7),
            _metrics(
                estimated_snr_db=26.0,
                noise_floor_dbfs=-46.0,
                spectral_flatness=0.08,
            ),
        ),
        dsp_backend=_FakeDspBackend(None),
        neural_backend=neural,
    )

    result = preprocessor.prepare("canonical.wav", str(tmp_path), mode="auto")

    assert result.asr_path == enhanced
    assert result.diarization_path == "canonical.wav"
    assert result.report.decision.use_neural is True
    assert neural.calls == [("canonical.wav", str(tmp_path))]


def test_preprocessor_falls_back_to_canonical_when_processing_fails(tmp_path):
    preprocessor = AudioPreprocessor(
        analyzer=_FakeAnalyzer(_metrics(estimated_snr_db=12.0, spectral_flatness=0.46)),
        dsp_backend=_FakeDspBackend(None),
    )

    result = preprocessor.prepare("canonical.wav", str(tmp_path), mode="auto")

    assert result.asr_path == result.diarization_path == "canonical.wav"
    assert result.report.applied is False
    assert result.report.runtime_fallback is True
    assert result.temporary_paths == ()


def test_preprocessor_quality_gate_rejects_candidate_that_damages_speech(tmp_path):
    damaged_path = tmp_path / "damaged.wav"
    damaged_path.write_bytes(b"candidate")
    source_metrics = _metrics(estimated_snr_db=12.0, spectral_flatness=0.46)
    damaged_metrics = _metrics(
        estimated_snr_db=4.0,
        spectral_flatness=0.70,
        clipping_ratio=0.08,
        silence_ratio=0.75,
    )
    preprocessor = AudioPreprocessor(
        analyzer=_FakeAnalyzer(source_metrics, damaged_metrics),
        dsp_backend=_FakeDspBackend(str(damaged_path)),
    )

    result = preprocessor.prepare("canonical.wav", str(tmp_path), mode="auto")

    assert result.asr_path == result.diarization_path == "canonical.wav"
    assert result.report.applied is False
    assert result.report.runtime_fallback is True
    assert "quality gate" in (result.report.fallback_reason or "").lower()
    assert result.report.processed_metrics == damaged_metrics
    assert not damaged_path.exists()


def test_ffmpeg_backend_uses_argument_list_and_preserves_timeline(monkeypatch, tmp_path):
    source = tmp_path / "canonical.wav"
    _write_pcm16(source, np.zeros(16000, dtype=np.float32))
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        shutil.copyfile(source, command[-1])
        return type("Result", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr("src.utils.audio_preprocessing._find_ffmpeg", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr("src.utils.audio_preprocessing.subprocess.run", fake_run)

    backend = FFmpegAudioPreprocessingBackend(logger=lambda _message: None)
    output = backend.process(str(source), str(tmp_path), "light_cleanup")

    command = captured["command"]
    assert isinstance(command, list)
    assert command[0] == "/usr/bin/ffmpeg"
    assert "-af" in command
    assert "afftdn" in command[command.index("-af") + 1]
    assert captured["kwargs"].get("shell") is None
    assert output is not None
    assert Path(output).exists()


def test_ffmpeg_backend_rejects_duration_drift_and_removes_output(monkeypatch, tmp_path):
    source = tmp_path / "canonical.wav"
    _write_pcm16(source, np.zeros(16000, dtype=np.float32))
    created: list[Path] = []

    def fake_run(command, **_kwargs):
        target = Path(command[-1])
        created.append(target)
        _write_pcm16(target, np.zeros(8000, dtype=np.float32))
        return type("Result", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr("src.utils.audio_preprocessing._find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr("src.utils.audio_preprocessing.subprocess.run", fake_run)

    output = FFmpegAudioPreprocessingBackend(logger=lambda _message: None).process(
        str(source), str(tmp_path), "normalize"
    )

    assert output is None
    assert created and not created[0].exists()
