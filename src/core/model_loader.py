"""
Модуль загрузки и управления моделью GigaAM
"""

import torch
from transformers import AutoModel
from ..config import MODEL_NAME, MODEL_REVISION, HF_TOKEN


class ModelLoader:
    """Класс для загрузки и управления моделью GigaAM"""
    
    def __init__(self):
        self.model = None
        self.device = None

    def _select_device(self) -> str:
        """Выбирает устройство вычисления в порядке приоритета."""
        # Приоритет: CUDA (NVIDIA) > XPU (Intel) > MPS (Apple) > CPU
        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch, "xpu") and torch.xpu.is_available():
            return "xpu"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
        return "cpu"
        
    def load_model(self, logger=None):
        """
        Загружает модель GigaAM-v3
        
        Args:
            logger: функция для логирования (опционально)
            
        Returns:
            bool: True если загрузка успешна, False иначе
        """
        if self.model is not None:
            return True
            
        if logger:
            logger("Инициализация модели GigaAM-v3 (e2e_rnnt)...")
            logger("Это может занять несколько минут при первом запуске (скачивание весов).")
        
        try:
            # Определение устройства: CUDA > XPU (Intel) > MPS (Apple) > CPU
            self.device = self._select_device()
            if logger:
                logger(f"Устройство вычисления: {self.device.upper()}")
            
            # Загружаем модель с токеном
            model_kwargs = {
                "trust_remote_code": True
            }
            if HF_TOKEN and HF_TOKEN.startswith("hf_"):
                model_kwargs["token"] = HF_TOKEN
            
            loaded_model = AutoModel.from_pretrained(
                MODEL_NAME,
                revision=MODEL_REVISION,
                **model_kwargs
            )

            try:
                self.model = loaded_model.to(self.device)
            except Exception as device_error:
                # Безопасный откат: если выбранный ускоритель недоступен для части графа,
                # запускаем на CPU, чтобы приложение не падало.
                if logger:
                    logger(
                        f"ПРЕДУПРЕЖДЕНИЕ: Не удалось перенести модель на {self.device.upper()}: "
                        f"{device_error}. Переключаюсь на CPU."
                    )
                self.device = "cpu"
                self.model = loaded_model.to(self.device)
            
            if logger:
                logger("Модель успешно загружена!")
            return True
            
        except Exception as e:
            if logger:
                logger(f"КРИТИЧЕСКАЯ ОШИБКА загрузки модели:\n{str(e)}")
            return False
    
    def _empty_cache(self):
        """Освобождает кэш ускорителя, чтобы снизить фрагментацию памяти между файлами."""
        try:
            if self.device == "cuda" and torch.cuda.is_available():
                torch.cuda.empty_cache()
            elif self.device == "mps" and hasattr(torch, "mps"):
                torch.mps.empty_cache()
        except Exception:
            pass  # очистка кэша не критична

    def transcribe_longform(self, audio_path: str):
        """
        Транскрибирует длинное аудио

        Args:
            audio_path: путь к аудио файлу

        Returns:
            list: список utterances с транскрипцией и таймкодами
        """
        if self.model is None:
            raise RuntimeError("Модель не загружена")

        try:
            return self.model.transcribe_longform(audio_path)
        finally:
            # Чистим кэш ускорителя после каждого файла (длинные файлы фрагментируют память)
            self._empty_cache()

    def unload(self):
        """Выгружает модель и освобождает память ускорителя."""
        self.model = None
        self._empty_cache()

    def is_loaded(self) -> bool:
        """Проверяет, загружена ли модель"""
        return self.model is not None