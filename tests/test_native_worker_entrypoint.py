"""Liquid's frozen worker entry point must keep the media import contract."""

import json
import os
import subprocess
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def test_native_worker_downloads_media_without_the_gui(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "sample.wav").write_bytes(b"RIFF" + b"\0" * 64)

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(source)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        destination = tmp_path / "download"
        binary = os.environ.get("GIGAAM_WORKER_BINARY")
        command = [binary] if binary else [sys.executable, "native_worker.py"]
        result = subprocess.run(
            [*command, "--media-download-smoke",
             f"http://127.0.0.1:{server.server_port}/sample.wav", str(destination)],
            capture_output=True, text=True, timeout=30,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result.returncode == 0, result.stderr
    files = json.loads(result.stdout.splitlines()[-1])["files"]
    assert files == [str(destination / "sample.wav")]
    assert Path(files[0]).read_bytes() == (source / "sample.wav").read_bytes()
