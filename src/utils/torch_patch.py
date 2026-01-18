"""
Патч для совместимости с PyTorch 2.6+

Решает проблему с параметром weights_only=True, который по умолчанию
включен в PyTorch 2.6+ и вызывает ошибки при загрузке старых моделей:

    Weights only load failed... Unsupported global: GLOBAL torch.torch_version.TorchVersion

Этот патч должен быть импортирован ДО любых других импортов torch или библиотек,
которые используют torch.load (transformers, pyannote.audio, и т.д.)
"""

import sys
import warnings

# Флаг для отслеживания применения патча
_TORCH_PATCH_APPLIED = False


def apply_torch_load_patch():
    """
    Применяет monkey-patch для torch.load, устанавливая weights_only=False по умолчанию.
    
    Это необходимо для совместимости со старыми моделями, которые были сохранены
    до введения безопасной загрузки в PyTorch 2.6+.
    
    Патч безопасно применяется только один раз.
    """
    global _TORCH_PATCH_APPLIED
    
    if _TORCH_PATCH_APPLIED:
        return True
    
    try:
        import torch
        
        # Проверяем версию PyTorch
        torch_version = tuple(int(x) for x in torch.__version__.split('+')[0].split('.')[:2])
        
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
