"""
Точка входа GUI приложения GigaAM v3 Transcriber (PyQt6).

Порядок запуска важен: сначала выбирается и активируется нужная сборка PyTorch
(CPU / CUDA 12.4 / CUDA 12.8), и только ПОСЛЕ этого импортируются модули,
которые тянут torch. Иначе torch загрузился бы раньше подстановки sys.path.
"""

import sys
import warnings


def _torch_is_available() -> bool:
    """True, если torch уже можно импортировать без загрузки (вшит/установлен)."""
    import importlib.util
    try:
        return importlib.util.find_spec("torch") is not None
    except (ImportError, ValueError):
        return False


def main():
    """Главная функция запуска приложения."""
    warnings.filterwarnings("ignore", category=UserWarning, module="pyannote")
    warnings.filterwarnings("ignore", category=UserWarning, module="speechbrain")
    warnings.filterwarnings("ignore", category=UserWarning, module="torchaudio")
    warnings.filterwarnings("ignore", message=".*torchaudio.*deprecated.*")
    warnings.filterwarnings("ignore", message=".*speechbrain.pretrained.*deprecated.*")

    from PyQt6.QtGui import QGuiApplication
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication

    if QApplication.instance() is None:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    app = QApplication.instance() or QApplication(sys.argv)

    # torch НЕ вшит в портативную сборку — выбираем и при необходимости качаем.
    # Если torch уже доступен (dev-окружение или сборка с вшитым torch) — пропускаем.
    if not _torch_is_available():
        from src.utils import runtime_manager as rm
        from src.gui.device_dialog import ensure_device_ready

        variant = ensure_device_ready()
        if not variant:
            sys.exit(0)
        rm.activate(variant)

    from src.utils.pyannote_patch import apply_pyannote_patch
    apply_pyannote_patch()

    from src.gui import run_qt_app
    run_qt_app(app=app)


if __name__ == "__main__":
    main()
