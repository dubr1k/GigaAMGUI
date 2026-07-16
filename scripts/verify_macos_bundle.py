"""Практические проверки для dist/GigaAMTranscriber.app."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_APP_ARCHIVE_EXT = ".app"


def _run(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out


def verify_bundle(bundle_path: str) -> int:
    root = Path(bundle_path)
    if not root.exists():
        print(f"Bundle not found: {root}")
        return 1

    if not root.name.endswith(_APP_ARCHIVE_EXT):
        print(f"Expected .app bundle, got {root.name}")
        return 1

    if sys.platform != "darwin":
        print("Verification script is macOS-specific; skipping runtime checks")
        return 0

    exe = root / "Contents" / "MacOS"
    candidates = [p for p in exe.iterdir() if p.is_file() and os.access(p, os.X_OK)] if exe.exists() else []
    if not candidates:
        print(f"No executable found in {exe}")
        return 1

    # Проверка архитектуры для всех Mach-O бинарников.
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        ok, info = _run(["file", str(path)])
        if ok != 0:
            continue
        if "Mach-O" not in info:
            continue
        if "arm64" not in info:
            print(f"Non-arm64 Mach-O detected: {path}")
            return 1

    out_plist = root / "Contents" / "Info.plist"
    if not out_plist.exists():
        print("Info.plist not found")
        return 1

    if not (root / "Contents" / "Frameworks").exists():
        print("No bundled Frameworks")

    ffmpeg = root.rglob("**/ffmpeg")
    if not any(p for p in ffmpeg):
        print("ffmpeg not found in bundle")
        return 1

    frameworks = root / "Contents" / "Frameworks"
    for package in ("mlx", "gigaam_mlx"):
        if not (frameworks / package).exists():
            print(f"Required MLX package not found in bundle: {package}")
            return 1

    try:
        smoke = subprocess.run(
            [str(candidates[0]), "--asr-runtime-smoke"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        print("Frozen MLX runtime smoke timed out")
        return 1
    smoke_output = (smoke.stdout or "") + (smoke.stderr or "")
    if smoke.returncode != 0 or '"backend": "mlx"' not in smoke_output:
        print(f"Frozen MLX runtime smoke failed ({smoke.returncode}): {smoke_output[-2000:]}")
        return 1

    bundle_sortformer = os.environ.get("GIGAAM_BUNDLE_SORTFORMER", "").strip().lower() in {
        "1", "true", "yes", "on",
    }
    if bundle_sortformer:
        try:
            sortformer_smoke = subprocess.run(
                [str(candidates[0]), "--sortformer-runtime-smoke"],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            print("Frozen Sortformer runtime smoke timed out")
            return 1
        sortformer_output = (sortformer_smoke.stdout or "") + (sortformer_smoke.stderr or "")
        if (
            sortformer_smoke.returncode != 0
            or '"sortformer": "SortformerEncLabelModel"' not in sortformer_output
        ):
            print(
                f"Frozen Sortformer runtime smoke failed ({sortformer_smoke.returncode}): "
                f"{sortformer_output[-4000:]}"
            )
            return 1

    print(f"Bundle verification passed: {root}")
    return 0


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/verify_macos_bundle.py <path_to_app>")
        return 1
    return verify_bundle(sys.argv[1])


if __name__ == "__main__":
    raise SystemExit(main())
