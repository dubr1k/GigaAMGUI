"""Speaker embedding extraction through the public onnx-asr WeSpeaker API."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..asr.onnx_provider import (
    available_onnx_providers,
    onnx_session_providers,
    resolve_onnx_providers,
)
from .onnx_segmentation import SegmentationResult


@dataclass(frozen=True)
class EmbeddingResult:
    embeddings: np.ndarray
    valid: np.ndarray
    window_indices: np.ndarray
    local_speakers: np.ndarray


class OnnxSpeakerEmbeddings:
    def __init__(
        self,
        *,
        provider: str = "auto",
        model_dir: str | None = None,
        sample_rate: int = 16_000,
        threshold: float = 0.5,
        min_speech_seconds: float = 0.5,
        model_factory: Callable[..., Any] | None = None,
        available_provider_probe: Callable[[], tuple[str, ...]] | None = None,
    ) -> None:
        self.provider = provider
        self.model_dir = model_dir
        self.sample_rate = sample_rate
        self.threshold = threshold
        self.min_speech_seconds = min_speech_seconds
        self._model_factory = model_factory
        self._available_provider_probe = available_provider_probe or available_onnx_providers
        self._model: Any | None = None

    @staticmethod
    def _load_model(*, providers: list[str], model_dir: str | None):
        from onnx_asr.loader import Manager  # noqa: PLC0415

        return Manager(
            providers=providers,
            preprocessor_config={"use_numpy_preprocessors": False},
        ).create_se(
            "wespeaker/wespeaker-voxceleb-resnet34",
            local_dir=model_dir,
        )

    def _ensure_model(self):
        if self._model is None:
            selection = resolve_onnx_providers(
                self.provider,
                available=self._available_provider_probe(),
            )
            factory = self._model_factory or self._load_model
            self._model = factory(
                providers=onnx_session_providers(selection),
                model_dir=self.model_dir,
            )
        return self._model

    def extract(self, waveform: np.ndarray, segmentation: SegmentationResult) -> EmbeddingResult:
        audio = np.asarray(waveform, dtype=np.float32).reshape(-1)
        probabilities = segmentation.probabilities
        rows = probabilities.shape[0] * probabilities.shape[2]
        valid = np.zeros(rows, dtype=np.bool_)
        window_indices = np.repeat(np.arange(probabilities.shape[0]), probabilities.shape[2])
        local_speakers = np.tile(np.arange(probabilities.shape[2]), probabilities.shape[0])
        waveforms: list[np.ndarray] = []
        valid_rows: list[int] = []
        min_samples = max(1, int(round(self.min_speech_seconds * self.sample_rate)))

        for row, (window_index, speaker_index) in enumerate(
            zip(window_indices, local_speakers, strict=True)
        ):
            frame_mask = probabilities[window_index, :, speaker_index] >= self.threshold
            speech_seconds = float(frame_mask.sum()) * segmentation.frame_step
            if speech_seconds < self.min_speech_seconds:
                continue
            start = int(round(segmentation.window_starts[window_index] * self.sample_rate))
            frame_samples = max(1, int(round(segmentation.frame_step * self.sample_rate)))
            mask = np.repeat(frame_mask, frame_samples)
            end = min(len(audio), start + len(mask))
            if end <= start:
                continue
            # Вырезаем активную речь, а не глушим остальное окно нулями:
            # иначе строка с 0.5 c речи отдавала бы в WeSpeaker ~9.5 c тишины,
            # fbank/CMN-статистики считались бы по ней и эмбеддинги разных
            # спикеров стягивались бы к «эмбеддингу тишины».
            chunk = np.ascontiguousarray(audio[start:end][mask[: end - start]])
            if len(chunk) < min_samples:
                continue
            valid[row] = True
            valid_rows.append(row)
            waveforms.append(chunk)

        if not waveforms:
            embeddings = np.empty((rows, 0), dtype=np.float32)
        else:
            raw = np.asarray(self._ensure_model().embedding(waveforms), dtype=np.float32)
            if raw.ndim == 1:
                raw = raw[None, :]
            norms = np.linalg.norm(raw, axis=1, keepdims=True)
            normalized = raw / np.maximum(norms, np.finfo(np.float32).eps)
            embeddings = np.zeros((rows, normalized.shape[1]), dtype=np.float32)
            embeddings[np.asarray(valid_rows)] = normalized

        return EmbeddingResult(
            embeddings=embeddings,
            valid=valid,
            window_indices=window_indices,
            local_speakers=local_speakers,
        )

    def unload(self) -> None:
        self._model = None
