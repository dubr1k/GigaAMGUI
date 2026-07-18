"""ASR backend на базе ``onnx-asr`` и ONNX Runtime."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from .chunking import plan_audio_chunks, stitch_overlapping_text
from .models import onnx_model_name, validate_asr_model
from .onnx_provider import (
    ProviderSelection,
    available_onnx_providers,
    onnx_session_providers,
    resolve_onnx_providers,
)
from .onnx_vad import OnnxVadSegmenter
from .token_timestamps import tokens_to_words
from .types import BackendCapabilities, TranscriptionSegment
from .vad import VadSegmenter


class OnnxBackend:
    """Лениво загружаемый GigaAM backend через ONNX Runtime."""

    name = "onnx"

    def __init__(
        self,
        model: str | None = None,
        *,
        provider: str = "auto",
        quantization: str | None = None,
        model_dir: str | None = None,
        vad_model: str = "silero",
        segmentation_mode: str = "vad",
        model_factory: Callable[..., Any] | None = None,
        available_provider_probe: Callable[[], tuple[str, ...]] | None = None,
        vad_segmenter_factory: Callable[..., VadSegmenter] | None = None,
    ) -> None:
        self.model_revision = validate_asr_model(model)
        self.requested_provider = (provider or "auto").strip().lower() or "auto"
        normalized_quantization = (quantization or "").strip().lower()
        self.quantization = normalized_quantization or None
        self.model_dir = model_dir
        self.vad_model = vad_model
        self.segmentation_strategy = segmentation_mode
        if self.segmentation_strategy not in {"vad", "overlap_chunks", "fixed_chunks"}:
            raise ValueError(f"Неизвестный режим сегментации: {self.segmentation_strategy}")

        self.model: Any | None = None
        self.device: str | None = None
        self.segmentation_mode = "not_run"
        self.segmentation_fallback_reason: str | None = None
        self.provider_selection: ProviderSelection | None = None
        self._model_factory = model_factory
        self._available_provider_probe = available_provider_probe or available_onnx_providers
        self._vad_segmenter_factory = vad_segmenter_factory or OnnxVadSegmenter
        self._vad_segmenter: VadSegmenter | None = None
        self._logger: Callable[[str], None] | None = None
        self._inference_lock = threading.Lock()

    @staticmethod
    def _load_onnx_model(*args, **kwargs):
        import onnx_asr  # noqa: PLC0415

        return onnx_asr.load_model(*args, **kwargs)

    def _bundled_download_root(self) -> str | None:
        return self.model_dir

    def _create_model(self, selection: ProviderSelection) -> Any:
        factory = self._model_factory or self._load_onnx_model
        raw_model = factory(
            onnx_model_name(self.model_revision),
            path=self.model_dir,
            quantization=self.quantization,
            providers=onnx_session_providers(selection),
            preprocessor_config={"use_numpy_preprocessors": False},
        )
        return raw_model.with_timestamps()

    def load(self, logger: Callable[[str], None] | None = None) -> bool:
        self._logger = logger
        if self.model is not None:
            return True

        try:
            selection = resolve_onnx_providers(
                self.requested_provider,
                available=self._available_provider_probe(),
            )
            self.model = self._create_model(selection)
            self.provider_selection = selection
            self.device = selection.active
            if logger:
                logger(
                    "ONNX ASR загружен: "
                    f"{self.model_revision}, provider={selection.providers[0]}"
                )
            return True
        except Exception as exc:
            self.model = None
            self.provider_selection = None
            self.device = None
            if logger:
                logger(f"КРИТИЧЕСКАЯ ОШИБКА загрузки ONNX ASR:\n{exc}")
            return False

    def transcribe_longform(
        self,
        audio_path: str,
        progress_callback: Callable[[float, float | None, float | None], None] | None = None,
    ) -> list[TranscriptionSegment]:
        with self._inference_lock:
            try:
                return self._transcribe_longform_unlocked(
                    audio_path,
                    progress_callback=progress_callback,
                )
            except Exception as exc:
                if not self._retry_on_cpu_after_provider_failure(exc):
                    raise
                return self._transcribe_longform_unlocked(
                    audio_path,
                    progress_callback=progress_callback,
                )

    def _retry_on_cpu_after_provider_failure(self, exc: Exception) -> bool:
        selection = self.provider_selection
        if (
            self.requested_provider != "auto"
            or selection is None
            or selection.active == "cpu"
            or "CPUExecutionProvider" not in self._available_provider_probe()
        ):
            return False

        failed_provider = selection.providers[0]
        reason = (
            f"{failed_provider} завершил inference с ошибкой "
            f"{type(exc).__name__}: {exc}; использован CPUExecutionProvider"
        )
        cpu_selection = ProviderSelection(
            requested="auto",
            active="cpu",
            providers=("CPUExecutionProvider",),
            fallback_reason=reason,
        )
        if self._logger:
            self._logger(f"ПРЕДУПРЕЖДЕНИЕ: {reason}")
        self.model = self._create_model(cpu_selection)
        self.provider_selection = cpu_selection
        self.device = "cpu"
        return True

    def _transcribe_longform_unlocked(
        self,
        audio_path: str,
        progress_callback: Callable[[float, float | None, float | None], None] | None = None,
    ) -> list[TranscriptionSegment]:
        if self.model is None:
            raise RuntimeError("Модель не загружена")

        import soundfile as sf  # noqa: PLC0415

        samples, sample_rate = sf.read(audio_path, dtype="float32", always_2d=True)
        audio = samples.mean(axis=1)
        total_samples = int(audio.shape[0])
        total_seconds = total_samples / sample_rate if sample_rate else 0.0

        if self.segmentation_strategy == "fixed_chunks":
            boundaries = [
                (
                    float(start) / sample_rate,
                    float(min(start + 20 * sample_rate, total_samples)) / sample_rate,
                )
                for start in range(0, total_samples, 20 * sample_rate)
            ]
            chunks = plan_audio_chunks(
                audio,
                boundaries,
                sample_rate=sample_rate,
                max_chunk_seconds=20.0,
                overlap_seconds=0.0,
            )
            self.segmentation_mode = "fixed_chunks"
            self.segmentation_fallback_reason = (
                "VAD отключён настройкой: использовано разбиение по 20 секунд"
            )
        elif self.segmentation_strategy == "vad":
            try:
                if self._vad_segmenter is None:
                    self._vad_segmenter = self._vad_segmenter_factory(
                        model=self.vad_model,
                        provider=self.requested_provider,
                        quantization=self.quantization,
                        model_dir=self.model_dir,
                    )
                boundaries = self._vad_segmenter.segment_file(
                    audio_path,
                    audio_duration=total_seconds,
                )
                chunks = plan_audio_chunks(
                    audio,
                    boundaries,
                    sample_rate=sample_rate,
                    max_chunk_seconds=20.0,
                )
                self.segmentation_mode = "vad"
                self.segmentation_fallback_reason = None
            except Exception as exc:
                self._vad_segmenter = None
                chunks = plan_audio_chunks(
                    audio,
                    [(0.0, total_seconds)],
                    sample_rate=sample_rate,
                    max_chunk_seconds=20.0,
                )
                self.segmentation_mode = "overlap_chunks"
                self.segmentation_fallback_reason = (
                    f"ONNX VAD недоступен ({type(exc).__name__}: {exc}); "
                    "использовано разбиение по тихим точкам с перекрытием"
                )
                if self._logger:
                    self._logger(f"ПРЕДУПРЕЖДЕНИЕ: {self.segmentation_fallback_reason}")
        else:
            chunks = plan_audio_chunks(
                audio,
                [(0.0, total_seconds)],
                sample_rate=sample_rate,
                max_chunk_seconds=20.0,
            )
            self.segmentation_mode = "overlap_chunks"
            self.segmentation_fallback_reason = (
                "VAD отключён настройкой: использовано разбиение "
                "по тихим точкам с перекрытием"
            )

        results: list[TranscriptionSegment] = []
        previous_result_index: int | None = None
        previous_group: int | None = None
        reported = 0.0

        for chunk in chunks:
            start = chunk.decode_start_sample
            end = chunk.decode_end_sample
            if end - start < max(1, sample_rate // 10):
                continue

            decoded = self.model.recognize(audio[start:end], sample_rate=sample_rate)
            text = str(getattr(decoded, "text", decoded) or "").strip()
            relative_words = tokens_to_words(
                getattr(decoded, "tokens", None),
                getattr(decoded, "timestamps", None),
                duration=float(end - start) / sample_rate,
            )
            words = None
            if relative_words is not None:
                decode_start_sec = float(start) / sample_rate
                words = [
                    {
                        "text": word["text"],
                        "start": round(decode_start_sec + word["start"], 9),
                        "end": round(decode_start_sec + word["end"], 9),
                    }
                    for word in relative_words
                ]

            if text:
                if (
                    chunk.overlaps_previous
                    and previous_result_index is not None
                    and previous_group == chunk.group
                ):
                    previous_text = results[previous_result_index]["transcription"]
                    previous_text, text, overlap_words = stitch_overlapping_text(
                        previous_text,
                        text,
                    )
                    results[previous_result_index]["transcription"] = previous_text
                    if words is not None and overlap_words:
                        words = words[overlap_words:]

                start_time = max(0.0, float(chunk.start_sec))
                end_time = min(total_seconds, float(chunk.end_sec))
                if text and end_time >= start_time:
                    segment: TranscriptionSegment = {
                        "transcription": text,
                        "boundaries": (start_time, end_time),
                    }
                    if words is not None:
                        segment["words"] = words
                    results.append(segment)
                    previous_result_index = len(results) - 1
                    previous_group = chunk.group
            else:
                previous_result_index = None
                previous_group = None

            processed_seconds = min(total_seconds, float(chunk.end_sec))
            ratio = 1.0 if total_seconds <= 0 else min(processed_seconds / total_seconds, 1.0)
            if progress_callback is not None and ratio >= reported:
                progress_callback(ratio, processed_seconds, total_seconds)
                reported = ratio

        if progress_callback is not None and total_samples > 0 and reported < 1.0:
            progress_callback(1.0, total_seconds, total_seconds)
        return results

    def unload(self) -> None:
        with self._inference_lock:
            self.model = None
            self._vad_segmenter = None
            self.provider_selection = None
            self.device = None
            self.segmentation_mode = "not_run"
            self.segmentation_fallback_reason = None

    def is_loaded(self) -> bool:
        return self.model is not None

    def capabilities(self) -> BackendCapabilities:
        selection = self.provider_selection
        return BackendCapabilities(
            backend=self.name,
            model=self.model_revision,
            device=self.device or "N/A",
            segmentation_mode=self.segmentation_mode,
            segmentation_fallback_reason=self.segmentation_fallback_reason,
            provider=selection.providers[0] if selection else None,
            quantization=self.quantization,
            provider_fallback_reason=selection.fallback_reason if selection else None,
        )
