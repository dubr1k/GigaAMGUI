"""Практические проверки для dist/GigaAMTranscriber.app.

Два профиля сборки, две разные проверки. ``arm64-mlx`` — обычный Apple Silicon
бандл с torch и MLX. ``x86_64-onnx`` — Intel-бандл из
``packaging/gigaam_app_mac_x86_64.spec``, где ни torch, ни mlx быть не должно:
под macOS x86_64 колёс torch>=2.6 не существует, поэтому цепочка целиком ONNX
(issue #45). Профиль определяется автоматически по архитектуре машины, но его
можно задать явно через ``--profile``.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

_APP_ARCHIVE_EXT = ".app"


@dataclass(frozen=True)
class BundleProfile:
    """Чего мы ждём от собранного .app под конкретную архитектуру."""

    name: str
    arch: str
    #: Пакеты, без которых бандл бесполезен (ищем в Contents/Frameworks).
    required_packages: tuple[str, ...]
    #: Пакеты, присутствие которых означает сломанную сборку.
    forbidden_packages: tuple[str, ...]
    #: Смок нативного рантайма (флаг app.py) и обязательный маркер в его выводе.
    runtime_smoke: tuple[str, str]
    extra_smokes: tuple[tuple[str, str | None], ...] = field(default=())


PROFILES = {
    "arm64-mlx": BundleProfile(
        name="arm64-mlx",
        arch="arm64",
        required_packages=("mlx", "gigaam_mlx"),
        forbidden_packages=(),
        runtime_smoke=("--asr-runtime-smoke", '"backend": "mlx"'),
    ),
    "x86_64-onnx": BundleProfile(
        name="x86_64-onnx",
        arch="x86_64",
        required_packages=("onnxruntime", "onnx_asr"),
        # torch/mlx внутри Intel-бандла — это либо бинарники не той архитектуры,
        # либо лишний гигабайт веса; и то и другое должно валить сборку.
        forbidden_packages=("torch", "mlx", "gigaam_mlx", "pyannote"),
        runtime_smoke=("--onnx-runtime-smoke", '"backend": "onnx"'),
        # Смока диаризации здесь сознательно нет: он тянет модель из сети, а тот
        # же путь уже закрыт `--offline-models-smoke` на привезённых моделях —
        # и в CI, и локально, без стомегабайтной докачки посреди сборки.
    ),
}


def default_profile() -> str:
    return "arm64-mlx" if platform.machine() == "arm64" else "x86_64-onnx"


def _run(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out


def _run_smoke(executable: Path, flag: str, marker: str | None, timeout: int = 60) -> int:
    try:
        smoke = subprocess.run(
            [str(executable), flag],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"Frozen smoke timed out: {flag}")
        return 1
    output = (smoke.stdout or "") + (smoke.stderr or "")
    if smoke.returncode != 0 or (marker is not None and marker not in output):
        print(f"Frozen smoke failed {flag} ({smoke.returncode}): {output[-4000:]}")
        return 1
    return 0


def verify_bundle(bundle_path: str, profile_name: str | None = None) -> int:
    profile = PROFILES[profile_name or default_profile()]

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
        if profile.arch not in info:
            print(f"Non-{profile.arch} Mach-O detected: {path}")
            return 1

    out_plist = root / "Contents" / "Info.plist"
    if not out_plist.exists():
        print("Info.plist not found")
        return 1

    frameworks = root / "Contents" / "Frameworks"
    if not frameworks.exists():
        print("No bundled Frameworks")

    ffmpeg = root.rglob("**/ffmpeg")
    if not any(p for p in ffmpeg):
        print("ffmpeg not found in bundle")
        return 1

    for package in profile.required_packages:
        if not (frameworks / package).exists():
            print(f"Required package not found in bundle ({profile.name}): {package}")
            return 1

    for package in profile.forbidden_packages:
        if (frameworks / package).exists():
            print(
                f"Package must not be bundled in the {profile.name} build: {package}"
            )
            return 1

    smoke_flag, smoke_marker = profile.runtime_smoke
    if _run_smoke(candidates[0], smoke_flag, smoke_marker, timeout=120):
        return 1

    # Без этого гейта полная .app уезжала в релиз без sounddevice/ScreenCaptureKit,
    # а вкладка Live падала уже у пользователя (issue #47).
    if _run_smoke(candidates[0], "--live-capture-smoke", None):
        return 1

    for flag, marker in profile.extra_smokes:
        if _run_smoke(candidates[0], flag, marker):
            return 1

    bundle_sortformer = os.environ.get("GIGAAM_BUNDLE_SORTFORMER", "").strip().lower() in {
        "1", "true", "yes", "on",
    }
    if bundle_sortformer:
        if _run_smoke(
            candidates[0],
            "--sortformer-runtime-smoke",
            '"sortformer": "SortformerEncLabelModel"',
        ):
            return 1

    print(f"Bundle verification passed ({profile.name}): {root}")
    return 0


def main() -> int:
    args = [arg for arg in sys.argv[1:]]
    profile_name = None
    if "--profile" in args:
        index = args.index("--profile")
        if index + 1 >= len(args):
            print("--profile requires a value: " + ", ".join(sorted(PROFILES)))
            return 1
        profile_name = args[index + 1]
        if profile_name not in PROFILES:
            print(f"Unknown profile {profile_name}; expected one of {', '.join(sorted(PROFILES))}")
            return 1
        del args[index:index + 2]
    if len(args) != 1:
        print("Usage: python scripts/verify_macos_bundle.py [--profile NAME] <path_to_app>")
        return 1
    return verify_bundle(args[0], profile_name)


if __name__ == "__main__":
    raise SystemExit(main())
