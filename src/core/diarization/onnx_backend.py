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

    @staticmethod
    def _progress(callback, value: float) -> None:
        if callback is not None:
            callback(value, None, None)

    def diarize(
        self,
        audio_path: str,
        num_speakers: int | None = None,
        progress_callback=None,
    ):
        import soundfile as sf  # noqa: PLC0415

        samples, sample_rate = sf.read(audio_path, dtype="float32", always_2d=True)
        if sample_rate != 16_000:
            raise ValueError(f"ONNX diarization ожидает 16000 Гц, получено {sample_rate}")
        waveform = samples.mean(axis=1).astype(np.float32, copy=False)
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
