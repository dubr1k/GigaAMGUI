"""
Monkey patching для pyannote.audio чтобы использовать soundfile вместо torchcodec на Windows

Также включает патч для PyTorch 2.6+ (weights_only=False)
и защитный адаптер для torchaudio 2.9+, где удалены устаревшие функции
backend-а. Штатные runtime приложения зафиксированы на совместимой ветке
torchaudio 2.6-2.8, но адаптер нужен для старых кэшей и внешних окружений.
"""

import importlib
import importlib.machinery
import logging
import os
import sys
import types
import warnings
from typing import NamedTuple

import numpy as np

# Mock torchcodec: pyannote.audio пытается импортировать torchcodec при загрузке
# io.py. В окружениях без torchcodec (портативная сборка) или со сломанным
# torchcodec (Docker, хрупкая линковка ffmpeg) вставляем заглушку, чтобы
# import не падал и не выводил warning. Аудио и так грузится через soundfile.
if "torchcodec" not in sys.modules:
    try:
        import torchcodec  # noqa: F401
    except Exception:
        _mock_tc = types.ModuleType("torchcodec")
        _mock_tc.__version__ = "0.0.0-mock"
        _mock_tc.__path__ = []
        _mock_tc.__spec__ = importlib.machinery.ModuleSpec("torchcodec", None)

        class _MockClass:
            pass

        _mock_decoders = types.ModuleType("torchcodec.decoders")
        _mock_decoders.__spec__ = importlib.machinery.ModuleSpec("torchcodec.decoders", None)
        _mock_decoders.AudioDecoder = _MockClass
        _mock_decoders.AudioStreamMetadata = _MockClass
        _mock_tc.AudioSamples = _MockClass
        _mock_tc.decoders = _mock_decoders
        sys.modules["torchcodec"] = _mock_tc
        sys.modules["torchcodec.decoders"] = _mock_decoders

# ВАЖНО: Применяем патч для torch.load ДО импорта других библиотек
from .torch_patch import apply_torch_load_patch

# Применяем патч сразу при импорте модуля
apply_torch_load_patch()

# Флаг идемпотентности: pyannote-патч применяется только один раз за процесс,
# чтобы повторные вызовы не оборачивали Pipeline.__call__ многократно.
_PYANNOTE_PATCH_APPLIED = False


class _FallbackAudioMetaData(NamedTuple):
    """Минимально совместимая замена удалённого torchaudio.AudioMetaData."""

    sample_rate: int
    num_frames: int
    num_channels: int
    bits_per_sample: int
    encoding: str


def _ensure_torchaudio_backend_modules(torchaudio) -> None:
    """Создаёт package-совместимые backend/common, не затирая реальные модули."""
    try:
        backend = importlib.import_module("torchaudio.backend")
    except ModuleNotFoundError as exc:
        if exc.name != "torchaudio.backend":
            raise
        backend = types.ModuleType("torchaudio.backend")
        backend.__package__ = "torchaudio"
        backend.__path__ = []
        backend.__spec__ = importlib.machinery.ModuleSpec("torchaudio.backend", loader=None, is_package=True)
        sys.modules["torchaudio.backend"] = backend
    if not hasattr(backend, "__path__"):
        backend.__path__ = []
    backend.__package__ = "torchaudio"
    backend_spec = getattr(backend, "__spec__", None)
    if backend_spec is None or backend_spec.submodule_search_locations is None:
        backend.__spec__ = importlib.machinery.ModuleSpec("torchaudio.backend", loader=None, is_package=True)
    torchaudio.backend = backend

    try:
        common = importlib.import_module("torchaudio.backend.common")
    except ModuleNotFoundError as exc:
        if exc.name != "torchaudio.backend.common":
            raise
        common = types.ModuleType("torchaudio.backend.common")
        common.__package__ = "torchaudio.backend"
        common.__spec__ = importlib.machinery.ModuleSpec("torchaudio.backend.common", loader=None)
        sys.modules["torchaudio.backend.common"] = common

    audio_metadata = getattr(torchaudio, "AudioMetaData", _FallbackAudioMetaData)
    if not hasattr(torchaudio, "AudioMetaData"):
        torchaudio.AudioMetaData = audio_metadata
    # vars() не вызывает deprecated __getattr__ реального torchaudio.common.
    if "AudioMetaData" not in vars(common):
        common.AudioMetaData = audio_metadata
    backend.common = common


