"""Интеллектуальная диагностика качества аудио и консервативная policy очистки.

Модуль намеренно не импортирует torch и необязательные ML-backend'ы. Анализ
выполняется по репрезентативным окнам WAV и пригоден для длинных записей без
загрузки файла целиком в память.
"""

from __future__ import annotations

import math
import os
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Literal, Protocol

import numpy as np
import soundfile as sf

from ..config import AUDIO_CHANNELS, AUDIO_SAMPLE_RATE
from .audio_converter import _find_ffmpeg, _windows_startupinfo

PreprocessingMode = Literal["off", "auto", "light", "denoise"]
PreprocessingAction = Literal["none", "normalize", "light_cleanup", "neural_denoise"]

_MODE_ALIASES = {
    "disabled": "off",
    "disable": "off",
    "none": "off",
    "false": "off",
    "neural": "denoise",
    "deepfilter": "denoise",
    "deepfilternet": "denoise",
}
_SUPPORTED_MODES = {"off", "auto", "light", "denoise"}


def normalize_preprocessing_mode(value: str | None) -> PreprocessingMode:
    """Возвращает каноническое имя режима или сообщает об ошибке явно."""

    normalized = (value or "off").strip().lower()
    normalized = _MODE_ALIASES.get(normalized, normalized)
    if normalized not in _SUPPORTED_MODES:
        raise ValueError(f"Unsupported audio preprocessing mode: {value!r}")
    return normalized  # type: ignore[return-value]


def _finite_float(value: float, *, default: float = 0.0) -> float:
    numeric = float(value)
    return numeric if math.isfinite(numeric) else default


@dataclass(frozen=True)
class AudioQualityMetrics:
    """Измеримые признаки входного аудио, используемые policy."""

    duration_seconds: float
    sample_rate: int
    channels: int
    rms_dbfs: float
    peak_dbfs: float
    noise_floor_dbfs: float
    estimated_snr_db: float
    clipping_ratio: float
    silence_ratio: float
    dc_offset: float
    spectral_flatness: float
    low_frequency_ratio: float
    analyzed_fraction: float

    def to_dict(self) -> dict[str, float | int]:
        payload = asdict(self)
        for key, value in payload.items():
            if isinstance(value, float):
                payload[key] = round(_finite_float(value), 6)
        return payload


@dataclass(frozen=True)
class PreprocessingDecision:
    """Объяснимое решение о безопасном уровне обработки."""

    action: PreprocessingAction
    confidence: float
    reasons: tuple[str, ...]
    use_neural: bool
    fallback: bool = False
    refused: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "confidence": round(min(max(_finite_float(self.confidence), 0.0), 1.0), 6),
            "reasons": list(self.reasons),
            "use_neural": self.use_neural,
            "fallback": self.fallback,
            "refused": self.refused,
        }


@dataclass(frozen=True)
class AudioPreprocessingReport:
    """Serializable diagnostics and runtime outcome for one input file."""

    mode: PreprocessingMode
    metrics: AudioQualityMetrics | None
    decision: PreprocessingDecision
    applied: bool
    backend: str | None = None
    runtime_fallback: bool = False
    fallback_reason: str | None = None
    processed_metrics: AudioQualityMetrics | None = None
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "metrics": self.metrics.to_dict() if self.metrics else None,
            "decision": self.decision.to_dict(),
            "applied": self.applied,
            "backend": self.backend,
            "runtime_fallback": self.runtime_fallback,
            "fallback_reason": self.fallback_reason,
            "processed_metrics": (
                self.processed_metrics.to_dict() if self.processed_metrics else None
            ),
            "elapsed_seconds": round(max(_finite_float(self.elapsed_seconds), 0.0), 6),
        }


@dataclass(frozen=True)
class PreprocessedAudio:
    """Aligned tracks prepared for downstream models."""

    asr_path: str
    diarization_path: str
    temporary_paths: tuple[str, ...]
    report: AudioPreprocessingReport


