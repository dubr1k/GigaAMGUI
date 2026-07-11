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
from collections.abc import Callable
from threading import Thread

from ..config import AUDIO_CHANNELS, AUDIO_SAMPLE_RATE

# Таймауты для проб длительности (защита от зависания на битых файлах)
_PROBE_TIMEOUT = 30       # ffprobe
_FFMPEG_PROBE_TIMEOUT = 120  # ffmpeg -f null (полное декодирование как fallback)


def _project_root() -> str:
    """Корень проекта: при EXE — рядом с _MEIPASS, при разработке — 3 уровня вверх от этого файла."""
    if getattr(sys, '_MEIPASS', None):
        return sys._MEIPASS
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _find_bundled_tool(tool_name: str) -> str | None:
    """Возвращает совместимый с текущей ОС bundled tool из bin/, если он есть."""
    root = _project_root()
    candidates = [f"{tool_name}.exe"] if os.name == 'nt' else [tool_name]

    for filename in candidates:
        candidate = os.path.join(root, 'bin', filename)
        if os.path.isfile(candidate):
            return candidate
    return None


def _find_ffmpeg() -> str:
    """Ищет ffmpeg: совместимый bundled bin/ → PATH."""
    return _find_bundled_tool('ffmpeg') or shutil.which("ffmpeg") or "ffmpeg"


def _find_ffprobe() -> str | None:
    """Ищет ffprobe: совместимый bundled bin/ → PATH. None если не найден."""
    return _find_bundled_tool('ffprobe') or shutil.which("ffprobe") or None


def _windows_startupinfo():
    """STARTUPINFO для скрытия консольного окна на Windows (no-op на других ОС)."""
    if os.name != 'nt':
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return startupinfo


def ffmpeg_available() -> bool:
    """Проверяет наличие совместимого ffmpeg в bundle/bin проекта или PATH."""
    ffmpeg = _find_ffmpeg()
    return os.path.isfile(ffmpeg) or shutil.which("ffmpeg") is not None


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

    def convert_to_wav(
        self,
        input_path: str,
        output_dir: str,
        progress_callback: Callable[[float | None], None] | None = None,
        media_duration: float | None = None,
    ) -> str | None:
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
            command = [
                _find_ffmpeg(),
                "-hide_banner",
                "-nostdin",
                "-i", str(input_path),
                "-ar", str(AUDIO_SAMPLE_RATE),
                "-ac", str(AUDIO_CHANNELS),
                "-vn",
                "-y",
                temp_wav,
                "-progress", "pipe:1",
                "-nostats",
            ]

            self.logger(
                f"DEBUG: Команда FFmpeg: ffmpeg -i [файл] -ar {AUDIO_SAMPLE_RATE} -ac {AUDIO_CHANNELS} -vn -y [выход] -progress pipe:1"
            )

            duration = media_duration if media_duration is not None and media_duration > 0 else 0.0
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                startupinfo=_windows_startupinfo(),
            )

            stderr_lines: list[str] = []

            def _drain_stderr(pipe):
                for line in iter(pipe.readline, ""):
                    stderr_lines.append(line)

            stderr_thread = None
            if process.stderr is not None:
                stderr_thread = Thread(target=_drain_stderr, args=(process.stderr,), daemon=True)
                stderr_thread.start()

            last_reported = -1.0
            reported_unknown = False

            if process.stdout is not None:
                for raw_line in iter(process.stdout.readline, ""):
                    if "=" not in raw_line:
                        continue
                    key, value = raw_line.rstrip("\n").split("=", 1)
                    parsed_seconds: float | None = None
                    if key == "out_time_ms":
                        try:
                            parsed_seconds = int(value) / 1_000_000
                        except ValueError:
                            parsed_seconds = None
                    elif key == "out_time_us":
                        try:
                            parsed_seconds = int(value) / 1_000_000
                        except ValueError:
                            parsed_seconds = None
                    if parsed_seconds is None or duration <= 0:
                        if duration <= 0 and progress_callback is not None and not reported_unknown:
                            progress_callback(None)
                            reported_unknown = True
                        continue
                    ratio = max(0.0, min(parsed_seconds / duration, 1.0))
                    if ratio < last_reported:
                        continue
                    if ratio <= last_reported + 1e-9:
                        continue
                    last_reported = ratio
                    if progress_callback is not None:
                        progress_callback(ratio)

            if duration <= 0 and progress_callback is not None and not reported_unknown:
                progress_callback(None)

            returncode = process.wait()
            if stderr_thread is not None:
                stderr_thread.join(timeout=1.0)

            if returncode != 0:
                stderr_text = "".join(stderr_lines).strip()
                self.logger(f"Ошибка FFmpeg: код возврата {returncode}")
                self._log_ffmpeg_tail(stderr_text)
                if "moov atom not found" in stderr_text or "Invalid data found when processing input" in stderr_text:
                    self.logger("")
                    self.logger("Возможная причина: файл повреждён или загружен не до конца (в MP4 метаданные «moov» в конце — если файл обрезан, FFmpeg не может его прочитать).")
                    self.logger("Что попробовать: перезаписать/скачать файл заново, открыть в другом плеере и пересохранить, либо взять другой файл.")
                return None

            if progress_callback is not None and duration > 0 and last_reported < 1.0:
                progress_callback(1.0)
            return temp_wav

        except FileNotFoundError:
            self.logger("ОШИБКА: FFmpeg не найден в bundle/bin приложения и PATH.")
            return None
        except OSError as exc:
            self.logger(f"ОШИБКА: не удалось запустить FFmpeg ({exc}).")
            self.logger("Проверьте, что рядом с приложением нет несовместимого ffmpeg для другой ОС/архитектуры.")
            return None
