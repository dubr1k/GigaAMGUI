"""Complete ONNX diarization pipeline: segmentation → embeddings → clustering."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from .clustering import cluster_embeddings
from .mapping import SpeakerMappingMixin
from .onnx_embeddings import OnnxSpeakerEmbeddings
from .onnx_segmentation import OnnxSegmentation
from .reconstruction import reconstruct_speaker_segments


class OnnxDiarizationBackend(SpeakerMappingMixin):
    backend = "onnx"

    def __init__(
        self,
        *,
        provider: str = "auto",
        model_dir: str | None = None,
        segmenter: OnnxSegmentation | None = None,
        embedding_extractor: OnnxSpeakerEmbeddings | None = None,
        cluster_fn: Callable = cluster_embeddings,
        reconstruct_fn: Callable = reconstruct_speaker_segments,
    ) -> None:
        self.provider = provider
        self.model_dir = model_dir
        self.hf_token = None
        self._segmenter = segmenter or OnnxSegmentation(
            provider=provider,
            model_dir=model_dir,
        )
        self._embedding_extractor = embedding_extractor or OnnxSpeakerEmbeddings(
            provider=provider,
            model_dir=model_dir,
        )
        self._cluster_fn = cluster_fn
        self._reconstruct_fn = reconstruct_fn

    SAMPLE_RATE = 16_000

    @staticmethod
    def _progress(callback, value: float) -> None:
        if callback is not None:
            callback(value, None, None)

    @classmethod
    def _to_target_rate(cls, waveform: np.ndarray, sample_rate: int) -> np.ndarray:
        """Привести дорожку к 16 кГц.

        AUDIO_SAMPLE_RATE настраивается через env, а pyannote и Sortformer
        ресемплируют сами — падать на легитимной настройке нельзя.
        """
        if sample_rate == cls.SAMPLE_RATE:
            return waveform
        import soxr  # noqa: PLC0415

        resampled = soxr.resample(waveform, sample_rate, cls.SAMPLE_RATE)
        return np.asarray(resampled, dtype=np.float32)

    def diarize(
        self,
        audio_path: str,
        num_speakers: int | None = None,
        progress_callback=None,
    ):
        import soundfile as sf  # noqa: PLC0415

        samples, sample_rate = sf.read(audio_path, dtype="float32", always_2d=True)
        waveform = samples.mean(axis=1).astype(np.float32, copy=False)
        waveform = self._to_target_rate(waveform, sample_rate)
        segmentation = self._segmenter.infer(waveform)
        self._progress(progress_callback, 0.35)
        embeddings = self._embedding_extractor.extract(waveform, segmentation)
        if not bool(np.any(embeddings.valid)):
            raise ValueError("ONNX diarization не получила валидные speaker embeddings")
        self._progress(progress_callback, 0.7)
        assignments = self._cluster_fn(embeddings, num_speakers=num_speakers)
        segments = self._reconstruct_fn(segmentation, assignments)
        if not segments:
            raise ValueError("ONNX diarization не восстановила speaker-сегменты")
        segments = self._rename_speakers(segments)
        self._progress(progress_callback, 1.0)
        return segments

    def unload(self) -> None:
        self._segmenter.unload()
        self._embedding_extractor.unload()
