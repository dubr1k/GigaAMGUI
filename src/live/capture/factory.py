"""Select platform capture adapters without importing their optional runtimes."""

from __future__ import annotations

from dataclasses import dataclass

from ..types import CaptureSource
from .base import CaptureAdapter


class CaptureUnavailable(RuntimeError):
    """Capture runtime or OS capability is unavailable with remediation guidance."""


@dataclass(frozen=True)
class CaptureCapabilities:
    platform: str
    sources: frozenset[CaptureSource]
    install_hint: str


def capture_capabilities(platform_name: str) -> CaptureCapabilities:
    if platform_name.startswith("win"):
        return CaptureCapabilities("win32", frozenset(CaptureSource), "Install requirements-live-windows.txt (PyAudioWPatch).")
    if platform_name == "darwin":
        return CaptureCapabilities(
            "darwin",
            frozenset(CaptureSource),
            "Install requirements-live-macos.txt; grant Microphone and Screen Recording permissions.",
        )
    if platform_name.startswith("linux"):
        return CaptureCapabilities(
            "linux",
            frozenset(CaptureSource),
            "Install requirements-live-linux.txt, libportaudio2 and pulseaudio-utils, "
            "and expose a PipeWire/PulseAudio monitor source.",
        )
    return CaptureCapabilities(platform_name, frozenset(), "Live capture is unsupported on this platform.")


def create_capture_adapter(
    platform_name: str,
    source: CaptureSource,
    device_id: str | None = None,
) -> CaptureAdapter:
    if platform_name.startswith("win"):
        from .windows import WindowsMicrophoneAdapter, WindowsSystemAudioAdapter

        return WindowsMicrophoneAdapter(device_id=device_id) if source is CaptureSource.MIC else WindowsSystemAudioAdapter(device_id=device_id)
    if platform_name == "darwin":
        from .macos import MacMicrophoneAdapter, MacSystemAudioAdapter

        return MacMicrophoneAdapter(device_id=device_id) if source is CaptureSource.MIC else MacSystemAudioAdapter(device_id=device_id)
    if platform_name.startswith("linux"):
        from .linux import LinuxMicrophoneAdapter, LinuxSystemAudioAdapter

        return LinuxMicrophoneAdapter(device_id=device_id) if source is CaptureSource.MIC else LinuxSystemAudioAdapter(device_id=device_id)
    raise CaptureUnavailable(f"Unsupported platform '{platform_name}' for live capture.")
