"""
GUI модули приложения (PyQt6).

Импорт app_qt отложен: он тянет core -> torch, а torch на старте должен
грузиться только ПОСЛЕ выбора и активации нужной сборки. Благодаря ленивому
доступу ``from src.gui.device_dialog import ...`` (диалог выбора устройства)
не приводит к преждевременному импорту torch.
"""

import importlib

_LAZY = {
    'GigaTranscriberQtApp': ('.app_qt', 'GigaTranscriberQtApp'),
    'run_qt_app':           ('.app_qt', 'run_qt_app'),
}

__all__ = list(_LAZY)


def __getattr__(name):
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(target[0], __name__)
    value = getattr(module, target[1])
    globals()[name] = value
    return value


def __dir__():
    return sorted(list(globals().keys()) + __all__)
