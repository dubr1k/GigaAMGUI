"""PyTorch backend using `gigaam` model API."""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Callable
from typing import Any, cast

import numpy as np

from ...config import (
    ASR_SEGMENTATION_MODE,
    ASR_VAD_DEVICE,
    MODEL_NAME,
    MODEL_REVISION,
)
from ..devices import best_torch_device, empty_accelerator_cache
from .chunking import (
    AudioChunk,
    plan_audio_chunks,
    vad_regions_miss_active_audio,
)
from .longform import (
    VadSegmenterCache,
    absolute_words,
    assemble_segments,
    call_logger,
    pyannote_vad_key,
)
from .types import BackendCapabilities, TranscriptionSegment, TranscriptionWord, normalize_window_audio
from .vad import PyannoteVadSegmenter, VadSegmenter, VadUnavailableError, resolve_vad_device


def gigaam_checkpoint_files(revision: object) -> tuple[str, ...]:
    """Файлы, которые ``gigaam.load_model`` ищет в download_root для revision.

    Короткие имена v3 (``e2e_rnnt``) gigaam хранит как ``v3_e2e_rnnt``; e2e-модели
    кроме чекпоинта требуют SentencePiece-токенизатор.
    """
    name = str(revision)
    if name in {"ctc", "rnnt", "e2e_ctc", "e2e_rnnt", "ssl"}:
        name = f"v3_{name}"
    files = [f"{name}.ckpt"]
    if name != "v1_rnnt" and "e2e" in name:
        files.append(f"{name}_tokenizer.model")
    return tuple(files)


