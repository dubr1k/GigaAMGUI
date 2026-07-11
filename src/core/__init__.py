"""
Core модули для обработки транскрибации
"""

import importlib

_LAZY = {
    "ModelLoader": (".model_loader", "ModelLoader"),
    "TranscriptionProcessor": (".processor", "TranscriptionProcessor"),
    "ProgressEvent": (".progress", "ProgressEvent"),
    "ProgressPlan": (".progress", "ProgressPlan"),
    "ProgressStage": (".progress", "ProgressStage"),
    "ProgressCallback": (".progress", "ProgressCallback"),
}

__all__ = list(_LAZY.keys())


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
