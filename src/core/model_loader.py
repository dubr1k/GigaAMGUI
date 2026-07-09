"""
Модуль загрузки и управления моделью GigaAM
"""

import os
import sys

import torch
import gigaam

from ..config import HF_TOKEN


class ModelLoader:
    """Класс для загрузки и управления моделью GigaAM"""

    def __init__(self):
        self.model = None
        self.device = None

    def _bundled_download_root(self) -> str | None:
        """Return bundled GigaAM model directory when the app ships with weights."""
        roots = []
        frozen_root = getattr(sys, "_MEIPASS", None)
        if frozen_root:
            roots.append(frozen_root)
        roots.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

        for root in roots:
            candidate = os.path.join(root, "models", "gigaam")
            ckpt = os.path.join(candidate, "v3_e2e_rnnt.ckpt")
            tokenizer = os.path.join(candidate, "v3_e2e_rnnt_tokenizer.model")
            if os.path.isfile(ckpt) and os.path.isfile(tokenizer):
                return candidate
        return None

    def _select_device(self) -> str:
        """Выбирает устройство вычисления.

        Приоритет отдаётся варианту, выбранному пользователем при первом запуске
        (CPU / GPU / GPU 50xx) — он определяет, какая сборка torch активирована.
        Если пользователь выбрал GPU-вариант, но CUDA почему-то недоступна
        (нет драйвера/видеокарты), безопасно откатываемся на CPU.
        """
        try:
            from ..utils.runtime_manager import get_selected_variant, torch_device_for
            preferred = torch_device_for(get_selected_variant())
        except Exception:
            preferred = None

        if preferred == "cuda":
            return "cuda" if torch.cuda.is_available() else "cpu"
        if preferred == "cpu":
            return "cpu"

        # Вариант не выбран — авто-определение по возможностям torch.
        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch, "xpu") and torch.xpu.is_available():
            return "xpu"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
        return "cpu"

    def load_model(self, logger=None):
        """
        Загружает модель GigaAM-v3 (e2e_rnnt) через gigaam.load_model.

        Returns:
            bool: True если загрузка успешна, False иначе
        """
        if self.model is not None:
            return True

        if logger:
            logger("Инициализация модели GigaAM-v3 (e2e_rnnt)...")
            logger("Это может занять несколько минут при первом запуске (скачивание весов).")

        try:
            self.device = self._select_device()
            if logger:
                logger(f"Устройство вычисления: {self.device.upper()}")

            use_fp16 = self.device != "cpu"
            download_root = self._bundled_download_root()
            if logger and download_root:
                logger("Используется встроенная модель GigaAM из приложения.")
            self.model = gigaam.load_model(
                "e2e_rnnt",
                fp16_encoder=use_fp16,
                device=self.device,
                download_root=download_root,
            )

            if logger:
                logger("Модель успешно загружена!")
            return True

        except Exception as e:
            if logger:
                logger(f"КРИТИЧЕСКАЯ ОШИБКА загрузки модели:\n{str(e)}")
            return False

    def _empty_cache(self):
        """Освобождает кэш ускорителя."""
        try:
            if self.device == "cuda" and torch.cuda.is_available():
                torch.cuda.empty_cache()
            elif self.device == "mps" and hasattr(torch, "mps"):
                torch.mps.empty_cache()
        except Exception:
            pass

    def transcribe_longform(self, audio_path: str):
        """
        Транскрибирует длинное аудио чанками по 20 сек (без ffmpeg subprocess).
        """
        import traceback as _tb
        if self.model is None:
            raise RuntimeError("Модель не загружена")

        import soundfile as sf
        import torchaudio
        SAMPLE_RATE = 16000

        # TorchAudio 2.9+ delegates torchaudio.load() to TorchCodec.  Portable
        # builds intentionally do not ship TorchCodec because its FFmpeg linkage
        # is fragile, so decode the normalized WAV produced by AudioConverter
        # with libsndfile instead.  This path is identical on Windows and macOS.
        samples, sr = sf.read(audio_path, dtype="float32", always_2d=True)
        wav_data = torch.from_numpy(samples.T.copy())
        audio = wav_data.mean(0)
        if sr != SAMPLE_RATE:
            audio = torchaudio.functional.resample(audio, sr, SAMPLE_RATE)

        chunk_size = 20 * SAMPLE_RATE
        results = []
        total = audio.shape[0]
        start = 0

        device = self.model._device
        dtype = self.model._dtype

        try:
            with torch.inference_mode():
                while start < total:
                    end = min(start + chunk_size, total)
                    chunk = audio[start:end]

                    if chunk.shape[0] < 1600:
                        start = end
                        continue

                    wav = chunk.to(device).to(dtype).unsqueeze(0)
                    length = torch.full([1], wav.shape[-1], device=device)
                    encoded, encoded_len = self.model.forward(wav, length)
                    decode_result = self.model.decoding.decode(self.model.head, encoded, encoded_len)
                    text = decode_result[0]
                    if isinstance(text, tuple):
                        text = text[0] if text else ''
                    if not isinstance(text, str):
                        text = str(text)

                    if text.strip():
                        results.append({
                            'transcription': text,
                            'boundaries': (start / SAMPLE_RATE, end / SAMPLE_RATE),
                        })

                    start = end
        except Exception as e:
            raise RuntimeError(f"Chunk transcription error at {start}/{total}: {e}\n{_tb.format_exc()}")
        finally:
            self._empty_cache()

        return results

    def unload(self):
        """Выгружает модель и освобождает память."""
        self.model = None
        self._empty_cache()

    def is_loaded(self) -> bool:
        """Проверяет, загружена ли модель"""
        return self.model is not None
