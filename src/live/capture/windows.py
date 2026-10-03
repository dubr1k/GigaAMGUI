"""Windows WASAPI capture adapters backed by optional PyAudioWPatch."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np

from ..types import CaptureSource
from .common import NativeCaptureApi, QueuedCaptureAdapter, select_device
from .factory import CaptureUnavailable


def _load_windows_api() -> NativeCaptureApi:
    try:
        import pyaudiowpatch as pyaudio
    except ImportError as exc:
        raise CaptureUnavailable("Windows live capture requires PyAudioWPatch. Install requirements-live-windows.txt.") from exc
    return _PyAudioWASAPI(pyaudio)


class _PyAudioWASAPI:
    def __init__(self, pyaudio: Any) -> None:
        self._pyaudio = pyaudio
        self._audio = pyaudio.PyAudio()
        self._stream: Any = None

    def devices(self, source: CaptureSource) -> list[dict[str, Any]]:
        default_index = self._default_device_index(source)
        devices = []
        for index in range(self._audio.get_device_count()):
            info = self._audio.get_device_info_by_index(index)
            loopback = bool(info.get("isLoopbackDevice", False))
            if (source is CaptureSource.SYSTEM) != loopback or info.get("maxInputChannels", 0) <= 0:
                continue
            devices.append(
                {
                    "id": str(index),
                    "name": info["name"],
                    "sample_rate": int(info["defaultSampleRate"]),
                    "channels": int(info["maxInputChannels"]),
                    "is_default": index == default_index,
                }
            )
        return devices

    def _default_device_index(self, source: CaptureSource) -> int | None:
        """Loopback endpoints are never the default *input* device.

        Picking the first enumerated loopback instead gave us silent, inactive
        outputs (S/PDIF, unplugged HDMI); the right default is the loopback
        that belongs to the current default playback endpoint.
        """
        try:
            if source is not CaptureSource.SYSTEM:
                return self._audio.get_default_input_device_info()["index"]
            wasapi = self._audio.get_host_api_info_by_type(self._pyaudio.paWASAPI)
            speakers = self._audio.get_device_info_by_index(int(wasapi["defaultOutputDevice"]))
        except Exception:
            return None
        if speakers.get("isLoopbackDevice", False):
            return int(speakers["index"])
        name = str(speakers.get("name", ""))
        for index in range(self._audio.get_device_count()):
            info = self._audio.get_device_info_by_index(index)
            if bool(info.get("isLoopbackDevice", False)) and name and name in str(info.get("name", "")):
                return index
        return None

    def start(self, source: CaptureSource, device_id: str | None, callback: Callable[..., None]) -> None:
        selected = select_device(self.devices(source), device_id)
        if selected is None:
            if source is CaptureSource.SYSTEM:
                raise OSError(
                    "No WASAPI loopback device is available. Enable a playback device and restart capture."
                )
            raise OSError("No WASAPI microphone device is available")

        def on_audio(data: bytes, frame_count: int, _time_info: Any, _status: Any) -> tuple[None, int]:
            frames = np.frombuffer(data, dtype=np.float32).reshape(frame_count, selected["channels"])
            callback(frames, None, selected["sample_rate"])
            return None, self._pyaudio.paContinue

        self._stream = self._audio.open(
            format=self._pyaudio.paFloat32,
            channels=selected["channels"],
            rate=selected["sample_rate"],
            input=True,
            input_device_index=int(selected["id"]),
            stream_callback=on_audio,
        )
        self._stream.start_stream()

    def pause(self) -> None:
        if self._stream is not None:
            self._stream.stop_stream()

    def resume(self) -> None:
        if self._stream is not None:
            self._stream.start_stream()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop_stream()
            self._stream.close()
            self._stream = None
        self.close()

    def close(self) -> None:
        if self._audio is None:
            return
        self._audio.terminate()
        self._audio = None


class _WindowsAdapter(QueuedCaptureAdapter):
    def __init__(
        self,
        source: CaptureSource,
        device_id: str | None = None,
        *,
        api: NativeCaptureApi | None = None,
        api_loader: Callable[[], NativeCaptureApi] = _load_windows_api,
    ) -> None:
        super().__init__(source, api, device_id, api_loader=api_loader)

    def _native_api(self) -> NativeCaptureApi:
        try:
            return super()._native_api()
        except ImportError as exc:
            raise CaptureUnavailable(
                "Windows live capture requires PyAudioWPatch. Install requirements-live-windows.txt."
            ) from exc


class WindowsMicrophoneAdapter(_WindowsAdapter):
    def __init__(self, device_id: str | None = None, **kwargs: Any) -> None:
        super().__init__(CaptureSource.MIC, device_id, **kwargs)


class WindowsSystemAudioAdapter(_WindowsAdapter):
    def __init__(self, device_id: str | None = None, **kwargs: Any) -> None:
        super().__init__(CaptureSource.SYSTEM, device_id, **kwargs)
