#!/usr/bin/env python3
"""
Скрипт запуска GUI приложения GigaAM v3 Transcriber (PyQt6 версия)
Строгий профессиональный дизайн
"""

import sys
import os

# Добавляем путь к модулям проекта
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.gui.app_qt import run_qt_app

if __name__ == "__main__":
    run_qt_app()
