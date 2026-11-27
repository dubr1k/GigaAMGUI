"""Install the separate Community-1 environment without modifying the app's Python.

Run with Python 3.10–3.13: python3 install_community_runtime.py <directory>
A packaged macOS app includes this script and its requirements next to the worker.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import venv
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="directory for the separate Community-1 venv")
    args = parser.parse_args()

    if not (3, 10) <= sys.version_info[:2] <= (3, 13):
        parser.error("Community-1 requires Python 3.10–3.13; Python 3.11 is recommended")

    target = args.directory.expanduser().resolve()
    if target == Path(sys.prefix).resolve() or target == Path(sys.base_prefix).resolve():
        parser.error("the Community-1 environment must be separate from the current Python")
    if target.exists() and (not target.is_dir() or (any(target.iterdir()) and not (target / "pyvenv.cfg").is_file())):
        parser.error(f"{target} is not an empty directory or an existing virtual environment")

    requirements = Path(__file__).with_name("requirements-community.txt")
    if not requirements.is_file():
        requirements = Path(__file__).resolve().parents[3] / "requirements-community.txt"
    if not requirements.is_file():
        parser.error("requirements-community.txt was not included in this installation")

    venv.EnvBuilder(with_pip=True).create(target)
    python = target / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    try:
        subprocess.run([str(python), "-m", "pip", "install", "-r", str(requirements)], check=True)
        probe = (
            "import importlib.metadata as m; "
            "assert m.version('pyannote.audio') == '4.0.7'; "
            "import soundfile, torch; from pyannote.audio import Pipeline"
        )
        subprocess.run([str(python), "-I", "-c", probe], check=True)
    except subprocess.CalledProcessError as exc:
        print(f"Community-1 installation or import failed (exit {exc.returncode}); rerun to retry.", file=sys.stderr)
        return exc.returncode or 1
    print(f"Community-1 runtime installed at {python}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