class DspPreprocessingBackend(Protocol):
    def process(self, input_path: str, output_dir: str, action: str) -> str | None: ...


class NeuralPreprocessingBackend(Protocol):
    def is_available(self) -> bool: ...

    def process(self, input_path: str, output_dir: str) -> str | None: ...


@dataclass(frozen=True)
class AudioPreprocessingPolicy:
    """Детерминированная conservative policy с явными порогами."""

    severe_clipping_ratio: float = 0.02
    almost_silent_ratio: float = 0.97
    almost_silent_rms_dbfs: float = -60.0
    quiet_rms_dbfs: float = -32.0
    clean_snr_db: float = 22.0
    severe_noise_snr_db: float = 8.0
    moderate_flatness: float = 0.08
    severe_flatness: float = 0.18
    audible_noise_floor_dbfs: float = -50.0
    rumble_ratio: float = 0.12

    def decide(
        self,
        metrics: AudioQualityMetrics,
        *,
        neural_available: bool,
    ) -> PreprocessingDecision:
        """Выбирает минимальное воздействие; при сомнении оставляет исходник."""

        if metrics.clipping_ratio >= self.severe_clipping_ratio:
            return PreprocessingDecision(
                action="none",
                confidence=0.98,
                reasons=(
                    "Severe clipping detected; denoising cannot restore clipped speech safely",
                ),
                use_neural=False,
                refused=True,
            )

        if (
            metrics.silence_ratio >= self.almost_silent_ratio
            or metrics.rms_dbfs <= self.almost_silent_rms_dbfs
        ):
            return PreprocessingDecision(
                action="none",
                confidence=0.97,
                reasons=("Almost no analyzable speech signal; processing refused",),
                use_neural=False,
                refused=True,
            )

        severe_noise = (
            metrics.estimated_snr_db <= self.severe_noise_snr_db
            and metrics.spectral_flatness >= self.severe_flatness
        )
        if severe_noise:
            if neural_available:
                return PreprocessingDecision(
                    action="neural_denoise",
                    confidence=0.86,
                    reasons=("Severe broadband noise detected",),
                    use_neural=True,
                )
            return PreprocessingDecision(
                action="light_cleanup",
                confidence=0.66,
                reasons=(
                    "Severe broadband noise detected",
                    "Neural denoiser unavailable; using conservative FFmpeg cleanup",
                ),
                use_neural=False,
                fallback=True,
            )

        broadband_noise = (
            metrics.noise_floor_dbfs >= self.audible_noise_floor_dbfs
            and metrics.estimated_snr_db < self.clean_snr_db
            and metrics.spectral_flatness >= self.moderate_flatness
        )
        moderate_noise = broadband_noise or metrics.low_frequency_ratio >= self.rumble_ratio
        if moderate_noise:
            reasons: list[str] = []
            if broadband_noise:
                reasons.append("Reduced estimated signal-to-noise ratio")
                reasons.append("Broadband stationary noise signature")
            if metrics.low_frequency_ratio >= self.rumble_ratio:
                reasons.append("Low-frequency rumble signature")
            return PreprocessingDecision(
                action="light_cleanup",
                confidence=0.78,
                reasons=tuple(reasons),
                use_neural=False,
            )

        if metrics.rms_dbfs <= self.quiet_rms_dbfs:
            if metrics.estimated_snr_db < self.clean_snr_db:
                return PreprocessingDecision(
                    action="none",
                    confidence=0.74,
                    reasons=(
                        "Signal is quiet but estimated SNR is uncertain; amplification refused",
                    ),
                    use_neural=False,
                    refused=True,
                )
            return PreprocessingDecision(
                action="normalize",
                confidence=0.88,
                reasons=("Speech level is low while signal-to-noise ratio is healthy",),
                use_neural=False,
            )

        return PreprocessingDecision(
            action="none",
            confidence=0.93,
            reasons=("Input appears clean; enhancement is unnecessary",),
            use_neural=False,
        )


