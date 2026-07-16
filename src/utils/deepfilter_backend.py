"""Optional DeepFilterNet backend using the dependency-free official Rust binary.

The official Python package pins NumPy < 2 and conflicts with the application's
NumPy 2 runtime. The signed-by-checksum standalone binary avoids mutating the
PyTorch/NumPy environment and contains the pretrained model.
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import soundfile as sf

from ..config import AUDIO_CHANNELS, AUDIO_SAMPLE_RATE
from .audio_converter import _find_ffmpeg, _windows_startupinfo
from .runtime_manager import base_dir

_DEEPFILTER_VERSION = "0.5.6"
_RELEASE_BASE_URL = (
    "https://github.com/Rikorose/DeepFilterNet/releases/download/"
    f"v{_DEEPFILTER_VERSION}"
)


@dataclass(frozen=True)
class DeepFilterAsset:
    filename: str
    sha256: str

    @property
    def url(self) -> str:
        return f"{_RELEASE_BASE_URL}/{self.filename}"


_ASSETS: dict[tuple[str, str], DeepFilterAsset] = {
    ("linux", "x86_64"): DeepFilterAsset(
        "deep-filter-0.5.6-x86_64-unknown-linux-musl",
        "70775e251eee44c0f2451a1e833326cf8bcbbe304d3e7cd12851e6fce72ef7da",
    ),
    ("linux", "aarch64"): DeepFilterAsset(
        "deep-filter-0.5.6-aarch64-unknown-linux-gnu",
        "14e02a1c0028f3ca0bdf83b62b3336e56ba0556894ef295a95e8573f06557166",
    ),
    ("darwin", "arm64"): DeepFilterAsset(
        "deep-filter-0.5.6-aarch64-apple-darwin",
        "4601e7f4e4c03e59a4c5b5000216ef3add3e808799cfccd95e14e83ea4611081",
    ),
    ("darwin", "x86_64"): DeepFilterAsset(
        "deep-filter-0.5.6-x86_64-apple-darwin",
        "d3be84003acb7c23e738ad7f70a158ec779a8d233a82e7fa3e717d112eb5b50f",
    ),
    ("win32", "amd64"): DeepFilterAsset(
        "deep-filter-0.5.6-x86_64-pc-windows-msvc.exe",
        "75e11fa16445f560cb6b021521ddb89e89270d13b83089705d98776f58fd7915",
    ),
    ("win32", "x86_64"): DeepFilterAsset(
        "deep-filter-0.5.6-x86_64-pc-windows-msvc.exe",
        "75e11fa16445f560cb6b021521ddb89e89270d13b83089705d98776f58fd7915",
    ),
}


def _asset_for(system: str | None = None, machine: str | None = None) -> DeepFilterAsset | None:
    normalized_system = (system or sys.platform).lower()
    normalized_machine = (machine or platform.machine()).lower()
    aliases = {"arm64": "aarch64"} if normalized_system == "linux" else {"aarch64": "arm64"}
    normalized_machine = aliases.get(normalized_machine, normalized_machine)
    return _ASSETS.get((normalized_system, normalized_machine))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class DeepFilterBinaryManager:
    """Downloads a pinned official binary atomically and verifies SHA-256."""

    _download_lock = threading.Lock()
    _max_download_bytes = 256 * 1024 * 1024

    def __init__(
        self,
        *,
        cache_dir: str | Path | None = None,
        asset: DeepFilterAsset | None = None,
    ) -> None:
        configured_cache = os.environ.get("GIGAAM_DEEPFILTER_DIR")
        self.cache_dir = Path(
            cache_dir
            or configured_cache
            or (base_dir() / "audio-enhancement" / _DEEPFILTER_VERSION)
        )
        self.asset = asset or _asset_for()

    def supported(self) -> bool:
        return self.asset is not None

    @staticmethod
    def _trusted_download_url(url: str) -> bool:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        return parsed.scheme == "https" and (
            hostname == "github.com" or hostname.endswith(".githubusercontent.com")
        )

    @classmethod
    def _download(cls, url: str, target: Path) -> None:
        if not cls._trusted_download_url(url):
            raise RuntimeError("DeepFilterNet download URL must use trusted GitHub HTTPS")
        request = urllib.request.Request(url, headers={"User-Agent": "GigaAMGUI-audio-enhancement"})
        # URL and redirect targets are allowlisted above/below; checksum validation
        # in ensure() remains the final fail-closed integrity gate.
        with urllib.request.urlopen(request, timeout=120) as response, target.open("wb") as output:  # nosec B310
            final_url = response.geturl()
            if not cls._trusted_download_url(final_url):
                raise RuntimeError("DeepFilterNet download redirected outside trusted GitHub HTTPS")
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > cls._max_download_bytes:
                raise RuntimeError("DeepFilterNet download exceeds the size limit")
            downloaded = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                downloaded += len(chunk)
                if downloaded > cls._max_download_bytes:
                    raise RuntimeError("DeepFilterNet download exceeds the size limit")
                output.write(chunk)

    def ensure(self) -> str:
        if self.asset is None:
            raise RuntimeError("DeepFilterNet binary is unavailable for this OS/architecture")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        target = self.cache_dir / self.asset.filename
        with self._download_lock:
            if target.is_symlink():
                target.unlink()
            if target.is_file() and _sha256(target) == self.asset.sha256:
                if os.name != "nt":
                    target.chmod(target.stat().st_mode | 0o111)
                return str(target)
            target.unlink(missing_ok=True)
            temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.download")
            try:
                self._download(self.asset.url, temporary)
                actual = _sha256(temporary)
                if actual != self.asset.sha256:
                    raise RuntimeError(
                        f"DeepFilterNet checksum mismatch: expected {self.asset.sha256}, got {actual}"
                    )
                if os.name != "nt":
                    temporary.chmod(0o755)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        return str(target)


class DeepFilterNetBinaryBackend:
    """Runs the official model with bounded attenuation and exact duration."""

    _inference_lock = threading.Lock()

    def __init__(
        self,
        logger=None,
        *,
        manager: DeepFilterBinaryManager | None = None,
        attenuation_limit_db: float = 18.0,
    ) -> None:
        self.logger = logger or (lambda _message: None)
        self.manager = manager or DeepFilterBinaryManager()
        self.attenuation_limit_db = min(max(float(attenuation_limit_db), 0.0), 100.0)

    def is_available(self) -> bool:
        return self.manager.supported()

    @staticmethod
    def _duration(path: str) -> float:
        info = sf.info(path)
        if info.samplerate <= 0 or info.frames <= 0:
            raise ValueError("Audio has no readable samples")
        return float(info.frames) / float(info.samplerate)

    def _run(self, command: list[str], *, timeout: float) -> subprocess.CompletedProcess:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            startupinfo=_windows_startupinfo(),
            timeout=timeout,
        )

    def process(self, input_path: str, output_dir: str) -> str | None:
        os.makedirs(output_dir, exist_ok=True)
        source_duration = self._duration(input_path)
        timeout = max(180.0, min(7200.0, source_duration * 6.0 + 120.0))
        final_path = os.path.join(output_dir, f"temp_deepfiltered_{uuid.uuid4().hex}.wav")
        workspace: Path | None = None

        try:
            workspace = Path(tempfile.mkdtemp(prefix="gigaam_deepfilter_", dir=output_dir))
            input_dir = workspace / "input"
            enhanced_dir = workspace / "enhanced"
            input_dir.mkdir()
            enhanced_dir.mkdir()
            fullband_input = input_dir / "audio.wav"
            fullband_output = enhanced_dir / "audio.wav"

            self.logger("DeepFilterNet: проверка локального backend и модели…")
            binary = self.manager.ensure()
            self.logger("DeepFilterNet: нейросетевое шумоподавление…")
            decode = self._run(
                [
                    _find_ffmpeg(), "-hide_banner", "-nostdin", "-i", input_path,
                    "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", "-vn", "-y",
                    str(fullband_input),
                ],
                timeout=timeout,
            )
            if decode.returncode != 0:
                raise RuntimeError("FFmpeg could not prepare 48 kHz audio for DeepFilterNet")

            with self._inference_lock:
                enhanced = self._run(
                    [
                        binary,
                        "-D",
                        "--atten-lim-db", f"{self.attenuation_limit_db:g}",
                        "--output-dir", str(enhanced_dir),
                        str(fullband_input),
                    ],
                    timeout=timeout,
                )
            if enhanced.returncode != 0 or not fullband_output.is_file():
                raise RuntimeError("DeepFilterNet inference failed")

            # DeepFilterNet compensation can differ by a few milliseconds.
            # Pad/trim the tail to the canonical duration without shifting t=0.
            duration_text = f"{source_duration:.9f}"
            finalize = self._run(
                [
                    _find_ffmpeg(), "-hide_banner", "-nostdin", "-i", str(fullband_output),
                    "-af", f"apad,atrim=duration={duration_text}",
                    "-ar", str(AUDIO_SAMPLE_RATE), "-ac", str(AUDIO_CHANNELS),
                    "-c:a", "pcm_s16le", "-vn", "-y", final_path,
                ],
                timeout=timeout,
            )
            if finalize.returncode != 0 or not os.path.isfile(final_path):
                raise RuntimeError("FFmpeg could not finalize DeepFilterNet output")
            drift = abs(self._duration(final_path) - source_duration)
            if drift > 2.0 / AUDIO_SAMPLE_RATE:
                raise RuntimeError(f"DeepFilterNet timeline drift is unsafe: {drift:.6f}s")
            return final_path
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            self.logger(f"DeepFilterNet unavailable; using safe fallback: {exc}")
            try:
                os.remove(final_path)
            except OSError:
                pass
            return None
        finally:
            if workspace is not None:
                shutil.rmtree(workspace, ignore_errors=True)
