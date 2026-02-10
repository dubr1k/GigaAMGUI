"""
Модуль конвертации аудио/видео файлов
"""

import os
import subprocess
import json
from ..config import AUDIO_SAMPLE_RATE, AUDIO_CHANNELS


class AudioConverter:
    """Класс для конвертации медиа файлов в WAV формат"""
    
    def __init__(self, logger=None):
        """
        Args:
            logger: функция для логирования (опционально)
        """
        self.logger = logger or print
    
    @staticmethod
    def get_media_duration(filepath: str) -> float:
        """
        Получает длительность аудио/видео файла через ffprobe
        
        Args:
            filepath: путь к медиа файлу
            
        Returns:
            float: длительность в секундах, или 0 при ошибке
        """
        try:
            command = [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "json",
                filepath
            ]
            
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=True,
                startupinfo=startupinfo
            )
            
            data = json.loads(result.stdout)
            duration = float(data.get("format", {}).get("duration", 0))
            return duration
            
        except (subprocess.CalledProcessError, ValueError, KeyError, FileNotFoundError) as e:
            # Если ffprobe не работает, пробуем альтернативный метод
            try:
                command = [
                    "ffmpeg",
                    "-i", filepath,
                    "-f", "null",
                    "-"
                ]
                
                startupinfo = None
                if os.name == 'nt':
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    startupinfo=startupinfo
                )
                
                # Парсим вывод ffmpeg для получения длительности
                # Ищем строку типа "Duration: 00:01:23.45"
                import re
                duration_match = re.search(r'Duration: (\d{2}):(\d{2}):(\d{2}\.\d{2})', result.stderr)
                if duration_match:
                    hours = int(duration_match.group(1))
                    minutes = int(duration_match.group(2))
                    seconds = float(duration_match.group(3))
                    return hours * 3600 + minutes * 60 + seconds
                    
            except Exception:
                pass
            
            return 0.0
        
    def convert_to_wav(self, input_path: str, output_dir: str) -> str:
        """
        Конвертирует любой входной файл в 16kHz mono wav для модели
        
        Args:
            input_path: путь к входному файлу
            output_dir: директория для временного файла
            
        Returns:
            str: путь к конвертированному WAV файлу или None при ошибке
        """
        # Нормализуем путь (решает проблемы с относительными путями и символическими ссылками)
        input_path = os.path.abspath(os.path.expanduser(input_path))
        
        # Проверяем существование файла
        if not os.path.exists(input_path):
            self.logger(f"ОШИБКА: Файл не найден: {input_path}")
            return None
        
        if not os.path.isfile(input_path):
            self.logger(f"ОШИБКА: Путь не является файлом: {input_path}")
            return None
        
        # Создаем временный файл в папке вывода
        temp_filename = f"temp_{os.path.basename(input_path)}.wav"
        temp_wav = os.path.join(output_dir, temp_filename)
        
        self.logger(f"Конвертация {os.path.basename(input_path)} -> 16kHz WAV...")
        
        # Логируем реальный путь для отладки
        self.logger(f"DEBUG: Абсолютный путь к файлу: {input_path}")
        self.logger(f"DEBUG: Файл существует: {os.path.exists(input_path)}")
        
        try:
            # FFmpeg: -i input -ar 16000 (Hz) -ac 1 (mono) -y (overwrite)
            # Используем абсолютный путь для надежности
            # Убеждаемся, что путь - это строка (не Path объект)
            input_path_str = str(input_path) if not isinstance(input_path, str) else input_path
            command = [
                "ffmpeg", "-i", input_path_str,
                "-ar", str(AUDIO_SAMPLE_RATE),
                "-ac", str(AUDIO_CHANNELS),
                "-vn",  # убираем видео поток
                "-y",   # перезаписывать если есть
                temp_wav
            ]
            
            # Логируем команду для отладки (без полного пути для краткости)
            self.logger(f"DEBUG: Команда FFmpeg: ffmpeg -i [файл] -ar {AUDIO_SAMPLE_RATE} -ac {AUDIO_CHANNELS} -vn -y [выход]")
            
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                startupinfo=startupinfo
            )
            
            if result.returncode != 0:
                # Выводим детальную информацию об ошибке FFmpeg
                self.logger(f"Ошибка FFmpeg: Command '{' '.join(command)}' returned non-zero exit status {result.returncode}.")
                stderr_text = (result.stderr or "").strip()
                if result.stderr:
                    # Логируем последние строки stderr для диагностики
                    error_lines = stderr_text.split('\n')
                    relevant_errors = error_lines[-5:] if len(error_lines) > 5 else error_lines
                    for line in relevant_errors:
                        if line.strip():
                            self.logger(f"  FFmpeg: {line}")
                # Понятное сообщение для типичных ошибок MP4
                if "moov atom not found" in stderr_text or "Invalid data found when processing input" in stderr_text:
                    self.logger("")
                    self.logger("Возможная причина: файл повреждён или загружен не до конца (в MP4 метаданные «moov» в конце — если файл обрезан, FFmpeg не может его прочитать).")
                    self.logger("Что попробовать: перезаписать/скачать файл заново, открыть в другом плеере и пересохранить, либо взять другой файл.")
                return None
            
            return temp_wav
            
        except subprocess.CalledProcessError as e:
            # Fallback для случая, если все же возникло исключение
            error_details = e.stderr if isinstance(e.stderr, str) else (e.stderr.decode('utf-8', errors='ignore') if e.stderr else '')
            self.logger(f"Ошибка FFmpeg (код {e.returncode}): {e}")
            if error_details:
                error_lines = error_details.strip().split('\n')
                relevant_errors = error_lines[-5:] if len(error_lines) > 5 else error_lines
                for line in relevant_errors:
                    if line.strip():
                        self.logger(f"  FFmpeg: {line}")
            return None
            
        except FileNotFoundError:
            self.logger("ОШИБКА: FFmpeg не найден! Установите ffmpeg и добавьте в PATH.")
            return None