class AudioQualityAnalyzer:
    """Оценивает WAV по ограниченному набору репрезентативных окон."""

    def __init__(
        self,
        *,
        max_analysis_seconds: float = 120.0,
        window_seconds: float = 10.0,
        silence_threshold_dbfs: float = -50.0,
    ) -> None:
        self.max_analysis_seconds = max(float(max_analysis_seconds), 1.0)
        self.window_seconds = max(float(window_seconds), 0.25)
        self.silence_threshold = 10.0 ** (float(silence_threshold_dbfs) / 20.0)

    @staticmethod
    def _dbfs(value: float, *, floor: float = -120.0) -> float:
        if value <= 0.0 or not math.isfinite(value):
            return floor
        return max(20.0 * math.log10(value), floor)

    def _read_representative_audio(self, audio: sf.SoundFile) -> tuple[np.ndarray, float]:
        total_frames = len(audio)
        if total_frames <= 0 or audio.samplerate <= 0:
            raise ValueError("Audio file has no readable samples")

        max_frames = max(int(self.max_analysis_seconds * audio.samplerate), 1)
        window_frames = max(int(self.window_seconds * audio.samplerate), 1)
        if total_frames <= max_frames:
            audio.seek(0)
            data = audio.read(total_frames, dtype="float32", always_2d=True)
            return data, 1.0

        window_count = max(max_frames // window_frames, 1)
        max_start = max(total_frames - window_frames, 0)
        starts = np.linspace(0, max_start, num=window_count, dtype=np.int64)
        chunks: list[np.ndarray] = []
        for start in starts:
            audio.seek(int(start))
            chunk = audio.read(window_frames, dtype="float32", always_2d=True)
            if chunk.size:
                chunks.append(chunk)
        if not chunks:
            raise ValueError("Audio file has no analyzable samples")
        data = np.concatenate(chunks, axis=0)
        return data, min(data.shape[0] / total_frames, 1.0)

    @staticmethod
    def _frame_rms(samples: np.ndarray, frame_size: int) -> np.ndarray:
        usable = samples.size - (samples.size % frame_size)
        if usable <= 0:
            return np.asarray([float(np.sqrt(np.mean(np.square(samples), dtype=np.float64)))])
        framed = samples[:usable].reshape(-1, frame_size)
        return np.sqrt(np.mean(np.square(framed), axis=1, dtype=np.float64))

    def analyze(self, path: str) -> AudioQualityMetrics:
        try:
            with sf.SoundFile(path) as audio:
                sample_rate = int(audio.samplerate)
                channels = int(audio.channels)
                duration = len(audio) / sample_rate if sample_rate > 0 else 0.0
                data, analyzed_fraction = self._read_representative_audio(audio)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError(f"Unable to analyze audio/WAV file: {exc}") from exc

        mono = np.mean(data, axis=1, dtype=np.float32)
        if mono.size == 0:
            raise ValueError("Audio file has no analyzable samples")
        finite = np.nan_to_num(mono, nan=0.0, posinf=1.0, neginf=-1.0)
        absolute = np.abs(finite)
        rms = float(np.sqrt(np.mean(np.square(finite), dtype=np.float64)))
        peak = float(np.max(absolute))
        clipping_ratio = float(np.mean(absolute >= 0.999))
        silence_ratio = float(np.mean(absolute <= self.silence_threshold))
        dc_offset = float(np.mean(finite, dtype=np.float64))

        frame_size = max(int(sample_rate * 0.02), 1)
        frame_rms = self._frame_rms(finite, frame_size)
        if frame_rms.size:
            noise_rms = float(np.percentile(frame_rms, 20.0))
            speech_rms = float(np.percentile(frame_rms, 80.0))
        else:
            noise_rms = 0.0
            speech_rms = 0.0
        noise_floor_dbfs = self._dbfs(noise_rms)
        estimated_snr_db = min(max(self._dbfs(speech_rms) - noise_floor_dbfs, 0.0), 60.0)

        spectral_limit = max(sample_rate * 20, 4096)
        usable = finite.size - (finite.size % frame_size)
        spectral_samples = finite[: min(finite.size, spectral_limit)]
        if usable > 0:
            framed = finite[:usable].reshape(-1, frame_size)
            valid_noise_frames = (frame_rms > 1e-8) & (
                frame_rms <= np.percentile(frame_rms[frame_rms > 1e-8], 25.0)
            ) if np.any(frame_rms > 1e-8) else np.zeros(frame_rms.shape, dtype=bool)
            if np.any(valid_noise_frames) and noise_floor_dbfs > -60.0:
                spectral_samples = framed[valid_noise_frames].reshape(-1)[:spectral_limit]
        if spectral_samples.size < 2:
            spectral_flatness = 0.0
            low_frequency_ratio = 0.0
        else:
            window = np.hanning(spectral_samples.size).astype(np.float32)
            power = np.square(np.abs(np.fft.rfft(spectral_samples * window))).astype(np.float64)
            power = np.maximum(power, 1e-20)
            spectral_flatness = float(np.exp(np.mean(np.log(power))) / np.mean(power))
            frequencies = np.fft.rfftfreq(spectral_samples.size, d=1.0 / sample_rate)
            total_power = float(np.sum(power))
            low_frequency_ratio = (
                float(np.sum(power[frequencies <= 80.0])) / total_power
                if total_power > 0.0
                else 0.0
            )

        return AudioQualityMetrics(
            duration_seconds=_finite_float(duration),
            sample_rate=sample_rate,
            channels=channels,
            rms_dbfs=self._dbfs(rms),
            peak_dbfs=self._dbfs(peak),
            noise_floor_dbfs=noise_floor_dbfs,
            estimated_snr_db=_finite_float(estimated_snr_db),
            clipping_ratio=min(max(_finite_float(clipping_ratio), 0.0), 1.0),
            silence_ratio=min(max(_finite_float(silence_ratio), 0.0), 1.0),
            dc_offset=_finite_float(dc_offset),
            spectral_flatness=min(max(_finite_float(spectral_flatness), 0.0), 1.0),
            low_frequency_ratio=min(max(_finite_float(low_frequency_ratio), 0.0), 1.0),
            analyzed_fraction=min(max(_finite_float(analyzed_fraction), 0.0), 1.0),
        )


class FFmpegAudioPreprocessingBackend:
    """Conservative non-ML cleanup with strict timeline verification."""

    _FILTERS = {
        # A bounded linear gain is safer for ASR than dynamic loudnorm, which can
        # oversample internally and reshape dynamics. Auto chooses this only for
        # quiet, otherwise healthy recordings.
        "normalize": "volume=6dB",
        "light_cleanup": (
            "highpass=f=70,"
            "afftdn=nr=8:nf=-40:tn=1"
        ),
    }

    def __init__(
        self,
        logger=None,
        *,
        duration_tolerance_seconds: float = 2.0 / AUDIO_SAMPLE_RATE,
    ) -> None:
        self.logger = logger or (lambda _message: None)
        self.duration_tolerance_seconds = max(float(duration_tolerance_seconds), 0.0)

    @staticmethod
    def _duration(path: str) -> float:
        try:
            info = sf.info(path)
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"Cannot inspect processed WAV: {exc}") from exc
        if info.samplerate <= 0 or info.frames <= 0:
            raise ValueError("Processed WAV has no readable samples")
        return float(info.frames) / float(info.samplerate)

    @staticmethod
    def _remove_partial(path: str) -> None:
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

    def process(self, input_path: str, output_dir: str, action: str) -> str | None:
        filters = self._FILTERS.get(action)
        if filters is None:
            self.logger(f"Audio cleanup skipped: unsupported FFmpeg action {action!r}")
            return None

        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(
            output_dir,
            f"temp_preprocessed_{uuid.uuid4().hex}.wav",
        )
        try:
            source_duration = self._duration(input_path)
            timeout = max(120.0, min(7200.0, source_duration * 3.0 + 60.0))
            command = [
                _find_ffmpeg(),
                "-hide_banner",
                "-nostdin",
                "-i",
                input_path,
                "-af",
                filters,
                "-ar",
                str(AUDIO_SAMPLE_RATE),
                "-ac",
                str(AUDIO_CHANNELS),
                "-c:a",
                "pcm_s16le",
                "-vn",
                "-y",
                output_path,
            ]
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                startupinfo=_windows_startupinfo(),
                timeout=timeout,
            )
            if completed.returncode != 0:
                self.logger(f"FFmpeg audio cleanup failed with code {completed.returncode}")
                self._remove_partial(output_path)
                return None

            output_duration = self._duration(output_path)
            drift = abs(output_duration - source_duration)
            if drift > self.duration_tolerance_seconds:
                self.logger(
                    "Audio cleanup rejected because timeline drifted by "
                    f"{drift:.3f}s (limit {self.duration_tolerance_seconds:.3f}s)"
                )
                self._remove_partial(output_path)
                return None
            return output_path
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            self.logger(f"Audio cleanup failed safely: {exc}")
            self._remove_partial(output_path)
            return None


