"""Opt-in check that the pinned yt-dlp still downloads from YouTube.

YouTube changes its player clients and media protocol several times a year and
a pinned yt-dlp silently goes stale: yt-dlp 2026.3.17 still extracted the
video page, but its default `android_vr` client got HTTP 403 for the media
itself, so every front-end failed with "unable to download video data". A
fake YoutubeDL cannot catch that, and CI runner IPs are routinely blocked by
YouTube, so this test needs the network and runs only on request — before a
release, or after bumping yt-dlp:

    GIGAAM_ONLINE_TESTS=1 .venv/bin/python -m pytest tests/test_media_downloader_online.py
"""

import os
from pathlib import Path

import pytest

from src.utils.media_downloader import MediaDownloader

pytestmark = pytest.mark.skipif(
    os.environ.get("GIGAAM_ONLINE_TESTS") != "1",
    reason="needs the network; set GIGAAM_ONLINE_TESTS=1",
)

# A regular, long-lived public video (not the 19-second "Me at the zoo", which
# kept working on the stale yt-dlp and hid the 403).
YOUTUBE_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_pinned_yt_dlp_downloads_youtube_audio(tmp_path):
    result = MediaDownloader().download(YOUTUBE_URL, str(tmp_path))

    assert len(result.files) == 1
    assert Path(result.files[0]).stat().st_size > 1024 * 1024
