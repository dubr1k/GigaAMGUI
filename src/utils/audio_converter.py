"""
Модуль конвертации аудио/видео файлов
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from threading import Event, Thread

from ..config import AUDIO_CHANNELS, AUDIO_SAMPLE_RATE
from .cancellation import CancelCheck, raise_if_cancelled

# Таймауты для проб длительности (защита от зависания на битых файлах)
_PROBE_TIMEOUT = 30       # ffprobe
_FFMPEG_PROBE_TIMEOUT = 120  # ffmpeg -f null (полное декодирование как fallback)
_CONVERSION_STALL_TIMEOUT = 600  # сек без активности ffmpeg → зависание, убиваем процесс (issue #20)


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


def _tool_executable(path: str) -> bool:
    """True, если бинарь реально запускается на этой ОС.

    Ловит случай, когда в bundle попал ffmpeg для другой ОС/архитектуры
    (напр. macOS-бинарь в Windows-сборке) — запуск такого даёт WinError 193
    (`OSError`), и мы должны отбросить кандидата, а не падать на нём.
    """
    try:
        result = subprocess.run(
            [path, "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            startupinfo=_windows_startupinfo(),
            timeout=10,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


_ffmpeg_cached: str | None = None


def _find_ffmpeg() -> str:
    """Рабочий ffmpeg: проверенный bundled bin/ → проверенный PATH → 'ffmpeg'.

    Кандидат из bin/ берётся, только если он реально запускается — иначе
    откатываемся на системный ffmpeg (это и чинит WinError 193, когда в bundle
    лежит бинарь под другую ОС). Результат кэшируется на процесс.
    """
    global _ffmpeg_cached
    if _ffmpeg_cached is not None:
        return _ffmpeg_cached
    for candidate in (_find_bundled_tool('ffmpeg'), shutil.which("ffmpeg")):
        if candidate and _tool_executable(candidate):
            _ffmpeg_cached = candidate
            return candidate
    _ffmpeg_cached = "ffmpeg"
    return _ffmpeg_cached


def _find_ffprobe() -> str | None:
    """Рабочий ffprobe: проверенный bundled bin/ → проверенный PATH. None если нет."""
    for candidate in (_find_bundled_tool('ffprobe'), shutil.which("ffprobe")):
        if candidate and _tool_executable(candidate):
            return candidate
    return None


def _windows_startupinfo():
    """STARTUPINFO для скрытия консольного окна на Windows (no-op на других ОС)."""
    if os.name != 'nt':
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return startupinfo


def ffmpeg_available() -> bool:
    """True, если найден РАБОЧИЙ ffmpeg (проверенный bundle/bin или PATH)."""
    resolved = _find_ffmpeg()
    # _find_ffmpeg вернёт абсолютный путь только для проверенного бинаря;
    # голое "ffmpeg" — это fallback «ничего не проверилось», тогда пробуем PATH.
    return resolved != "ffmpeg" or _tool_executable("ffmpeg")


def _remove_quietly(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _stop_process(process) -> None:
    """Убить ffmpeg, если он ещё работает, и дождаться выхода."""
    poll = getattr(process, "poll", None)
    try:
        running = callable(poll) and poll() is None
    except Exception:
        running = False
    if not running:
        return
    try:
        process.kill()
    except Exception:
        pass
    try:
        process.wait(timeout=5)
    except Exception:
        pass


@dataclass(frozen=True)
class FfmpegRun:
    """Итог запуска ffmpeg под наблюдением."""

    returncode: int | None
    stderr: str
    # Процесс убит watchdog-ом: нет вывода дольше stall_timeout.
    stalled: bool
    # Последняя отданная в on_ratio доля (-1.0 — ни одной).
    last_ratio: float


def _progress_seconds(line: str) -> float | None:
    if "=" not in line:
        return None
    key, value = line.rstrip("\n").split("=", 1)
    if key not in {"out_time_ms", "out_time_us"}:
        return None
    try:
        return int(value) / 1_000_000
    except ValueError:
        return None


def run_ffmpeg_with_progress(
    command: list[str],
    *,
    duration: float = 0.0,
    on_ratio: Callable[[float | None], None] | None = None,
    stall_timeout: float | None = None,
    on_stall: Callable[[], None] | None = None,
    cancel_check: CancelCheck | None = None,
) -> FfmpegRun:
    """Запустить ffmpeg с ``-progress pipe:1`` и сторожем зависания.

    stderr дренируется отдельным потоком (иначе ffmpeg блокируется на записи в
    переполненный пайп, issue #20), watchdog убивает процесс без активности
    дольше ``stall_timeout``. Процесс останавливается в ``finally``: исключение
    из ``on_ratio`` раньше оставляло ffmpeg работать до 600-секундного
    watchdog. ``on_ratio`` получает монотонную долю 0..1 или один раз ``None``,
    если длительность неизвестна. ``cancel_check`` проверяется на каждой строке
    прогресса (ffmpeg пишет их ~2 раза в секунду): отмена останавливает процесс
    и поднимает ProcessingCancelled. Ошибки запуска (нет бинаря) пробрасываются.
    """
    timeout = _CONVERSION_STALL_TIMEOUT if stall_timeout is None else stall_timeout
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        startupinfo=_windows_startupinfo(),
    )

    stderr_lines: list[str] = []
    # Запись одного float атомарна под GIL, поэтому общий список без блокировки безопасен.
    last_activity = [time.monotonic()]
    watchdog_stop = Event()
    stalled = [False]

    def _bump():
        last_activity[0] = time.monotonic()

    def _drain_stderr(pipe):
        # Поток дренажа не должен молча падать и оставлять ffmpeg
        # заблокированным на записи в переполненный stderr-пайп (#20).
        try:
            for line in iter(pipe.readline, ""):
                stderr_lines.append(line)
                _bump()
        except Exception:
            pass

    def _watchdog():
        poll = max(0.05, min(5.0, timeout / 4))
        while not watchdog_stop.wait(poll):
            if time.monotonic() - last_activity[0] > timeout:
                stalled[0] = True
                if on_stall is not None:
                    try:
                        on_stall()
                    except Exception:
                        pass
                try:
                    process.kill()
                except Exception:
                    pass
                return

    stderr_thread = None
    if process.stderr is not None:
        stderr_thread = Thread(target=_drain_stderr, args=(process.stderr,), daemon=True)
        stderr_thread.start()
    Thread(target=_watchdog, daemon=True).start()

    last_reported = -1.0
    reported_unknown = False
    returncode = None
    try:
        if process.stdout is not None:
            for raw_line in iter(process.stdout.readline, ""):
                _bump()
                raise_if_cancelled(cancel_check)
                seconds = _progress_seconds(raw_line)
                if seconds is None or duration <= 0:
                    if duration <= 0 and on_ratio is not None and not reported_unknown and "=" in raw_line:
                        on_ratio(None)
                        reported_unknown = True
                    continue
                ratio = max(0.0, min(seconds / duration, 1.0))
                if ratio <= last_reported + 1e-9:
                    continue
                last_reported = ratio
                if on_ratio is not None:
                    on_ratio(ratio)

        if duration <= 0 and on_ratio is not None and not reported_unknown:
            on_ratio(None)

        returncode = process.wait()
    finally:
        watchdog_stop.set()
        _stop_process(process)
        if stderr_thread is not None:
            stderr_thread.join(timeout=1.0)

    return FfmpegRun(returncode, "".join(stderr_lines), stalled[0], last_reported)


class AudioConverter:
    """Класс для конвертации медиа файлов в WAV формат"""

    def __init__(self, logger=None):
        """
        Args:
            logger: функция для логирования (опционально)
        """
        self.logger = logger or print
        # Причина последнего отказа convert_to_wav одной строкой: процессор
        # кладёт её в result['error'], иначе web/MCP/API видят только «сбой».
        self.last_error: str | None = None

    def _fail(self, message: str) -> None:
        self.last_error = message
        self.logger(message)

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
                encoding="utf-8",
                errors="replace",
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
                    encoding="utf-8",
                    errors="replace",
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
        cancel_check: CancelCheck | None = None,
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
        self.last_error = None

        # Проверяем существование файла
        if not os.path.exists(input_path):
            self._fail(f"Ошибка: файл не найден: {input_path}")
            return None

        if not os.path.isfile(input_path):
            self._fail(f"Ошибка: это не файл, а папка или другой объект: {input_path}")
            return None

        # Создаём временный файл в папке вывода с уникальным именем,
        # чтобы исключить коллизии при одинаковых basename / параллельных конвертациях.
        temp_filename = f"temp_{uuid.uuid4().hex}_{os.path.basename(input_path)}.wav"
        temp_wav = os.path.join(output_dir, temp_filename)

        self.logger(f"Подготавливаем звук: {os.path.basename(input_path)} → WAV 16 кГц…")
        self.logger(f"Файл: {input_path}")

        duration = media_duration if media_duration is not None and media_duration > 0 else 0.0
        succeeded = False
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
            run = run_ffmpeg_with_progress(
                command,
                duration=duration,
                on_ratio=progress_callback,
                cancel_check=cancel_check,
                on_stall=lambda: self.logger(
                    f"ОШИБКА: FFmpeg не подаёт признаков активности дольше "
                    f"{_CONVERSION_STALL_TIMEOUT}s — принудительно останавливаю (issue #20)."
                ),
            )

            if run.stalled:
                self._fail("Подготовка звука заняла слишком долго и была прервана — файл не обработан.")
                return None

            if run.returncode != 0:
                stderr_text = run.stderr.strip()
                self.logger(f"FFmpeg не смог подготовить звук (код ошибки {run.returncode}). Подробности ниже:")
                self._log_ffmpeg_tail(stderr_text)
                tail = [line.strip() for line in stderr_text.splitlines() if line.strip()]
                self.last_error = (
                    f"FFmpeg не смог подготовить звук (код ошибки {run.returncode})"
                    + (f": {tail[-1]}" if tail else "")
                )
                if "moov atom not found" in stderr_text or "Invalid data found when processing input" in stderr_text:
                    self.logger("")
                    self.logger("Возможная причина: файл повреждён или загружен не до конца (в MP4 метаданные «moov» в конце — если файл обрезан, FFmpeg не может его прочитать).")
                    self.logger("Что попробовать: перезаписать/скачать файл заново, открыть в другом плеере и пересохранить, либо взять другой файл.")
                return None

            self.logger("Звук подготовлен.")
            if progress_callback is not None and duration > 0 and run.last_ratio < 1.0:
                progress_callback(1.0)
            succeeded = True
            return temp_wav

        except FileNotFoundError:
            self._fail("Ошибка: не найдена программа FFmpeg (ни в приложении, ни в системе) — без неё звук подготовить нельзя.")
            return None
        except OSError as exc:
            self._fail(f"Ошибка: не удалось запустить FFmpeg ({exc}).")
            self.logger("Проверьте, что рядом с приложением нет несовместимого ffmpeg для другой ОС/архитектуры.")
            return None
        finally:
            # Недописанный WAV (ошибка ffmpeg, watchdog, исключение из колбэка)
            # не нужен никому: следующий запуск всё равно создаст новый файл.
            if not succeeded:
                _remove_quietly(temp_wav)
