"""
Модуль для сохранения пользовательских настроек приложения
"""

import json
import os
from pathlib import Path
from typing import Optional


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
        """Загрузка настроек из файла"""
        if os.path.exists(self.settings_file):
            try:
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Ошибка загрузки настроек: {e}")
                return {}
        return {}
    
    def _save_settings(self):
        """Сохранение настроек в файл"""
        try:
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(self.settings, f, ensure_ascii=False, indent=2)
        except Exception as e:
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