class PyTorchBackend:
    """ASR backend implemented via torch + gigaam."""

    name = "pytorch"

    def __init__(
        self,
        model: str | None = None,
        *,
        revision: str | None = None,
        segmentation_mode: str | None = None,
        vad_segmenter_factory: Callable[..., VadSegmenter] | None = None,
    ):
        self.model_name = model or MODEL_NAME
        self.model_revision = revision or MODEL_REVISION
        self.model = None
        self.device = None
        self._gigaam = None
        self._vad_cache = VadSegmenterCache(vad_segmenter_factory or PyannoteVadSegmenter)
        self.segmentation_strategy = segmentation_mode or ASR_SEGMENTATION_MODE
        if self.segmentation_strategy not in {"vad", "overlap_chunks", "fixed_chunks"}:
            raise ValueError(f"Неизвестный режим сегментации: {self.segmentation_strategy}")
        self._inference_lock = threading.Lock()
        self.segmentation_mode = "not_run"
        self.segmentation_fallback_reason: str | None = None
        self._logger: Callable[[str], None] | None = None

    def _bundled_download_root(self) -> str | None:
        meipass_root = getattr(sys, "_MEIPASS", None)
        candidates = [
            *([meipass_root] if meipass_root else []),
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        ]
        model_dir = "models/gigaam"
        required = gigaam_checkpoint_files(self.model_revision)

        for root in candidates:
            candidate = os.path.join(root, model_dir)
            if all(os.path.isfile(os.path.join(candidate, name)) for name in required):
                return candidate
        selected = os.environ.get("GIGAAM_PYTORCH_MODEL_DIR")
        if selected:
            os.makedirs(selected, exist_ok=True)
            return selected
        return None

    def _select_device(self) -> str:
        """Choose a compute backend, preserving existing runtime behavior."""
        try:
            from ...utils.runtime_manager import get_selected_variant, torch_device_for

            selected_variant = get_selected_variant()
            preferred = torch_device_for(selected_variant) if selected_variant else None
        except Exception:
            preferred = None

        if preferred == "cuda":
            return best_torch_device(("cuda",))
        if preferred == "cpu":
            return "cpu"
        return best_torch_device(("cuda", "xpu", "mps"))

    @classmethod
    def _decode_text(cls, decode_result: object) -> str:
        """Extract text from legacy decoders and GigaAM 0.2 structured results."""
        if decode_result is None:
            return ""
        if isinstance(decode_result, str):
            return decode_result.strip()

        text_value = getattr(decode_result, "text", None)
        if isinstance(text_value, str):
            return text_value.strip()

        # GigaAM 0.2 returns one tuple per sample:
        # (text, token_ids, token_frames).
        if (
            isinstance(decode_result, tuple)
            and decode_result
            and isinstance(decode_result[0], str)
        ):
            return decode_result[0].strip()

        if isinstance(decode_result, (tuple, list)):
            for candidate in decode_result:
                candidate_text = cls._decode_text(candidate)
                if candidate_text:
                    return candidate_text
            return ""

        return str(decode_result).strip()

    @classmethod
    def _decode_chunk(
        cls,
        model: Any,
        encoded: Any,
        encoded_len: Any,
        wav_len: Any,
    ) -> tuple[str, list[TranscriptionWord] | None]:
        """Decode one chunk and request official GigaAM word timestamps when available."""
        timed_decode = getattr(model, "_decode", None)
        if callable(timed_decode):
            decoded = timed_decode(
                encoded,
                encoded_len,
                wav_len,
                word_timestamps=True,
            )
            if decoded:
                text, words = decoded[0]
                normalized_words: list[TranscriptionWord] = [
                    {
                        "text": str(word.text).strip(),
                        "start": float(word.start),
                        "end": float(word.end),
                    }
                    for word in (words or [])
                    if str(word.text).strip()
                ]
                return str(text).strip(), normalized_words or None

        decode_result = model.decoding.decode(model.head, encoded, encoded_len)
        return cls._decode_text(decode_result), None

    def load(self, logger: Callable[[str], None] | None = None) -> bool:
        self._logger = logger
        if self.model is not None:
            return True

        try:

            import gigaam

            from ...utils.runtime_manager import get_selected_variant  # noqa: F401  pylint: disable=unused-import

            self._gigaam = gigaam

            if logger:
                logger("Подготавливаем модель распознавания речи (GigaAM-v3)…")
                logger("При первом запуске модель скачивается — это может занять несколько минут.")

            self.device = self._select_device()
            if logger:
                logger(f"Вычисления выполняются на устройстве: {self.device.upper()}")

            use_fp16 = self.device != "cpu"
            self.model = gigaam.load_model(
                self.model_revision,
                fp16_encoder=use_fp16,
                device=self.device,
                download_root=self._bundled_download_root(),
            )

            if logger:
                logger("Модель готова.")
            return True
        except Exception as e:
            if logger:
                logger(f"Не удалось загрузить модель распознавания:\n{e}")
            return False

    def _empty_cache(self):
        empty_accelerator_cache(self.device)

    def transcribe_longform(
        self,
        audio_path: str,
        progress_callback: Callable[[float, float | None, float | None], None] | None = None,
        logger: Callable[[str], None] | None = None,
    ) -> list[TranscriptionSegment]:
        """Serialize access to the shared GigaAM and pyannote models."""
        with self._inference_lock, call_logger(self, logger):
            return self._transcribe_longform_unlocked(audio_path, progress_callback)

    def transcribe_window(
        self,
        audio: np.ndarray,
        sample_rate: int,
        offset_samples: int,
    ) -> list[TranscriptionSegment]:
        if self.model is None:
            raise RuntimeError("Модель не загружена")
        if offset_samples < 0:
            raise ValueError("offset_samples must be non-negative")
        import torch

        window = normalize_window_audio(audio, sample_rate)
        start = offset_samples / sample_rate
        with self._inference_lock, torch.inference_mode():
            model = cast(Any, self.model)
            wav = torch.from_numpy(window).to(model._device).to(model._dtype).unsqueeze(0)
            length = torch.full([1], wav.shape[-1], device=model._device)
            encoded, encoded_len = model.forward(wav, length)
            text, relative_words = self._decode_chunk(model, encoded, encoded_len, length)
        if not text:
            return []
        segment: TranscriptionSegment = {
            "transcription": text,
            "boundaries": (start, start + len(window) / 16_000),
        }
        if relative_words is not None:
            segment["words"] = [
                {"text": word["text"], "start": start + word["start"], "end": start + word["end"]}
                for word in relative_words
            ]
        return [segment]

    def _transcribe_longform_unlocked(
        self,
        audio_path: str,
        progress_callback: Callable[[float, float | None, float | None], None] | None = None,
    ) -> list[TranscriptionSegment]:
        if self.model is None:
            raise RuntimeError("Модель не загружена")

        import soundfile as sf
        import torch

        sample_rate = 16000
        samples, sr = sf.read(audio_path, dtype="float32", always_2d=True)
        audio = torch.from_numpy(samples.T.copy())
        audio = audio.mean(0)

        if sr != sample_rate:
            import torchaudio

            audio = torchaudio.functional.resample(audio, sr, sample_rate)

        chunk_size = 20 * sample_rate
        total = int(audio.shape[0])
        total_seconds = float(total) / sample_rate if sample_rate else 0.0
        model = cast(Any, self.model)

        def overlap_chunks(
            regions: list[tuple[float, float]],
            *,
            max_chunk_seconds: float,
        ) -> list[AudioChunk]:
            return plan_audio_chunks(
                audio,
                regions,
                sample_rate=sample_rate,
                max_chunk_seconds=max_chunk_seconds,
            )

        def warn(reason: str) -> None:
            if self._logger is not None:
                self._logger(f"Внимание: {reason}")

        if self.segmentation_strategy == "vad":
            vad_device = resolve_vad_device(ASR_VAD_DEVICE)
            hf_token, segmenter_key = pyannote_vad_key(vad_device)
            try:
                boundaries = self._vad_cache.segment(
                    segmenter_key,
                    audio_path,
                    audio_duration=total_seconds,
                    token=hf_token,
                    device=vad_device,
                )
                if vad_regions_miss_active_audio(audio, boundaries, sample_rate=sample_rate):
                    self.segmentation_mode = "overlap_chunks"
                    self.segmentation_fallback_reason = (
                        "VAD пропустил длинный участок с активным звуком; "
                        "использовано полное разбиение по тихим точкам с перекрытием"
                    )
                    warn(self.segmentation_fallback_reason)
                    chunks = overlap_chunks([(0.0, total_seconds)], max_chunk_seconds=20.0)
                else:
                    self.segmentation_mode = "vad"
                    self.segmentation_fallback_reason = None
                    # PyTorch-модель держит окна до 30 с (ONNX и MLX — до 20 с).
                    chunks = overlap_chunks(boundaries, max_chunk_seconds=30.0)
                    if self._logger is not None:
                        self._logger(
                            f"Речь найдена: участков — {len(boundaries)}, "
                            f"фрагментов для распознавания — {len(chunks)}"
                        )
            except Exception as exc:
                self.segmentation_mode = "overlap_chunks"
                if isinstance(exc, VadUnavailableError):
                    recovery_hint = (
                        "проверьте локальный кэш или HF_TOKEN и доступ к "
                        "pyannote/segmentation-3.0; "
                    )
                else:
                    recovery_hint = ""
                self.segmentation_fallback_reason = (
                    f"VAD недоступен ({type(exc).__name__}): "
                    f"{recovery_hint}использовано резервное разбиение "
                    "по тихим точкам с перекрытием"
                )
                warn(self.segmentation_fallback_reason)
                chunks = overlap_chunks([(0.0, total_seconds)], max_chunk_seconds=20.0)
        elif self.segmentation_strategy == "overlap_chunks":
            self.segmentation_mode = "overlap_chunks"
            self.segmentation_fallback_reason = (
                "VAD отключён настройкой ASR_SEGMENTATION_MODE: "
                "использовано разбиение по тихим точкам с перекрытием"
            )
            warn(self.segmentation_fallback_reason)
            chunks = overlap_chunks([(0.0, total_seconds)], max_chunk_seconds=20.0)
        else:
            self.segmentation_mode = "fixed_chunks"
            self.segmentation_fallback_reason = (
                "VAD отключён настройкой ASR_SEGMENTATION_MODE: "
                "использовано legacy-разбиение по 20 секунд без перекрытия"
            )
            warn(self.segmentation_fallback_reason)
            chunks = plan_audio_chunks(
                audio,
                [
                    (float(start) / sample_rate, float(min(start + chunk_size, total)) / sample_rate)
                    for start in range(0, total, chunk_size)
                ],
                sample_rate=sample_rate,
                max_chunk_seconds=20.0,
                overlap_seconds=0.0,
            )

        def decode(chunk: AudioChunk) -> tuple[str, list[TranscriptionWord] | None]:
            start = chunk.decode_start_sample
            end = chunk.decode_end_sample
            wav = audio[start:end].to(model._device).to(model._dtype).unsqueeze(0)
            length = torch.full([1], wav.shape[-1], device=model._device)
            encoded, encoded_len = model.forward(wav, length)
            text, relative_words = self._decode_chunk(model, encoded, encoded_len, length)
            return text, absolute_words(relative_words, float(start) / sample_rate)

        try:
            with torch.inference_mode():
                # Окна короче 0.1 с (1600 отсчётов) энкодер GigaAM не принимает.
                return assemble_segments(
                    chunks,
                    decode,
                    total_seconds=total_seconds,
                    min_chunk_samples=1600,
                    progress_callback=progress_callback,
                )
        finally:
            self._empty_cache()

    def unload(self) -> None:
        with self._inference_lock:
            self.model = None
            self._vad_cache.reset()
            self.segmentation_mode = "not_run"
            self.segmentation_fallback_reason = None
            self._empty_cache()

    def is_loaded(self) -> bool:
        return self.model is not None

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            backend=self.name,
            model=self.model_revision,
            device=self.device or "N/A",
            segmentation_mode=self.segmentation_mode,
            segmentation_fallback_reason=self.segmentation_fallback_reason,
        )
