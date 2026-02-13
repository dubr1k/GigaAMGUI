"""
Точка входа GUI приложения GigaAM v3 Transcriber (PyQt6)
"""

import warnings

from src.utils.pyannote_patch import apply_pyannote_patch
from src.gui import run_qt_app

# Подавляем предупреждения от зависимостей
warnings.filterwarnings("ignore", category=UserWarning, module="pyannote")
warnings.filterwarnings("ignore", category=UserWarning, module="speechbrain")
warnings.filterwarnings("ignore", category=UserWarning, module="torchaudio")
warnings.filterwarnings("ignore", message=".*torchaudio.*deprecated.*")
warnings.filterwarnings("ignore", message=".*speechbrain.pretrained.*deprecated.*")

# Применяем патч для pyannote.audio перед запуском
apply_pyannote_patch()


def main():
    """Главная функция запуска приложения"""
    run_qt_app()


if __name__ == "__main__":
    main()
