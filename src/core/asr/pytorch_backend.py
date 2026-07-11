"""PyTorch backend using `gigaam` model API."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from typing import cast

from ...config import MODEL_NAME, MODEL_REVISION
from .types import BackendCapabilities, TranscriptionSegment


class PyTorchBackend:
    """ASR backend implemented via torch + gigaam."""

    name = "pytorch"

    def __init__(self, model: str | None = None, *, revision: str | None = None):
        self.model_name = model or MODEL_NAME
        self.model_revision = revision or MODEL_REVISION
        self.model = None
        self.device = None
        self._gigaam = None

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

            preferred = torch_device_for(get_selected_variant())
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

    def load(self, logger: Callable[[str], None] | None = None) -> bool:
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
            if not self.model or not self.device:
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
        start = 0
        total_seconds = float(total) / sample_rate if sample_rate else 0.0
        reported = 0.0
        model = cast(object, self.model)

        try:
            with torch.inference_mode():
                while start < total:
                    end = min(start + chunk_size, total)
                    if end - start < 1600:
                        start = end
                        continue

                    wav = audio[start:end].to(model._device).to(model._dtype).unsqueeze(0)
                    length = torch.full([1], wav.shape[-1], device=model._device)
                    encoded, encoded_len = model.forward(wav, length)
                    decode_result = model.decoding.decode(model.head, encoded, encoded_len)

                    text = ""
                    if isinstance(decode_result, (tuple, list)):
                        for candidate in decode_result:
                            if candidate is None:
                                continue
                            candidate_text = str(candidate).strip()
                            if candidate_text:
                                text = candidate_text
                                break
                    elif decode_result is not None:
                        text = str(decode_result).strip()

                    if text:
                        start_time = float(start) / sample_rate
                        end_time = float(end) / sample_rate
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

                    start = end

                if progress_callback is not None and total > 0 and reported < 1.0:
                    progress_callback(1.0, total_seconds, total_seconds)
        finally:
            self._empty_cache()

        return results

    def unload(self) -> None:
        self.model = None
        self._empty_cache()

    def is_loaded(self) -> bool:
        return self.model is not None

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            backend=self.name,
            model=self.model_revision,
            device=self.device or "N/A",
        )
