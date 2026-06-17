import os

import pytest

from src.utils.media_downloader import MediaDownloader


class FakeYoutubeDL:
    instances = []
    exit_code = 0

    def __init__(self, opts):
        self.opts = opts
        self.urls = None
        self.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def download(self, urls):
        self.urls = urls
        hook = self.opts["progress_hooks"][0]
        output_file = os.path.join(
            os.path.dirname(self.opts["outtmpl"]),
            "downloaded.webm",
        )
        hook({"status": "downloading", "_percent_str": "42.8%"})
        hook({"status": "finished", "filename": output_file})
        hook({"status": "finished", "filename": output_file})
        return self.exit_code


class FakeYoutubeDLWithoutFinishedFile(FakeYoutubeDL):
    def download(self, urls):
        self.urls = urls
        output_file = os.path.join(
            os.path.dirname(self.opts["outtmpl"]),
            "fallback.m4a",
        )
        with open(output_file, "wb") as media_file:
            media_file.write(b"audio")
        return self.exit_code


@pytest.fixture(autouse=True)
def reset_fake_youtube_dl():
    FakeYoutubeDL.instances = []
    FakeYoutubeDL.exit_code = 0


def test_download_collects_files_and_progress(tmp_path):
    progress = []
    downloader = MediaDownloader(youtube_dl_cls=FakeYoutubeDL)

    result = downloader.download(
        "https://example.test/video",
        str(tmp_path),
        progress_callback=progress.append,
    )

    instance = FakeYoutubeDL.instances[0]
    assert instance.urls == ["https://example.test/video"]
    assert instance.opts["noplaylist"] is True
    assert instance.opts["ignoreerrors"] is False
    assert instance.opts["windowsfilenames"] is True
    assert result.files == [str(tmp_path / "downloaded.webm")]
    assert progress == [42, 100, 100]


def test_download_can_allow_playlists(tmp_path):
    downloader = MediaDownloader(youtube_dl_cls=FakeYoutubeDL)

    downloader.download(
        "https://example.test/playlist",
        str(tmp_path),
        allow_playlist=True,
    )

    assert FakeYoutubeDL.instances[0].opts["noplaylist"] is False


def test_download_falls_back_to_target_dir_scan(tmp_path):
    downloader = MediaDownloader(youtube_dl_cls=FakeYoutubeDLWithoutFinishedFile)

    result = downloader.download("https://example.test/video", str(tmp_path))

    assert result.files == [str(tmp_path / "fallback.m4a")]


def test_download_rejects_empty_url(tmp_path):
    downloader = MediaDownloader(youtube_dl_cls=FakeYoutubeDL)

    with pytest.raises(ValueError, match="URL"):
        downloader.download("  ", str(tmp_path))


@pytest.mark.parametrize("bad_url", [
    "file:///etc/passwd",
    "ftp://example.test/a.mp3",
    "/local/path/file.mp3",
])
def test_download_rejects_non_http_scheme(tmp_path, bad_url):
    downloader = MediaDownloader(youtube_dl_cls=FakeYoutubeDL)
    with pytest.raises(ValueError, match="схем"):
        downloader.download(bad_url, str(tmp_path))


def test_fallback_excludes_preexisting_files(tmp_path):
    # Посторонний файл, уже лежавший в папке до загрузки, не должен попасть в результат
    (tmp_path / "user_other.mp3").write_bytes(b"preexisting")
    downloader = MediaDownloader(youtube_dl_cls=FakeYoutubeDLWithoutFinishedFile)

    result = downloader.download("https://example.test/video", str(tmp_path))

    assert result.files == [str(tmp_path / "fallback.m4a")]
    assert str(tmp_path / "user_other.mp3") not in result.files


def test_download_raises_on_nonzero_exit_code(tmp_path):
    FakeYoutubeDL.exit_code = 1
    downloader = MediaDownloader(youtube_dl_cls=FakeYoutubeDL)

    with pytest.raises(RuntimeError, match="yt-dlp"):
        downloader.download("https://example.test/video", str(tmp_path))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12.4%", 12),
        (" 101% ", 100),
        ("-5%", 0),
        ("bad", 0),
        (None, 0),
    ],
)
def test_parse_percent(raw, expected):
    assert MediaDownloader._parse_percent(raw) == expected