class AudioPreprocessor:
    """Coordinates diagnostics and creates aligned ASR/diarization tracks.

    The canonical input is always retained for diarization. Enhancement is
    routed to ASR only, because aggressive cleanup can damage speaker cues.
    """

    def __init__(
        self,
        *,
        analyzer: AudioQualityAnalyzer | None = None,
        policy: AudioPreprocessingPolicy | None = None,
        dsp_backend: DspPreprocessingBackend | None = None,
        neural_backend: NeuralPreprocessingBackend | None = None,
    ) -> None:
        self.analyzer = analyzer or AudioQualityAnalyzer()
        self.policy = policy or AudioPreprocessingPolicy()
        self.dsp_backend = dsp_backend
        self.neural_backend = neural_backend

    @staticmethod
    def _disabled_decision() -> PreprocessingDecision:
        return PreprocessingDecision(
            action="none",
            confidence=1.0,
            reasons=("Audio preprocessing is disabled",),
            use_neural=False,
        )

    def _neural_available(self) -> bool:
        if self.neural_backend is None:
            return False
        try:
            return bool(self.neural_backend.is_available())
        except Exception:
            return False

    def _forced_decision(
        self,
        mode: PreprocessingMode,
        metrics: AudioQualityMetrics,
        *,
        neural_available: bool,
    ) -> PreprocessingDecision:
        safety = self.policy.decide(metrics, neural_available=neural_available)
        if safety.refused:
            return safety
        if mode == "light":
            return PreprocessingDecision(
                action="light_cleanup",
                confidence=1.0,
                reasons=("Light cleanup explicitly requested",),
                use_neural=False,
            )
        if mode == "denoise":
            if neural_available:
                return PreprocessingDecision(
                    action="neural_denoise",
                    confidence=1.0,
                    reasons=("Neural denoising explicitly requested",),
                    use_neural=True,
                )
            return PreprocessingDecision(
                action="light_cleanup",
                confidence=0.6,
                reasons=(
                    "Neural denoising explicitly requested",
                    "Neural denoiser unavailable; using conservative FFmpeg cleanup",
                ),
                use_neural=False,
                fallback=True,
            )
        return safety

    @staticmethod
    def _quality_gate(
        source: AudioQualityMetrics,
        candidate: AudioQualityMetrics,
        action: PreprocessingAction,
    ) -> tuple[bool, str | None]:
        """Reject enhancement artifacts using conservative no-reference checks."""
        clipping_limit = max(source.clipping_ratio + 0.002, 0.005)
        if candidate.clipping_ratio > clipping_limit:
            return False, "Quality gate rejected candidate: clipping increased"
        if candidate.silence_ratio > source.silence_ratio + 0.20:
            return False, "Quality gate rejected candidate: too much speech became silence"
        if candidate.rms_dbfs < -45.0 and source.rms_dbfs >= -45.0:
            return False, "Quality gate rejected candidate: useful signal level collapsed"

        if action == "normalize":
            before = abs(source.rms_dbfs - (-23.0))
            after = abs(candidate.rms_dbfs - (-23.0))
            if after > before + 1.0:
                return False, "Quality gate rejected candidate: loudness moved away from target"
            return True, None

        noise_floor_improved = candidate.noise_floor_dbfs <= source.noise_floor_dbfs - 1.0
        flatness_improved = candidate.spectral_flatness <= source.spectral_flatness * 0.95
        snr_improved = candidate.estimated_snr_db >= source.estimated_snr_db + 2.0
        if not (noise_floor_improved or flatness_improved or snr_improved):
            return False, "Quality gate rejected candidate: no measurable cleanup benefit"
        return True, None

    @staticmethod
    def _discard_candidate(path: str | None, canonical_path: str) -> None:
        if not path or path == canonical_path:
            return
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

    def prepare(
        self,
        canonical_path: str,
        output_dir: str,
        *,
        mode: str = "off",
    ) -> PreprocessedAudio:
        started = time.monotonic()
        normalized_mode = normalize_preprocessing_mode(mode)
        if normalized_mode == "off":
            report = AudioPreprocessingReport(
                mode=normalized_mode,
                metrics=None,
                decision=self._disabled_decision(),
                applied=False,
                elapsed_seconds=time.monotonic() - started,
            )
            return PreprocessedAudio(canonical_path, canonical_path, (), report)

        metrics = self.analyzer.analyze(canonical_path)
        neural_available = self._neural_available()
        if normalized_mode == "auto":
            decision = self.policy.decide(metrics, neural_available=neural_available)
        else:
            decision = self._forced_decision(
                normalized_mode,
                metrics,
                neural_available=neural_available,
            )

        if decision.action == "none":
            report = AudioPreprocessingReport(
                mode=normalized_mode,
                metrics=metrics,
                decision=decision,
                applied=False,
                elapsed_seconds=time.monotonic() - started,
            )
            return PreprocessedAudio(canonical_path, canonical_path, (), report)

        backend_name: str | None = None
        output_path: str | None = None
        try:
            if decision.use_neural and self.neural_backend is not None:
                backend_name = "neural"
                output_path = self.neural_backend.process(canonical_path, output_dir)
            elif self.dsp_backend is not None:
                backend_name = "ffmpeg"
                output_path = self.dsp_backend.process(
                    canonical_path,
                    output_dir,
                    decision.action,
                )
        except Exception:
            output_path = None

        applied = bool(output_path and output_path != canonical_path)
        processed_metrics: AudioQualityMetrics | None = None
        fallback_reason: str | None = None
        if applied and output_path is not None:
            try:
                processed_metrics = self.analyzer.analyze(output_path)
                accepted, fallback_reason = self._quality_gate(
                    metrics,
                    processed_metrics,
                    decision.action,
                )
            except Exception as exc:
                accepted = False
                fallback_reason = f"Quality gate could not validate candidate: {exc}"
            if not accepted:
                self._discard_candidate(output_path, canonical_path)
                output_path = None
                applied = False
        elif not applied:
            fallback_reason = "Selected preprocessing backend failed safely"

        report = AudioPreprocessingReport(
            mode=normalized_mode,
            metrics=metrics,
            decision=decision,
            applied=applied,
            backend=backend_name,
            runtime_fallback=not applied,
            fallback_reason=fallback_reason,
            processed_metrics=processed_metrics,
            elapsed_seconds=time.monotonic() - started,
        )
        if not applied:
            return PreprocessedAudio(canonical_path, canonical_path, (), report)
        if output_path is None:
            raise RuntimeError("Applied preprocessing did not produce an output path")
        return PreprocessedAudio(output_path, canonical_path, (output_path,), report)
