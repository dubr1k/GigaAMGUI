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
        # Создаем временный файл в папке вывода
        temp_filename = f"temp_{os.path.basename(input_path)}.wav"
        temp_wav = os.path.join(output_dir, temp_filename)
        
        self.logger(f"Конвертация {os.path.basename(input_path)} -> 16kHz WAV...")
        
        try:
            # FFmpeg: -i input -ar 16000 (Hz) -ac 1 (mono) -y (overwrite)
            command = [
                "ffmpeg", "-i", input_path,
                "-ar", str(AUDIO_SAMPLE_RATE),
                "-ac", str(AUDIO_CHANNELS),
                "-vn",  # убираем видео поток
                "-y",   # перезаписывать если есть
                temp_wav
            ]
            
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            
            subprocess.run(
                command,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                startupinfo=startupinfo
            )
            
            return temp_wav
            
        except subprocess.CalledProcessError as e:
            self.logger(f"Ошибка FFmpeg: {e}")
            return None
            
        except FileNotFoundError:
            self.logger("ОШИБКА: FFmpeg не найден! Установите ffmpeg и добавьте в PATH.")
            return None