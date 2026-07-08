"""
Утилиты для работы приложения.

Импорты сделаны ЛЕНИВЫМИ (PEP 562): часть модулей тянет torch, а он на старте
приложения должен загружаться только ПОСЛЕ выбора и активации нужной сборки
(см. runtime_manager). Поэтому ``from src.utils import runtime_manager`` не должен
приводить к преждевременному импорту torch — обращение к torch-зависимым именам
происходит лениво, при первом доступе.
"""

import importlib

# Имя атрибута -> (модуль, имя_в_модуле)
_LAZY = {
    'AudioConverter':          ('.audio_converter', 'AudioConverter'),
    'DiarizationManager':      ('.diarization', 'DiarizationManager'),
    'SpeakerSegment':          ('.diarization', 'SpeakerSegment'),
    'get_diarization_manager': ('.diarization', 'get_diarization_manager'),
    'AppLogger':               ('.logger', 'AppLogger'),
    'LLMClient':               ('.llm_client', 'LLMClient'),
    'LLMSettings':             ('.llm_client', 'LLMSettings'),
    'LoggerAdapter':           ('.logger', 'LoggerAdapter'),
    'DownloadResult':          ('.media_downloader', 'DownloadResult'),
    'MediaDownloader':         ('.media_downloader', 'MediaDownloader'),
    'ProcessingStats':         ('.processing_stats', 'ProcessingStats'),
    'apply_pyannote_patch':    ('.pyannote_patch', 'apply_pyannote_patch'),
    'TimeFormatter':           ('.time_formatter', 'TimeFormatter'),
    'apply_torch_load_patch':  ('.torch_patch', 'apply_torch_load_patch'),
    'UserSettings':            ('.user_settings', 'UserSettings'),
}

__all__ = list(_LAZY)


def __getattr__(name):
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(target[0], __name__)
    value = getattr(module, target[1])
    globals()[name] = value  # кэшируем, чтобы повторный доступ был быстрым
    return value


def __dir__():
    return sorted(list(globals().keys()) + __all__)
