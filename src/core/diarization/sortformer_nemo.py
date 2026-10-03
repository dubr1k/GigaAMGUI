"""Диаризация через NVIDIA Streaming Sortformer 4spk v2.1 (NeMo)."""

from __future__ import annotations

import logging
import threading
from contextlib import nullcontext
from pathlib import Path

from ...utils.model_cache import hf_repo_is_cached
from ..model_preparation import PreparationCancelled, PreparationState
from .base import SpeakerSegment
from .pyannote_backend import DiarizationManager

logger = logging.getLogger(__name__)

_SORTFORMER_MODEL_ID = "nvidia/diar_streaming_sortformer_4spk-v2.1"


class SortformerDiarizationManager(DiarizationManager):
    """Диаризация через NVIDIA Streaming Sortformer 4spk v2.1.

    NeMo импортируется только при первом запуске backend: базовая установка и
    PyInstaller-сборки с pyannote от него не зависят. Конфигурация повторяет
    официальный high-latency preset model card v2.1: длинный chunk даёт более ровные
    границы ценой задержки, которая для офлайн-транскрибации несущественна.
    """

    max_supported_speakers = 4
    _shared_pipelines = {}
    _shared_load_lock = threading.Lock()
    _shared_inference_lock = threading.Lock()
    _shared_inference_contexts = {}

    def __init__(self, device: str = "auto"):
        self.backend = "sortformer"
        self.hf_token = None
        self.device = self._resolve_sortformer_device(device)
        self.min_speakers = None
        self.max_speakers = self.max_supported_speakers
        self._pipeline = None
        self._inference_lock = self._shared_inference_lock
        self._inference_context = nullcontext
        self.last_fallback_reason: str | None = None

    @property
    def pipeline(self):
        """Переиспользует одну тяжёлую модель NeMo между processor-задачами."""
        if self._pipeline is not None:
            return self._pipeline
        with self._shared_load_lock:
            cls = type(self)
            if self.device not in cls._shared_pipelines:
                cls._shared_pipelines[self.device] = self._load_pipeline()
            self._pipeline = cls._shared_pipelines[self.device]
            self._inference_context = cls._shared_inference_contexts[self.device]
        return self._pipeline

    @staticmethod
    def _resolve_sortformer_device(device: str) -> str:
        """Выбрать CUDA/MPS/CPU без неявного переноса MPS на CPU."""
        if device not in {"auto", "cuda", "cpu", "mps"}:
            raise ValueError(f"Неподдерживаемое устройство Sortformer: {device}")
        if device == "cpu":
            return "cpu"
        try:
            import torch

            if device in {"auto", "cuda"} and torch.cuda.is_available():
                return "cuda"
            mps = getattr(getattr(torch, "backends", None), "mps", None)
            if device in {"auto", "mps"} and mps is not None and mps.is_available():
                return "mps"
        except ImportError:
            pass
        return "cpu"

    def _fallback_to_cpu(self) -> None:
        """Сбросить нерабочий MPS pipeline и лениво загрузить отдельный CPU."""
        cls = type(self)
        failed_pipeline = self._pipeline
        with self._shared_load_lock:
            if cls._shared_pipelines.get("mps") is failed_pipeline:
                cls._shared_pipelines.pop("mps", None)
                cls._shared_inference_contexts.pop("mps", None)
            self._pipeline = None
            self._inference_context = nullcontext
            self.device = "cpu"
        _ = self.pipeline

    def unload(self) -> None:
        """Выгрузить модель NeMo, включая общий для класса кэш.

        Базовый ``unload`` обнуляет только ``self._pipeline``, а модель живёт в
        ``cls._shared_pipelines`` и переживала выгрузку до конца процесса. На
        CUDA это удержанная VRAM на карте фиксированного объёма, поэтому кэш
        нужно чистить именно по ключу устройства.
        """
        cls = type(self)
        with self._shared_load_lock:
            cls._shared_pipelines.pop(self.device, None)
            cls._shared_inference_contexts.pop(self.device, None)
            self._pipeline = None
            self._inference_context = nullcontext
        self._empty_accelerator_cache()

    def _empty_accelerator_cache(self) -> None:
        """Вернуть драйверу VRAM/unified memory, освободившуюся после выгрузки."""
        try:
            import torch

            if self.device == "cuda" and torch.cuda.is_available():
                torch.cuda.empty_cache()
            elif self.device == "mps" and hasattr(torch, "mps"):
                torch.mps.empty_cache()
        except Exception:
            pass

    def _run_sortformer(self, audio_path: Path):
        with self._inference_lock:
            pipeline = self.pipeline
            with self._inference_context():
                return pipeline.diarize(
                    audio=[str(audio_path)],
                    override_config=self._diarize_config(),
                )

    def _load_pipeline(self):
        try:
            import torch
            from nemo.collections.asr.models import SortformerEncLabelModel
        except ImportError as exc:
            raise ImportError(
                "NVIDIA Sortformer не установлен. Установите опциональные "
                "зависимости: pip install -r requirements-sortformer.txt"
            ) from exc

        logger.info("Загрузка модели диаризации: %s", _SORTFORMER_MODEL_ID)
        model = SortformerEncLabelModel.from_pretrained(_SORTFORMER_MODEL_ID)
        model.eval()
        model.to(torch.device(self.device))
        self._inference_context = getattr(torch, "inference_mode", nullcontext)
        type(self)._shared_inference_contexts[self.device] = self._inference_context

        modules = model.sortformer_modules
        modules.chunk_len = 340
        modules.chunk_right_context = 40
        modules.fifo_len = 40
        modules.spkcache_update_period = 300
        modules.spkcache_len = 188
        modules._check_streaming_parameters()
        logger.info("Модель %s загружена на %s", _SORTFORMER_MODEL_ID, self.device)
        return model

    def prepare(self, report=None, cancel_check=None):
        """Скачать и загрузить NeMo Sortformer до обработки первого файла."""
        emit = report or (lambda _state, **_kwargs: None)
        cancelled = cancel_check or (lambda: False)
        if cancelled():
            raise PreparationCancelled("Подготовка Sortformer отменена")
        cached = hf_repo_is_cached(_SORTFORMER_MODEL_ID)
        if not cached:
            emit(
                PreparationState.DOWNLOADING,
                message=f"Sortformer NeMo ({_SORTFORMER_MODEL_ID})",
            )
        emit(
            PreparationState.LOADING,
            message=f"Sortformer NeMo (device={self.device})",
            cached=cached,
        )
        _ = self.pipeline
        if cancelled():
            raise PreparationCancelled("Подготовка Sortformer отменена")
        return self

    @staticmethod
    def _diarize_config():
        """NeMo-recommended post-processing for streaming Sortformer v2.1."""

        from nemo.collections.asr.parts.mixins.diarization import DiarizeConfig
        from nemo.collections.asr.parts.utils.vad_utils import PostProcessingParams
        from omegaconf import OmegaConf

        params = OmegaConf.structured(PostProcessingParams())
        params.onset = 0.64
        params.offset = 0.74
        params.pad_onset = 0.06
        params.pad_offset = 0.0
        params.min_duration_on = 0.1
        params.min_duration_off = 0.15
        return DiarizeConfig(
            batch_size=1,
            num_workers=0,
            verbose=False,
            postprocessing_params=params,
        )

    def diarize(
        self,
        audio_path: Path | str,
        num_speakers: int | None = None,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
        progress_callback=None,
    ) -> list[SpeakerSegment]:
        """Запускает Sortformer и приводит вывод NeMo к SpeakerSegment."""
        del min_speakers, max_speakers
        if num_speakers is not None and not 1 <= num_speakers <= self.max_supported_speakers:
            raise ValueError("Sortformer поддерживает не более 4 спикеров")
        if num_speakers is not None:
            logger.warning(
                "Sortformer сам определяет активных спикеров; num_speakers=%s игнорируется",
                num_speakers,
            )

        audio_path = Path(audio_path)
        try:
            try:
                predicted = self._run_sortformer(audio_path)
            except Exception as exc:
                if self.device != "mps":
                    raise
                self.last_fallback_reason = f"MPS: {exc}; использован CPU"
                logger.warning(
                    "Sortformer не выполнился на MPS (%s); повторяем на CPU",
                    exc,
                )
                self._fallback_to_cpu()
                predicted = self._run_sortformer(audio_path)
            if not predicted or not isinstance(predicted, (list, tuple)):
                raise ValueError("Sortformer не вернул результаты")
            raw_segments = predicted[0]
            if isinstance(raw_segments, str):
                raw_segments = [raw_segments]

            segments = [self._parse_sortformer_segment(item) for item in raw_segments]
            segments.sort(key=lambda segment: segment.start)
            segments = self._rename_speakers(segments)
            if progress_callback is not None:
                progress_callback(1.0, None, None)
            logger.info(
                "Диаризация Sortformer завершена. Найдено спикеров: %s",
                len({segment.speaker for segment in segments}),
            )
            return segments
        except Exception as exc:
            logger.error("Ошибка Sortformer: %s", exc)
            raise ValueError(f"Ошибка при диаризации Sortformer: {exc}") from exc

    @staticmethod
    def _parse_sortformer_segment(item) -> SpeakerSegment:
        if isinstance(item, str):
            parts = item.strip().split()
            if len(parts) != 3:
                raise ValueError(f"Неожиданный сегмент Sortformer: {item!r}")
            start, end, speaker = parts
        elif isinstance(item, dict):
            start = item.get("start")
            end = item.get("end")
            speaker = item.get("speaker", item.get("speaker_id"))
        elif isinstance(item, (list, tuple)) and len(item) == 3:
            start, end, speaker = item
        else:
            raise ValueError(f"Неожиданный сегмент Sortformer: {item!r}")

        try:
            start_value = float(start)
            end_value = float(end)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Неожиданный сегмент Sortformer: {item!r}") from exc
        if not speaker or start_value < 0 or end_value <= start_value:
            raise ValueError(f"Неожиданный сегмент Sortformer: {item!r}")
        return SpeakerSegment(start_value, end_value, str(speaker))
