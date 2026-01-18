"""
Monkey patching для pyannote.audio чтобы использовать soundfile вместо torchcodec на Windows

Также включает патч для PyTorch 2.6+ (weights_only=False)
"""

import os
import warnings
import numpy as np

# ВАЖНО: Применяем патч для torch.load ДО импорта других библиотек
from .torch_patch import apply_torch_load_patch

# Применяем патч сразу при импорте модуля
apply_torch_load_patch()


def apply_pyannote_patch():
    """Применяет патч для работы pyannote.audio через soundfile"""
    try:
        import soundfile as sf
        import torch
        
        # Патч для совместимости numpy 2.x с pyannote.audio (использует устаревший np.NaN)
        if not hasattr(np, 'NaN'):
            np.NaN = np.nan
        
        # Подавляем предупреждения о torchcodec
        warnings.filterwarnings("ignore", message=".*torchcodec.*")
        
        # Monkey patch для работы с pyannote.audio 4.0.2+
        try:
            from pyannote.audio import Pipeline
            import pyannote.audio.core.io as io_module
            
            # Создаем функцию-обертку для загрузки аудио через soundfile
            def _load_audio_with_soundfile(file_path):
                """Загружает аудио через soundfile и возвращает в формате pyannote"""
                audio_data, sample_rate = sf.read(file_path)
                # Конвертируем в torch tensor
                if isinstance(audio_data, np.ndarray):
                    audio_tensor = torch.from_numpy(audio_data).float()
                    if len(audio_tensor.shape) == 1:
                        audio_tensor = audio_tensor.unsqueeze(0)
                else:
                    audio_tensor = torch.tensor(audio_data).float()
                    if len(audio_tensor.shape) == 1:
                        audio_tensor = audio_tensor.unsqueeze(0)
                return {"waveform": audio_tensor, "sample_rate": sample_rate}
            
            # Сохраняем оригинальный __call__ метод Pipeline
            _original_pipeline_call = Pipeline.__call__
            
            # Патчим Pipeline.__call__ чтобы он загружал аудио через soundfile если это строка пути
            def _patched_pipeline_call(self, file, **kwargs):
                """Патченный метод Pipeline.__call__ для загрузки аудио через soundfile"""
                # Если file - это строка (путь к файлу), загружаем через soundfile
                if isinstance(file, (str, os.PathLike)):
                    file_path = str(file)
                    # Загружаем аудио через soundfile
                    audio_dict = _load_audio_with_soundfile(file_path)
                    # Вызываем оригинальный метод с предзагруженным аудио
                    return _original_pipeline_call(self, audio_dict, **kwargs)
                else:
                    # Если это уже словарь или другой тип, используем оригинальный метод
                    return _original_pipeline_call(self, file, **kwargs)
            
            # Применяем патч
            Pipeline.__call__ = _patched_pipeline_call
            
            # Если AudioDecoder все же используется где-то (для обратной совместимости)
            # создаем его как класс-заглушку
            if not hasattr(io_module, 'AudioDecoder'):
                class AudioDecoder:
                    """Заглушка AudioDecoder для обратной совместимости"""
                    def __init__(self, file_path):
                        result = _load_audio_with_soundfile(file_path)
                        self.waveform = result["waveform"]
                        self.sample_rate = result["sample_rate"]
                    
                    def __call__(self):
                        return {"waveform": self.waveform, "sample_rate": self.sample_rate}
                
                io_module.AudioDecoder = AudioDecoder
            
            print("Pyannote.audio патч успешно применен")
            
        except Exception as e:
            # Если не удалось сделать monkey patch, продолжаем с предупреждениями
            import traceback
            print(f"Предупреждение: не удалось применить monkey patch для pyannote.audio: {e}")
            traceback.print_exc()
            
    except ImportError as e:
        print(f"Не удалось импортировать soundfile для патча: {e}")