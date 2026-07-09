"""
Модуль для сохранения пользовательских настроек приложения
"""

import os
import sys
from pathlib import Path

from .atomic_json import load_json, save_json_atomic


def _default_settings_file() -> str:
    override = os.environ.get("GIGAAM_CONFIG_DIR")
    if override:
        base = Path(override)
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "GigaAMTranscriber"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "GigaAMTranscriber"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "GigaAMTranscriber"
    base.mkdir(parents=True, exist_ok=True)
    return str(base / "user_settings.json")


class UserSettings:
    """Класс для управления пользовательскими настройками"""

    def __init__(self, settings_file: str | os.PathLike | None = None):
        """
        Инициализация менеджера настроек

        Args:
            settings_file: путь к файлу с настройками
        """
        self.settings_file = str(settings_file) if settings_file is not None else _default_settings_file()
        self.settings: dict = self._load_settings()

    def _load_settings(self) -> dict:
        """Загрузка настроек из файла (устойчиво к битому JSON)"""
        return load_json(self.settings_file, {})

    def _save_settings(self):
        """Атомарное сохранение настроек в файл"""
        try:
            save_json_atomic(self.settings_file, self.settings)
        except OSError as e:
            print(f"Ошибка сохранения настроек: {e}")

    def get_last_output_dir(self) -> str | None:
        """
        Получить последний использованный путь для сохранения

        Returns:
            путь к директории или None, если не сохранен
        """
        path = self.settings.get("last_output_dir", "")
        # Проверяем, что путь существует
        if path and os.path.isdir(path):
            return path
        return None

    def set_last_output_dir(self, path: str):
        """
        Сохранить последний использованный путь для сохранения

        Args:
            path: путь к директории
        """
        if path and os.path.isdir(path):
            self.settings["last_output_dir"] = path
            self._save_settings()

    def get_last_files_dir(self) -> str | None:
        """
        Получить последний использованный путь для выбора файлов

        Returns:
            путь к директории или None, если не сохранен
        """
        path = self.settings.get("last_files_dir", "")
        # Проверяем, что путь существует
        if path and os.path.isdir(path):
            return path
        return None

    def set_last_files_dir(self, path: str):
        """
        Сохранить последний использованный путь для выбора файлов

        Args:
            path: путь к директории
        """
        if path:
            # Если это файл, берем директорию
            if os.path.isfile(path):
                path = os.path.dirname(path)
            elif os.path.isdir(path):
                pass
            else:
                return

            self.settings["last_files_dir"] = path
            self._save_settings()

    def get_value(self, key: str, default=None):
        """Вернуть произвольную настройку."""
        return self.settings.get(key, default)

    def set_value(self, key: str, value):
        """Сохранить произвольную настройку."""
        self.settings[key] = value
        self._save_settings()
