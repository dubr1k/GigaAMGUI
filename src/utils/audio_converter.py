"""
Модуль конвертации аудио/видео файлов
"""

import json
import os
import re
import shutil
import subprocess
import sys
import uuid

from ..config import AUDIO_CHANNELS, AUDIO_SAMPLE_RATE

# Таймауты для проб длительности (защита от зависания на битых файлах)
_PROBE_TIMEOUT = 30       # ffprobe
_FFMPEG_PROBE_TIMEOUT = 120  # ffmpeg -f null (полное декодирование как fallback)


def _project_root() -> str:
    """Корень проекта: при EXE — рядом с _MEIPASS, при разработке — 3 уровня вверх от этого файла."""
    if getattr(sys, '_MEIPASS', None):
        return sys._MEIPASS
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _find_ffmpeg() -> str:
    """Ищет ffmpeg: EXE-сборка → bin/ проекта → PATH."""
    root = _project_root()
    for candidate in [
        os.path.join(root, 'bin', 'ffmpeg.exe'),
        os.path.join(root, 'bin', 'ffmpeg'),
    ]:
        if os.path.exists(candidate):
            return candidate
    return "ffmpeg"


def _find_ffprobe() -> str:
    """Ищет ffprobe: bin/ проекта → PATH. None если не найден (EXE без ffprobe → None)."""
    root = _project_root()
    for candidate in [
        os.path.join(root, 'bin', 'ffprobe.exe'),
        os.path.join(root, 'bin', 'ffprobe'),
    ]:
        if os.path.exists(candidate):
            return candidate
    if getattr(sys, '_MEIPASS', None):
        return None
    return shutil.which("ffprobe") or None


def _windows_startupinfo():
    """STARTUPINFO для скрытия консольного окна на Windows (no-op на других ОС)."""
    if os.name != 'nt':
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return startupinfo


def ffmpeg_available() -> bool:
    """Проверяет наличие ffmpeg в bundle/bin проекта или PATH."""
    ffmpeg = _find_ffmpeg()
    return os.path.isfile(ffmpeg) or shutil.which(ffmpeg) is not None


class AudioConverter:
    """Класс для конвертации медиа файлов в WAV формат"""

    def __init__(self, logger=None):
        """
        Args:
            logger: функция для логирования (опционально)
        """
        self.logger = logger or print

    def _log_ffmpeg_tail(self, stderr: str, lines: int = 5):
        """Логирует последние строки stderr ffmpeg для диагностики."""
        if not stderr:
            return
        error_lines = [line for line in stderr.strip().split('\n') if line.strip()]
        for line in error_lines[-lines:]:
            self.logger(f"  FFmpeg: {line}")

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
            ffprobe = _find_ffprobe()
            if not ffprobe:
                raise FileNotFoundError("ffprobe not available")
            command = [
                ffprobe,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "json",
                filepath
            ]

            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=True,
                startupinfo=_windows_startupinfo(),
                timeout=_PROBE_TIMEOUT,
            )

            data = json.loads(result.stdout)
            duration = float(data.get("format", {}).get("duration", 0))
            return duration

        except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
                ValueError, KeyError, FileNotFoundError):
            # Если ffprobe не работает, пробуем альтернативный метод
            try:
                command = [
                    _find_ffmpeg(),
                    "-i", filepath,
                    "-f", "null",
                    "-"
                ]

                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    startupinfo=_windows_startupinfo(),
                    timeout=_FFMPEG_PROBE_TIMEOUT,
                )

                # Парсим вывод ffmpeg для получения длительности
                # Ищем строку типа "Duration: 00:01:23.45"
                duration_match = re.search(r'Duration: (\d{2}):(\d{2}):(\d{2}\.\d{2})', result.stderr)
                if duration_match:
                    hours = int(duration_match.group(1))
                    minutes = int(duration_match.group(2))
                    seconds = float(duration_match.group(3))
                    return hours * 3600 + minutes * 60 + seconds

            except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError):
                pass

            return 0.0

    def convert_to_wav(self, input_path: str, output_dir: str) -> str | None:
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

        # Создаём временный файл в папке вывода с уникальным именем,
        # чтобы исключить коллизии при одинаковых basename / параллельных конвертациях.
        temp_filename = f"temp_{uuid.uuid4().hex}_{os.path.basename(input_path)}.wav"
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
                _find_ffmpeg(), "-i", input_path_str,
                "-ar", str(AUDIO_SAMPLE_RATE),
                "-ac", str(AUDIO_CHANNELS),
                "-vn",  # убираем видео поток
                "-y",   # перезаписывать если есть
                temp_wav
            ]

            # Логируем команду для отладки (без полного пути для краткости)
            self.logger(f"DEBUG: Команда FFmpeg: ffmpeg -i [файл] -ar {AUDIO_SAMPLE_RATE} -ac {AUDIO_CHANNELS} -vn -y [выход]")

            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                startupinfo=_windows_startupinfo(),
            )

            if result.returncode != 0:
                # Выводим детальную информацию об ошибке FFmpeg
                self.logger(f"Ошибка FFmpeg: код возврата {result.returncode}")
                stderr_text = (result.stderr or "").strip()
                self._log_ffmpeg_tail(stderr_text)
                # Понятное сообщение для типичных ошибок MP4
                if "moov atom not found" in stderr_text or "Invalid data found when processing input" in stderr_text:
                    self.logger("")
                    self.logger("Возможная причина: файл повреждён или загружен не до конца (в MP4 метаданные «moov» в конце — если файл обрезан, FFmpeg не может его прочитать).")
                    self.logger("Что попробовать: перезаписать/скачать файл заново, открыть в другом плеере и пересохранить, либо взять другой файл.")
                return None

            return temp_wav

        except FileNotFoundError:
            self.logger("ОШИБКА: FFmpeg не найден в bundle/bin приложения и PATH.")
            return None
