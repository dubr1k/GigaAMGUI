"""Frozen headless companion for GigaAMLiquid."""

import json
import multiprocessing
import sys

# PyInstaller helpers (including torch's resource_tracker) must exit before
# dispatching worker arguments or importing the model pipeline.
multiprocessing.freeze_support()


def main() -> int:
    if sys.argv[1:] == ["--native-worker"]:
        from src.tui_worker import main as worker_main

        return worker_main()
    if len(sys.argv) == 4 and sys.argv[1] == "--media-download-smoke":
        from src.utils.media_downloader import MediaDownloader

        result = MediaDownloader().download(sys.argv[2], sys.argv[3])
        print(json.dumps({"files": result.files}, ensure_ascii=False))
        return 0
    raise SystemExit("Usage: GigaAMWorker --native-worker | --media-download-smoke URL TARGET_DIR")


if __name__ == "__main__":
    raise SystemExit(main())
