"""
Загрузка медиафайлов по URL через yt-dlp.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import yt_dlp


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
        progress_callback: Optional[ProgressCallback] = None,
        allow_playlist: bool = False,
        windows_filenames: bool = True,
    ) -> DownloadResult:
        url = url.strip()
        if not url:
            raise ValueError("URL для загрузки не указан")

        target_path = Path(target_dir).expanduser()
        target_path.mkdir(parents=True, exist_ok=True)

        downloaded_files: list[str] = []

        def progress_hook(data):
            status = data.get("status")
            if status == "downloading":
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

        with self.youtube_dl_cls(ydl_opts) as ydl:
            exit_code = ydl.download([url])

        if exit_code:
            raise RuntimeError(f"yt-dlp завершился с кодом {exit_code}")

        if not downloaded_files:
            downloaded_files = [
                os.path.abspath(str(path))
                for path in target_path.iterdir()
                if path.is_file() and not path.name.endswith((".part", ".ytdl"))
            ]

        return DownloadResult(files=downloaded_files)

    @staticmethod
    def _parse_percent(value) -> int:
        if value is None:
            return 0
        try:
            return max(0, min(100, int(float(str(value).replace("%", "").strip()))))
        except (TypeError, ValueError):
            return 0
