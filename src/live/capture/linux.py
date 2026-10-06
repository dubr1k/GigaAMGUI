"""Linux PipeWire/PulseAudio monitor-source adapter entry points."""

from __future__ import annotations

import ctypes.util
import os
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..types import CaptureSource
from . import pulse
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


#: Агрегаты ALSA, через которые PortAudio попадает в звуковой сервер. Первый
#: существующий и используется, когда выбран монитор из перечисления pactl.
_PULSE_ALSA_DEVICES = ("pulse", "default")


class LinuxSoundDeviceCapture(SoundDeviceCapture):
    """SoundDeviceCapture, которому мониторы называет сам звуковой сервер.

    Мониторов нет в перечислении PortAudio (issue #49), поэтому для SYSTEM к
    списку добавляются источники из ``pactl``, а поток для них открывается на
    ALSA-агрегате ``pulse`` и сразу перецепляется на выбранный монитор.
    """

    _known_source_outputs: frozenset[str] = frozenset()

    def devices(self, source: CaptureSource) -> list[dict[str, Any]]:
        devices = super().devices(source)
        if source is not CaptureSource.SYSTEM:
            return devices
        known = {device["name"].casefold() for device in devices}
        extra = [device for device in pulse.monitor_devices() if device["name"].casefold() not in known]
        if any(device["is_default"] for device in devices):
            extra = [{**device, "is_default": False} for device in extra]
        return devices + extra

    def no_system_source_message(self) -> str:
        return (
            "No PipeWire/PulseAudio monitor source is available. Install pulseaudio-utils (pactl) "
            "so monitor sources can be enumerated, or expose a monitor as a named ALSA PCM."
        )

    def _stream_device(self, selected: dict[str, Any]) -> Any:
        device_id = str(selected["id"])
        if not device_id.startswith(pulse.PULSE_DEVICE_PREFIX):
            return super()._stream_device(selected)
        names = {str(info["name"]).casefold(): index for index, info in enumerate(self._sounddevice.query_devices())}
        for candidate in _PULSE_ALSA_DEVICES:
            if candidate in names:
                return names[candidate]
        raise CaptureUnavailable(
            "PortAudio exposes no 'pulse' or 'default' device, so a PipeWire/PulseAudio monitor "
            "cannot be opened. Install libasound2-plugins (the ALSA pulse plugin)."
        )

    def _before_start(self, selected: dict[str, Any]) -> None:
        # Микрофон этого же процесса тоже идёт через pulse, поэтому свою запись
        # опознаём как ту, которой не было до открытия потока, а не как последнюю.
        self._known_source_outputs = (
            pulse.source_outputs(os.getpid()) if str(selected["id"]).startswith(pulse.PULSE_DEVICE_PREFIX) else set()
        )

    def _after_start(self, selected: dict[str, Any]) -> None:
        device_id = str(selected["id"])
        if not device_id.startswith(pulse.PULSE_DEVICE_PREFIX):
            return
        name = pulse.source_name(device_id)
        if not pulse.attach_to_monitor(name, os.getpid(), known=self._known_source_outputs):
            raise CaptureUnavailable(
                f"Could not move the capture stream to monitor source '{name}'; "
                "the stream would have recorded the default input instead."
            )


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
    return LinuxSoundDeviceCapture(sounddevice)


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
