"""
Патч для совместимости с PyTorch 2.6+

Решает проблему с параметром weights_only=True, который по умолчанию
включен в PyTorch 2.6+ и вызывает ошибки при загрузке старых моделей:

    Weights only load failed... Unsupported global: GLOBAL torch.torch_version.TorchVersion

Этот патч должен быть импортирован ДО любых других импортов torch или библиотек,
которые используют torch.load (transformers, pyannote.audio, и т.д.)
"""

import os
import warnings

# Флаг для отслеживания применения патча
_TORCH_PATCH_APPLIED = False


def _parse_torch_version(version: str) -> tuple:
    """Извлекает (major, minor) из строки версии torch, устойчиво к суффиксам (+cu124, a0, dev)."""
    base = version.split('+')[0]
    parts = []
    for chunk in base.split('.')[:2]:
        digits = ''.join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 2:
        parts.append(0)
    return tuple(parts)


def apply_torch_load_patch():
    """
    Применяет monkey-patch для torch.load, устанавливая weights_only=False по умолчанию.

    Это необходимо для совместимости с моделями (GigaAM, pyannote), сохранёнными
    до введения безопасной загрузки в PyTorch 2.6+. Загрузка таких чекпойнтов
    использует pickle и выполняет произвольный код — приложение доверяет ТОЛЬКО
    моделям из контролируемых источников (HuggingFace ai-sage/GigaAM, pyannote).

    ВНИМАНИЕ: патч глобальный и ослабляет защиту torch.load для всего процесса.
    Если в этом же процессе загружаются недоверенные чекпойнты — отключите патч
    переменной окружения GIGAAM_DISABLE_TORCH_PATCH=1 (модели тогда могут не загрузиться).

    Патч безопасно применяется только один раз (идемпотентно).
    """
    global _TORCH_PATCH_APPLIED

    if _TORCH_PATCH_APPLIED:
        return True

    if os.getenv("GIGAAM_DISABLE_TORCH_PATCH", "").lower() in ("1", "true", "yes"):
        _TORCH_PATCH_APPLIED = True
        return True

    try:
        import torch

        # Проверяем версию PyTorch (устойчиво к dev/pre-release суффиксам)
        torch_version = _parse_torch_version(torch.__version__)

        # Патч нужен только для PyTorch 2.6+
        if torch_version >= (2, 6):
            # Сохраняем оригинальную функцию
            _original_torch_load = torch.load
            
            def _patched_torch_load(*args, **kwargs):
                """
                Патченная версия torch.load с weights_only=False по умолчанию.
                
                Если пользователь явно передал weights_only, используем его значение.
                Иначе устанавливаем weights_only=False для совместимости.
                """
                if 'weights_only' not in kwargs:
                    kwargs['weights_only'] = False
                return _original_torch_load(*args, **kwargs)
            
            # Применяем патч
            torch.load = _patched_torch_load
            
            # Подавляем предупреждения о небезопасной загрузке
            warnings.filterwarnings(
                "ignore",
                message=".*You are using `torch.load` with `weights_only=False`.*",
                category=FutureWarning
            )
            
            _TORCH_PATCH_APPLIED = True
            print(f"PyTorch {torch.__version__} patch: weights_only=False установлен по умолчанию")
            return True
        else:
            # Для старых версий PyTorch патч не нужен
            _TORCH_PATCH_APPLIED = True
            return True
            
    except ImportError:
        print("Предупреждение: PyTorch не установлен, патч не применен")
        return False
    except Exception as e:
        print(f"Предупреждение: не удалось применить PyTorch patch: {e}")
        return False


def get_patch_status():
    """Возвращает статус применения патча"""
    return _TORCH_PATCH_APPLIED
