"""PyTorch backend using `gigaam` model API."""

from __future__ import annotations

import hashlib
import os
import sys
import threading
from collections.abc import Callable
from typing import cast

from ...config import (
    ASR_SEGMENTATION_MODE,
    ASR_VAD_DEVICE,
    MODEL_NAME,
    MODEL_REVISION,
)
from .types import BackendCapabilities, TranscriptionSegment
from .vad import PyannoteVadSegmenter, VadSegmenter, VadUnavailableError


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
        self._vad_segmenter_factory = vad_segmenter_factory or PyannoteVadSegmenter
        self._vad_segmenter: VadSegmenter | None = None
        self._vad_segmenter_key: tuple[bytes, str] | None = None
        self._vad_failure_key: tuple[bytes, str] | None = None
        self.segmentation_strategy = segmentation_mode or ASR_SEGMENTATION_MODE
        if self.segmentation_strategy not in {"vad", "fixed_chunks"}:
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
        ckpt = "v3_e2e_rnnt.ckpt"
        tokenizer = "v3_e2e_rnnt_tokenizer.model"

        for root in candidates:
            candidate = os.path.join(root, model_dir)
            ckpt_path = os.path.join(candidate, ckpt)
            tokenizer_path = os.path.join(candidate, tokenizer)
            if os.path.isfile(ckpt_path) and os.path.isfile(tokenizer_path):
                return candidate
        return None

    def _select_device(self) -> str:
        """Choose a compute backend, preserving existing runtime behavior."""
        try:
            from ...utils.runtime_manager import get_selected_variant, torch_device_for

            selected_variant = get_selected_variant()
            preferred = torch_device_for(selected_variant) if selected_variant else None
        except Exception:
            preferred = None

        import torch

        if preferred == "cuda":
            return "cuda" if torch.cuda.is_available() else "cpu"
        if preferred == "cpu":
            return "cpu"

        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch, "xpu") and torch.xpu.is_available():
            return "xpu"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
        return "cpu"

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

    def load(self, logger: Callable[[str], None] | None = None) -> bool:
        self._logger = logger
        if self.model is not None:
            return True

        try:

            import gigaam

            from ...utils.runtime_manager import get_selected_variant  # noqa: F401  pylint: disable=unused-import

            self._gigaam = gigaam

            if logger:
                logger("Инициализация модели GigaAM-v3...")
                logger("Это может занять несколько минут при первом запуске (скачивание весов).")

            self.device = self._select_device()
            if logger:
                logger(f"Устройство вычисления: {self.device.upper()}")

            use_fp16 = self.device != "cpu"
            self.model = gigaam.load_model(
                self.model_revision,
                fp16_encoder=use_fp16,
                device=self.device,
                download_root=self._bundled_download_root(),
            )

            if logger:
                logger("Модель успешно загружена!")
            return True
        except Exception as e:
            if logger:
                logger(f"КРИТИЧЕСКАЯ ОШИБКА загрузки модели:\n{e}")
            return False

    def _empty_cache(self):
        try:
            if not self.device:
                return

            import torch

            if self.device == "cuda" and torch.cuda.is_available():
                torch.cuda.empty_cache()
            elif self.device == "mps" and hasattr(torch, "mps"):
                torch.mps.empty_cache()
        except Exception:
            pass

    def transcribe_longform(
        self,
        audio_path: str,
        progress_callback: Callable[[float, float | None, float | None], None] | None = None,
    ) -> list[TranscriptionSegment]:
        """Serialize access to the shared GigaAM and pyannote models."""
        with self._inference_lock:
            return self._transcribe_longform_unlocked(audio_path, progress_callback)

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
        results: list[TranscriptionSegment] = []
        total = int(audio.shape[0])
        total_seconds = float(total) / sample_rate if sample_rate else 0.0
        reported = 0.0
        model = cast(object, self.model)

        def fixed_chunk_boundaries() -> list[tuple[float, float]]:
            return [
                (
                    float(start) / sample_rate,
                    float(min(start + chunk_size, total)) / sample_rate,
                )
                for start in range(0, total, chunk_size)
            ]

        hf_token = os.getenv("HF_TOKEN", "").strip() or None
        if self.segmentation_strategy == "vad":
            vad_device = ASR_VAD_DEVICE
            token_fingerprint = hashlib.sha256((hf_token or "").encode()).digest()
            segmenter_key = (token_fingerprint, vad_device)
            try:
                if self._vad_failure_key == segmenter_key:
                    raise VadUnavailableError("previous VAD initialization failed")
                if self._vad_segmenter is None or self._vad_segmenter_key != segmenter_key:
                    self._vad_segmenter = self._vad_segmenter_factory(
                        token=hf_token,
                        device=vad_device,
                    )
                    self._vad_segmenter_key = segmenter_key
                    self._vad_failure_key = None
                segmenter = self._vad_segmenter
                boundaries = segmenter.segment_file(
                    audio_path,
                    audio_duration=total_seconds,
                )
                self.segmentation_mode = "vad"
                self.segmentation_fallback_reason = None
            except Exception as exc:
                self._vad_segmenter = None
                self._vad_segmenter_key = None
                self._vad_failure_key = (
                    segmenter_key if isinstance(exc, VadUnavailableError) else None
                )
                self.segmentation_mode = "fixed_chunks"
                if isinstance(exc, VadUnavailableError):
                    recovery_hint = (
                        "проверьте локальный кэш или HF_TOKEN и доступ к "
                        "pyannote/segmentation-3.0; "
                    )
                else:
                    recovery_hint = ""
                self.segmentation_fallback_reason = (
                    f"VAD недоступен ({type(exc).__name__}): "
                    f"{recovery_hint}использовано резервное разбиение по 20 секунд"
                )
                if self._logger is not None:
                    self._logger(f"ПРЕДУПРЕЖДЕНИЕ: {self.segmentation_fallback_reason}")
                boundaries = fixed_chunk_boundaries()
        else:
            self.segmentation_mode = "fixed_chunks"
            self.segmentation_fallback_reason = (
                "VAD отключён настройкой ASR_SEGMENTATION_MODE: "
                "использовано разбиение по 20 секунд"
            )
            if self._logger is not None:
                self._logger(f"ПРЕДУПРЕЖДЕНИЕ: {self.segmentation_fallback_reason}")
            boundaries = fixed_chunk_boundaries()

        try:
            with torch.inference_mode():
                for raw_start, raw_end in boundaries:
                    start = max(0, int(raw_start * sample_rate))
                    end = min(total, int(raw_end * sample_rate))
                    if end - start < 1600:
                        continue

                    wav = audio[start:end].to(model._device).to(model._dtype).unsqueeze(0)
                    length = torch.full([1], wav.shape[-1], device=model._device)
                    encoded, encoded_len = model.forward(wav, length)
                    decode_result = model.decoding.decode(model.head, encoded, encoded_len)
                    text = self._decode_text(decode_result)

                    if text:
                        start_time = max(0.0, float(raw_start))
                        end_time = min(total_seconds, float(raw_end))
                        if end_time < start_time:
                            continue
                        results.append({
                            "transcription": text,
                            "boundaries": (start_time, end_time),
                        })

                    processed_seconds = float(end) / sample_rate
                    ratio = 1.0 if total <= 0 else min(processed_seconds / total_seconds, 1.0)
                    if progress_callback is not None and ratio >= reported:
                        progress_callback(ratio, processed_seconds, total_seconds)
                        reported = ratio

                if progress_callback is not None and total > 0 and reported < 1.0:
                    progress_callback(1.0, total_seconds, total_seconds)
        finally:
            self._empty_cache()

        return results

    def unload(self) -> None:
        with self._inference_lock:
            self.model = None
            self._vad_segmenter = None
            self._vad_segmenter_key = None
            self._vad_failure_key = None
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
