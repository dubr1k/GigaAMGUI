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
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
            if logger:
                logger(f"Устройство вычисления: {self.device.upper()}")
            
            # Загружаем модель с токеном
            model_kwargs = {
                "trust_remote_code": True
            }
            if HF_TOKEN and HF_TOKEN.startswith("hf_"):
                model_kwargs["token"] = HF_TOKEN
            
            self.model = AutoModel.from_pretrained(
                MODEL_NAME,
                revision=MODEL_REVISION,
                **model_kwargs
            ).to(self.device)
            
            if logger:
                logger("Модель успешно загружена!")
            return True
            
        except Exception as e:
            if logger:
                logger(f"КРИТИЧЕСКАЯ ОШИБКА загрузки модели:\n{str(e)}")
            return False
    
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
        
        return self.model.transcribe_longform(audio_path)
    
    def is_loaded(self) -> bool:
        """Проверяет, загружена ли модель"""
        return self.model is not None