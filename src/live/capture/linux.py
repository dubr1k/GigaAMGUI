"""Linux PipeWire/PulseAudio monitor-source adapter entry points."""

from __future__ import annotations

import ctypes.util
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from ..types import CaptureSource
from .common import NativeCaptureApi, QueuedCaptureAdapter, SoundDeviceCapture
from .factory import CaptureUnavailable

#: Куда packaging/_spec_common.py кладёт вшитый PortAudio — держать в паре с
#: BUNDLED_PORTAUDIO_RELPATH.
_BUNDLED_PORTAUDIO_RELPATH = "_portaudio/libportaudio.so.2"


def _bundled_portaudio() -> str | None:
    """Путь к вшитому libportaudio.so.2, если приложение запущено из бандла."""
    root = getattr(sys, "_MEIPASS", None)
    if not root:
        return None
    library = Path(root) / _BUNDLED_PORTAUDIO_RELPATH
    return str(library) if library.is_file() else None


@contextmanager
def bundled_portaudio_resolution() -> Iterator[None]:
    """На время блока учит ``find_library`` находить вшитый PortAudio.

    Linux-колесо sounddevice не содержит PortAudio: загрузчик ищет библиотеку
    через ``ctypes.util.find_library``, который смотрит в кэш ldconfig, а не в
    ``sys._MEIPASS``. Без подмены замороженная сборка требовала бы системный
    libportaudio2 даже при вшитой копии (issue #47). Вне бандла — no-op.
    """
    bundled = _bundled_portaudio()
    if bundled is None:
        yield
        return

    original = ctypes.util.find_library
    ctypes.util.find_library = lambda name: bundled if name == "portaudio" else original(name)
    try:
        yield
    finally:
        ctypes.util.find_library = original


def _load_linux_api() -> NativeCaptureApi:
    try:
        with bundled_portaudio_resolution():
            import sounddevice
    except (ImportError, OSError) as exc:
        raise CaptureUnavailable(
            "Linux live capture requires sounddevice and the PortAudio library "
            "(install requirements-live-linux.txt and the system package libportaudio2); "
            "for system audio configure a PipeWire/PulseAudio monitor source."
        ) from exc
    return SoundDeviceCapture(sounddevice)


class _LinuxAdapter(QueuedCaptureAdapter):
    def __init__(
        self,
        source: CaptureSource,
        device_id: str | None = None,
        *,
        api: NativeCaptureApi | None = None,
        api_loader: Callable[[], NativeCaptureApi] = _load_linux_api,
    ) -> None:
        super().__init__(source, api, device_id, api_loader=api_loader)


class LinuxMicrophoneAdapter(_LinuxAdapter):
    def __init__(self, device_id: str | None = None, **kwargs: object) -> None:
        super().__init__(CaptureSource.MIC, device_id, **kwargs)


class LinuxSystemAudioAdapter(_LinuxAdapter):
    def __init__(self, device_id: str | None = None, **kwargs: object) -> None:
        super().__init__(CaptureSource.SYSTEM, device_id, **kwargs)
