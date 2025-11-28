"""
Точка входа приложения GigaAM v3 Transcriber
"""

import warnings
import customtkinter as ctk
from src.utils.pyannote_patch import apply_pyannote_patch
from src.gui import GigaTranscriberApp

# Подавляем предупреждения от зависимостей
warnings.filterwarnings("ignore", category=UserWarning, module="pyannote")
warnings.filterwarnings("ignore", category=UserWarning, module="speechbrain")
warnings.filterwarnings("ignore", category=UserWarning, module="torchaudio")
warnings.filterwarnings("ignore", message=".*torchaudio.*deprecated.*")
warnings.filterwarnings("ignore", message=".*speechbrain.pretrained.*deprecated.*")

# Применяем патч для pyannote.audio перед запуском
apply_pyannote_patch()

# Настройка внешнего вида
ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")


def main():
    """Главная функция запуска приложения"""
    app = GigaTranscriberApp()
    app.mainloop()


if __name__ == "__main__":
    main()