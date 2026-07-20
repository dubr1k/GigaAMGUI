"""ONNX VAD adapter для общего ``VadSegmenter`` контракта."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np

from ...utils.model_cache import resolve_model_dir
from .onnx_provider import (
    available_onnx_providers,
    onnx_session_providers,
    resolve_onnx_providers,
)
from .vad import VadUnavailableError, merge_speech_regions

_VAD_REPOS = {
    "silero": "istupakov/silero-vad-onnx",
    "onnx-community/pyannote-segmentation-3.0": "onnx-community/pyannote-segmentation-3.0",
}


class OnnxVadSegmenter:
    """Лениво выделяет речевые интервалы через VAD из ``onnx-asr``."""

    def __init__(
        self,
        *,
        model: str = "silero",
        provider: str = "auto",
        quantization: str | None = None,
        model_dir: str | None = None,
        vad_factory: Callable[..., Any] | None = None,
        available_provider_probe: Callable[[], tuple[str, ...]] | None = None,
    ) -> None:
        self.model = model
        self.provider = provider
        self.quantization = quantization
        self.model_dir = model_dir
        self._vad_factory = vad_factory
        self._available_provider_probe = available_provider_probe or (
            lambda: available_onnx_providers(self.provider)
        )
        self._vad: Any | None = None

    @staticmethod
    def _load_vad(*args, **kwargs):
        import onnx_asr  # noqa: PLC0415

        return onnx_asr.load_vad(*args, **kwargs)

    def _ensure_vad(self):
        if self._vad is not None:
            return self._vad
        try:
            selection = resolve_onnx_providers(
                self.provider,
                available=self._available_provider_probe(),
            )
            factory = self._vad_factory or self._load_vad
            repo_id = _VAD_REPOS.get(self.model, self.model if "/" in self.model else None)
            model_dir = self.model_dir
            if model_dir is None and repo_id is not None:
                model_dir = resolve_model_dir(repo_id)
            vad = factory(
                self.model,
                path=model_dir,
                quantization=self.quantization,
                providers=onnx_session_providers(selection),
            )
            if not callable(getattr(vad, "segment_batch", None)):
                raise VadUnavailableError(
                    "Установленная версия onnx-asr не предоставляет VAD segment_batch"
                )
            self._vad = vad
            return vad
        except VadUnavailableError:
            raise
        except Exception as exc:
            raise VadUnavailableError("ONNX VAD initialization failed") from exc

    def segment_file(
        self,
        audio_path: str,
        *,
        audio_duration: float,
    ) -> list[tuple[float, float]]:
        vad = self._ensure_vad()

        import soundfile as sf  # noqa: PLC0415

        try:
            samples, sample_rate = sf.read(audio_path, dtype="float32", always_2d=True)
            if sample_rate not in {8000, 16000}:
                raise VadUnavailableError(
                    f"ONNX VAD поддерживает 8000/16000 Гц, получено {sample_rate}"
                )
            waveform = samples.mean(axis=1).astype(np.float32, copy=False)
            batches = vad.segment_batch(
                waveform[None, :],
                np.asarray([len(waveform)], dtype=np.int64),
                sample_rate,
            )
            sample_segments = list(next(iter(batches), iter(())))
            regions = [
                (float(start) / sample_rate, float(end) / sample_rate)
                for start, end in sample_segments
            ]
            return merge_speech_regions(regions, audio_duration=audio_duration)
        except VadUnavailableError:
            raise
        except Exception as exc:
            raise VadUnavailableError("ONNX VAD inference failed") from exc