def apply_torchaudio_backend_patch() -> bool:
    """
    Патч для совместимости старого pyannote.audio с torchaudio 2.9+.

    В torchaudio 2.8 API backend-а объявлен устаревшим, а в 2.9 удалены:
        - torchaudio.set_audio_backend()
        - torchaudio.get_audio_backend()
        - модуль torchaudio.backend

    pyannote.audio 3.1.1 дополнительно импортирует
    ``torchaudio.backend.common.AudioMetaData``. Поэтому backend должен быть не
    просто ModuleType, а package-совместимым модулем с зарегистрированным common.
    """
    try:
        import torchaudio

        # Функциональные заглушки нужны только новым torchaudio. Проверку
        # backend/common выполняем всегда: так исправляется и частично
        # применённый старый патч, оставивший backend обычным ModuleType.
        needs_patch = not hasattr(torchaudio, "set_audio_backend") or not hasattr(torchaudio, "get_audio_backend")

        # --- Заглушки для функций верхнего уровня ---
        if not hasattr(torchaudio, "set_audio_backend"):

            def set_audio_backend(backend):
                """No-op stub: set_audio_backend удалён в torchaudio 2.10+."""
                pass

            torchaudio.set_audio_backend = set_audio_backend

        if not hasattr(torchaudio, "get_audio_backend"):

            def get_audio_backend():
                """No-op stub: get_audio_backend удалён в torchaudio 2.10+. Возвращает 'soundfile'."""
                return "soundfile"

            torchaudio.get_audio_backend = get_audio_backend

        if not hasattr(torchaudio, "list_audio_backends"):

            def list_audio_backends():
                """No-op stub."""
                return ["soundfile"]

            torchaudio.list_audio_backends = list_audio_backends

        _ensure_torchaudio_backend_modules(torchaudio)
        backend = torchaudio.backend
        if needs_patch:
            backend.set_audio_backend = torchaudio.set_audio_backend
            backend.get_audio_backend = torchaudio.get_audio_backend
            for name in ("NoBackend", "Sox", "Soundfile"):
                if not hasattr(backend, name):
                    setattr(backend, name, type(name, (), {}))

        logging.getLogger(__name__).debug("Применён адаптер torchaudio backend")
        return True

    except ImportError:
        return False  # torchaudio не установлен — ничего не делаем
    except Exception as e:
        warnings.warn(f"Не удалось применить torchaudio backend патч: {e}", stacklevel=2)
        return False


# Применяем torchaudio патч сразу при импорте модуля
apply_torchaudio_backend_patch()


def apply_pyannote_patch():
    """Применяет патч для работы pyannote.audio через soundfile (идемпотентно)"""
    global _PYANNOTE_PATCH_APPLIED
    # После горячей смены runtime torchaudio/pyannote удаляются из sys.modules,
    # а этот модуль может остаться загруженным. Поэтому адаптер проверяется при
    # каждом вызове, а идемпотентность обёртки хранится на текущем Pipeline.
    apply_torch_load_patch()
    apply_torchaudio_backend_patch()
    try:
        import soundfile as sf
        import torch

        # Патч для совместимости numpy 2.x с pyannote.audio
        # pyannote использует устаревшие np.NaN и np.NAN которые удалены в NumPy 2.0
        # ВАЖНО: Применяем ДО импорта pyannote
        if not hasattr(np, "NaN"):
            np.NaN = np.nan
        if not hasattr(np, "NAN"):
            np.NAN = np.nan

        # Также добавляем в атрибуты модуля numpy напрямую
        np.NaN = np.nan
        np.NAN = np.nan

        # Подавляем предупреждения о torchcodec
        warnings.filterwarnings("ignore", message=".*torchcodec.*")

        # Monkey patch для работы с pyannote.audio 4.0.2+
        try:
            import pyannote.audio.core.io as io_module
            from pyannote.audio import Pipeline

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

            # Защита от повторной обёртки текущего Pipeline.
            if not getattr(Pipeline.__call__, "_gigaam_patched", False):
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

                _patched_pipeline_call._gigaam_patched = True
                # Применяем патч
                Pipeline.__call__ = _patched_pipeline_call

            # Если AudioDecoder все же используется где-то (для обратной совместимости)
            # создаем его как класс-заглушку
            if not hasattr(io_module, "AudioDecoder"):

                class AudioDecoder:
                    """Заглушка AudioDecoder для обратной совместимости"""

                    def __init__(self, file_path):
                        result = _load_audio_with_soundfile(file_path)
                        self.waveform = result["waveform"]
                        self.sample_rate = result["sample_rate"]

                    def __call__(self):
                        return {"waveform": self.waveform, "sample_rate": self.sample_rate}

                io_module.AudioDecoder = AudioDecoder

            _PYANNOTE_PATCH_APPLIED = True
            logging.getLogger(__name__).debug("Pyannote.audio патч успешно применён")

        except Exception as e:
            # Если не удалось сделать monkey patch, продолжаем с предупреждениями
            logging.getLogger(__name__).warning(
                "Не удалось применить monkey patch для pyannote.audio: %s", e, exc_info=True
            )

    except ImportError as e:
        logging.getLogger(__name__).warning("Не удалось импортировать soundfile для патча: %s", e)
