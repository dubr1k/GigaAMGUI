"""
Загрузка медиафайлов по URL через yt-dlp.
"""

import os
import re
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp

# Разрешённые схемы URL (защита от SSRF/локальных путей через file://, и т.п.)
_ALLOWED_SCHEMES = {"http", "https"}


ProgressCallback = Callable[[int], None]


@dataclass
class DownloadResult:
    """Результат загрузки медиафайлов."""

    files: list[str] = field(default_factory=list)


class MediaDownloader:
    """Обертка над yt-dlp с простым контрактом для GUI и тестов."""

    def __init__(self, youtube_dl_cls=None):
        self.youtube_dl_cls = youtube_dl_cls or yt_dlp.YoutubeDL

    def download(
        self,
        url: str,
        target_dir: str,
        progress_callback: ProgressCallback | None = None,
        allow_playlist: bool = False,
        windows_filenames: bool = True,
    ) -> DownloadResult:
        url = url.strip()
        if not url:
            raise ValueError("URL для загрузки не указан")

        scheme = urlparse(url).scheme.lower()
        if scheme not in _ALLOWED_SCHEMES:
            raise ValueError(
                f"Недопустимая схема URL: '{scheme or '(пусто)'}'. "
                "Разрешены только http:// и https://"
            )

        target_path = Path(target_dir).expanduser()
        target_path.mkdir(parents=True, exist_ok=True)

        # Запоминаем файлы, которые уже были в папке ДО загрузки,
        # чтобы fallback не вернул посторонние пользовательские файлы.
        files_before = {
            os.path.abspath(str(p))
            for p in target_path.iterdir()
            if p.is_file()
        }

        downloaded_files: list[str] = []

        def progress_hook(data):
            status = data.get("status")
            if status == "downloading":
                # Считаем процент из байтов — надёжнее, чем _percent_str
                # (в новых версиях yt-dlp он обёрнут ANSI-кодами и не парсится).
                total = data.get("total_bytes") or data.get("total_bytes_estimate")
                downloaded = data.get("downloaded_bytes")
                if total and downloaded is not None:
                    percent = max(0, min(100, int(downloaded * 100 / total)))
                else:
                    percent = self._parse_percent(data.get("_percent_str"))
                if progress_callback:
                    progress_callback(percent)
            elif status == "finished":
                filepath = data.get("filename")
                if filepath:
                    normalized = os.path.abspath(filepath)
                    if normalized not in downloaded_files:
                        downloaded_files.append(normalized)
                if progress_callback:
                    progress_callback(100)

        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": str(target_path / "%(title)s.%(ext)s"),
            "progress_hooks": [progress_hook],
            "ignoreerrors": False,
            "noplaylist": not allow_playlist,
            "quiet": True,
            "no_warnings": True,
            "windowsfilenames": windows_filenames,
        }

        exit_code = 1
        for attempt in range(2):
            try:
                with self.youtube_dl_cls(ydl_opts) as ydl:
                    exit_code = ydl.download([url])
                break
            except zlib.error:
                if attempt == 1:
                    raise

        if exit_code:
            raise RuntimeError(f"yt-dlp завершился с кодом {exit_code}")

        if not downloaded_files:
            # Хук не сообщил имя файла — берём только НОВЫЕ файлы (появившиеся после старта),
            # исключая то, что уже лежало в папке, и временные файлы yt-dlp.
            downloaded_files = [
                os.path.abspath(str(path))
                for path in target_path.iterdir()
                if path.is_file()
                and not path.name.endswith((".part", ".ytdl"))
                and os.path.abspath(str(path)) not in files_before
            ]

        return DownloadResult(files=downloaded_files)

    @staticmethod
    def _parse_percent(value) -> int:
        if value is None:
            return 0
        # Снимаем ANSI-escape коды, которыми yt-dlp раскрашивает _percent_str
        text = re.sub(r"\x1b\[[0-9;]*m", "", str(value)).replace("%", "").strip()
        try:
            return max(0, min(100, int(float(text))))
        except (TypeError, ValueError):
            return 0
