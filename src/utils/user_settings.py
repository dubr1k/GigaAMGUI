"""
Модуль для сохранения пользовательских настроек приложения
"""

import os
from typing import Optional

from .atomic_json import load_json, save_json_atomic


class UserSettings:
    """Класс для управления пользовательскими настройками"""
    
    def __init__(self, settings_file: str = "user_settings.json"):
        """
        Инициализация менеджера настроек
        
        Args:
            settings_file: путь к файлу с настройками
        """
        self.settings_file = settings_file
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
    
    def get_last_output_dir(self) -> Optional[str]:
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
    
    def get_last_files_dir(self) -> Optional[str]:
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

