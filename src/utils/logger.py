"""
Модуль логирования для приложения
Создает логи в папке logs/ с организацией по датам и времени
"""

import logging
import os
from datetime import datetime
from pathlib import Path


class AppLogger:
    """Класс для настройки и управления логированием"""
    
    def __init__(self, base_dir: str = None):
        """
        Args:
            base_dir: базовая директория проекта (по умолчанию - корень проекта)
        """
        if base_dir is None:
            # Определяем корень проекта (на 2 уровня выше от utils)
            base_dir = Path(__file__).parent.parent.parent
        
        self.base_dir = Path(base_dir)
        self.logs_dir = self.base_dir / "logs"
        
        # Создаем структуру папок
        self._setup_directories()
        
        # Настраиваем логгер
        self.logger = self._setup_logger()
    
    def _setup_directories(self):
        """Создает структуру папок для логов"""
        # Текущая дата и время
        now = datetime.now()
        date_folder = now.strftime("%d-%m-%Y")
        time_folder = now.strftime("%H-%M-%S")
        
        # Путь: logs/dd-mm-yyyy/HH-MM-SS/
        self.session_dir = self.logs_dir / date_folder / time_folder
        self.session_dir.mkdir(parents=True, exist_ok=True)
        
        # Файлы логов
        self.main_log = self.session_dir / "app.log"
        self.errors_log = self.session_dir / "errors.log"
    
    def _setup_logger(self) -> logging.Logger:
        """Настраивает и возвращает логгер"""
        logger = logging.getLogger("GigaAM")
        logger.setLevel(logging.DEBUG)
        
        # Очищаем предыдущие handlers если есть
        logger.handlers.clear()
        
        # Формат логов
        formatter = logging.Formatter(
            '[%(asctime)s] %(levelname)-8s | %(message)s',
            datefmt='%H:%M:%S'
        )
        
        # Handler для основного лога (все сообщения)
        file_handler = logging.FileHandler(
            self.main_log, 
            encoding='utf-8', 
            mode='a'
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        
        # Handler для ошибок (только WARNING и выше)
        error_handler = logging.FileHandler(
            self.errors_log, 
            encoding='utf-8', 
            mode='a'
        )
        error_handler.setLevel(logging.WARNING)
        error_handler.setFormatter(formatter)
        logger.addHandler(error_handler)
        
        # Handler для консоли (опционально)
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        
        return logger
    
    def get_logger(self) -> logging.Logger:
        """Возвращает настроенный логгер"""
        return self.logger
    
    def get_session_dir(self) -> Path:
        """Возвращает путь к папке текущей сессии"""
        return self.session_dir
    
    def log_session_start(self):
        """Логирует начало сессии"""
        self.logger.info("=" * 60)
        self.logger.info("GigaAM v3 Transcriber - Запуск приложения")
        self.logger.info(f"Сессия: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
        self.logger.info(f"Логи сохраняются в: {self.session_dir}")
        self.logger.info("=" * 60)
    
    def log_session_end(self):
        """Логирует завершение сессии"""
        self.logger.info("=" * 60)
        self.logger.info("Сессия завершена")
        self.logger.info("=" * 60)
    
    @staticmethod
    def cleanup_old_logs(base_dir: str = None, days: int = 30):
        """
        Удаляет логи старше указанного количества дней
        
        Args:
            base_dir: базовая директория проекта
            days: количество дней (логи старше будут удалены)
        """
        if base_dir is None:
            base_dir = Path(__file__).parent.parent.parent
        
        logs_dir = Path(base_dir) / "logs"
        if not logs_dir.exists():
            return
        
        from datetime import timedelta
        cutoff_date = datetime.now() - timedelta(days=days)
        
        deleted_count = 0
        for date_folder in logs_dir.iterdir():
            if not date_folder.is_dir():
                continue
            
            try:
                # Парсим имя папки (dd-mm-yyyy)
                folder_date = datetime.strptime(date_folder.name, "%d-%m-%Y")
                
                if folder_date < cutoff_date:
                    # Удаляем старую папку
                    import shutil
                    shutil.rmtree(date_folder)
                    deleted_count += 1
            except (ValueError, OSError):
                # Пропускаем папки с неверным форматом или ошибками
                continue
        
        if deleted_count > 0:
            print(f"Удалено {deleted_count} старых папок с логами")


class LoggerAdapter:
    """Адаптер для совместимости с существующим кодом"""
    
    def __init__(self, logger: logging.Logger, gui_callback=None):
        """
        Args:
            logger: экземпляр logging.Logger
            gui_callback: функция для вывода в GUI (опционально)
        """
        self.logger = logger
        self.gui_callback = gui_callback
    
    def __call__(self, message: str, level: str = "info"):
        """
        Логирует сообщение
        
        Args:
            message: текст сообщения
            level: уровень логирования (info, warning, error, debug)
        """
        # Логируем в файл
        log_method = getattr(self.logger, level.lower(), self.logger.info)
        log_method(message)
        
        # Если есть GUI callback, выводим и туда
        if self.gui_callback:
            self.gui_callback(message)
    
    def info(self, message: str):
        """Логирует INFO сообщение"""
        self(message, "info")
    
    def warning(self, message: str):
        """Логирует WARNING сообщение"""
        self(message, "warning")
    
    def error(self, message: str):
        """Логирует ERROR сообщение"""
        self(message, "error")
    
    def debug(self, message: str):
        """Логирует DEBUG сообщение"""
        self(message, "debug")


def setup_logger(base_dir: str = None) -> logging.Logger:
    """
    Быстрая функция для создания логгера
    Используется в CLI и API
    
    Args:
        base_dir: базовая директория проекта
        
    Returns:
        logging.Logger: настроенный логгер
    """
    app_logger = AppLogger(base_dir=base_dir)
    app_logger.log_session_start()
    return app_logger.get_logger()
