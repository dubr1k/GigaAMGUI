"""Worker-isolated plumbing shared by platform-specific capture adapters."""

from __future__ import annotations

from collections.abc import Callable
from queue import Empty, SimpleQueue
from threading import Event, Lock, Thread
from time import time_ns
from typing import Any, Protocol

import numpy as np

from ..types import CaptureDevice, CaptureEvent, CaptureEventKind, CaptureSource, PcmChunk
from .factory import CaptureUnavailable
from .queue import BoundedChunkQueue


class NativeCaptureApi(Protocol):
    def devices(self, source: CaptureSource) -> list[dict[str, Any]]: ...

    def start(self, source: CaptureSource, device_id: str | None, callback: Callable[..., None]) -> None: ...

    def pause(self) -> None: ...

    def resume(self) -> None: ...

    def stop(self) -> None: ...

    def set_error_handler(self, handler: Callable[[Exception], None]) -> None: ...


MAX_CAPTURE_CHANNELS = 2
"""Pulse/PipeWire aggregates advertise 32 input channels; FLAC tops out at 8 and
recognition needs mono, so opening a stream that wide only breaks the session
recording and floods the capture queue."""


class SoundDeviceCapture:
    """Optional sounddevice bridge for microphones and Pulse/PipeWire monitors."""

    def __init__(self, sounddevice: Any) -> None:
        self._sounddevice = sounddevice
        self._stream: Any = None

    def devices(self, source: CaptureSource) -> list[dict[str, Any]]:
        default_input = getattr(getattr(self._sounddevice, "default", None), "device", [None])[0]
        devices = []
        for index, info in enumerate(self._sounddevice.query_devices()):
            name = str(info["name"])
            is_monitor = "monitor" in name.casefold()
            if int(info.get("max_input_channels", 0)) <= 0 or is_monitor != (source is CaptureSource.SYSTEM):
                continue
            devices.append(
                {
                    "id": str(index),
                    "name": name,
                    "sample_rate": int(info.get("default_samplerate") or 48_000),
                    "channels": int(info["max_input_channels"]),
                    "is_default": index == default_input,
                }
            )
        return devices

    def start(self, source: CaptureSource, device_id: str | None, callback: Callable[..., None]) -> None:
        selected = self._select(source, device_id)
        self._before_start(selected)

        def on_audio(indata: Any, _frames: int, _time_info: Any, _status: Any) -> None:
            callback(indata, None, selected["sample_rate"])

        channels = max(1, min(int(selected["channels"]), MAX_CAPTURE_CHANNELS))
        self._stream = self._sounddevice.InputStream(
            device=self._stream_device(selected),
            samplerate=selected["sample_rate"],
            channels=channels,
            dtype="float32",
            callback=on_audio,
        )
        self._stream.start()
        try:
            self._after_start(selected)
        except Exception:
            # Половина захвата хуже отсутствующего: поток, который не удалось
            # довести до нужного источника, пишет что-то другое.
            self.stop()
            raise

    def _select(self, source: CaptureSource, device_id: str | None) -> dict[str, Any]:
        devices = self.devices(source)
        selected = next((item for item in devices if item["id"] == device_id), None) if device_id else next(
            (item for item in devices if item["is_default"]), devices[0] if devices else None
        )
        if selected is None:
            if source is CaptureSource.SYSTEM:
                raise CaptureUnavailable(self.no_system_source_message())
            raise OSError("No microphone input device is available")
        return selected

    def no_system_source_message(self) -> str:
        return "No PipeWire/PulseAudio monitor source is available. Enable a Pulse monitor source and select it."

    def _stream_device(self, selected: dict[str, Any]) -> Any:
        """Устройство PortAudio, на котором открывается поток."""
        return int(selected["id"])

    def _before_start(self, selected: dict[str, Any]) -> None:
        """Подготовка до открытия потока — точка расширения для платформ."""
        return None

    def _after_start(self, selected: dict[str, Any]) -> None:
        """Доводка уже открытого потока — точка расширения для платформ."""
        return None

    def pause(self) -> None:
        if self._stream is not None:
            self._stream.stop()

    def resume(self) -> None:
        if self._stream is not None:
            self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None


class QueuedCaptureAdapter:
    """Keep native callback work bounded to frame copying and queue insertion."""

    def __init__(
        self,
        source: CaptureSource,
        api: NativeCaptureApi | None,
        device_id: str | None = None,
        *,
        max_queue_bytes: int = 480_000 * 2 * 4,
        api_loader: Callable[[], NativeCaptureApi] | None = None,
    ) -> None:
        self.source = source
        self._api = api
        self._api_loader = api_loader
        self._device_id = device_id
        self._queue = BoundedChunkQueue(max_queue_bytes)
        self._events: SimpleQueue[CaptureEvent] = SimpleQueue()
        self._stopped = Event()
        self._paused = Event()
        self._offset = 0
        self._offset_lock = Lock()
        self._worker: Thread | None = None
        self._on_chunk: Callable[[PcmChunk], None] | None = None
        self._on_event: Callable[[CaptureEvent], None] | None = None
        self._reported_failures: set[str] = set()
        self.dispatch_failures = 0

    def devices(self) -> list[CaptureDevice]:
        return [
            CaptureDevice(
                id=str(device["id"]),
                name=str(device["name"]),
                source=self.source,
                sample_rate=int(device["sample_rate"]),
                channels=int(device["channels"]),
                is_default=bool(device.get("is_default", False)),
            )
            for device in self._native_api().devices(self.source)
        ]

    def start(
        self,
        on_chunk: Callable[[PcmChunk], None],
        on_event: Callable[[CaptureEvent], None],
    ) -> None:
        if self._worker is not None:
            return
        self._on_chunk = on_chunk
        self._on_event = on_event
        self._stopped.clear()
        self._worker = Thread(target=self._dispatch, name=f"live-{self.source.value}-capture", daemon=True)
        self._worker.start()
        try:
            api = self._native_api()
            set_error_handler = getattr(api, "set_error_handler", None)
            if callable(set_error_handler):
                set_error_handler(self._emit_native_error)
            api.start(self.source, self._device_id, self._capture)
        except Exception as exc:
            self._emit_native_error(exc)

    def pause(self) -> None:
        self._paused.set()
        self._native_api().pause()

    def resume(self) -> None:
        if not self._paused.is_set():
            return
        resume = getattr(self._native_api(), "resume", None)
        if not callable(resume):
            raise RuntimeError("native capture adapter cannot resume")
        resume()
        self._paused.clear()

    def stop(self) -> None:
        if self._worker is None:
            return
        self._stopped.set()
        if self._api is not None:
            self._api.stop()
        self._worker.join(timeout=1)
        self._worker = None
        self._paused.clear()

    def _capture(self, frames: Any, timestamp_ns: int | None = None, sample_rate: int = 48_000) -> None:
        if self._stopped.is_set() or self._paused.is_set():
            return
        copied = np.array(frames, dtype=np.float32, copy=True, order="C")
        if copied.ndim == 1:
            copied = copied[:, None]
        if copied.ndim != 2 or not len(copied):
            self._emit(CaptureEventKind.STATUS, "native capture delivered invalid PCM frames")
            return
        with self._offset_lock:
            offset = self._offset
            self._offset += len(copied)
        chunk = PcmChunk(
            self.source,
            sample_rate,
            copied.shape[1],
            offset,
            copied,
            timestamp_ns or time_ns(),
        )
        if not self._queue.put(chunk):
            self._emit(CaptureEventKind.OVERFLOW, f"capture queue full; dropped_frames={len(copied)}", chunk)

    def release(self) -> None:
        """Drop the native handle acquired for device enumeration."""
        if self._worker is not None or self._api is None:
            return
        close = getattr(self._api, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        self._api = None

    def _native_api(self) -> NativeCaptureApi:
        if self._api is None:
            if self._api_loader is None:
                raise RuntimeError("native capture API is not configured")
            self._api = self._api_loader()
        return self._api

    def _emit_native_error(self, exc: Exception) -> None:
        detail = str(exc)
        permission_words = ("permission", "screen recording", "tcc", "not authorized")
        if isinstance(exc, PermissionError) or any(word in detail.casefold() for word in permission_words):
            self._emit(CaptureEventKind.PERMISSION_DENIED, detail)
        elif isinstance(exc, OSError):
            self._emit(CaptureEventKind.DEVICE_REMOVED, detail)
        else:
            self._emit(CaptureEventKind.STATUS, detail)

    def _emit(self, kind: CaptureEventKind, detail: str, chunk: PcmChunk | None = None) -> None:
        self._events.put(
            CaptureEvent(
                kind,
                self.source,
                chunk.sample_offset if chunk else self._offset,
                chunk.timestamp_ns if chunk else time_ns(),
                detail,
            )
        )

    def _dispatch(self) -> None:
        while True:
            try:
                event = self._events.get_nowait()
            except Empty:
                event = None
            if event is not None and self._on_event is not None:
                self._deliver_event(event)
            chunk = self._queue.get(timeout=0.02)
            if chunk is not None and self._on_chunk is not None:
                # A failing consumer must never silently end capture: in a
                # windowed frozen build the traceback would go nowhere.
                try:
                    self._on_chunk(chunk)
                except Exception as exc:
                    self._report_dispatch_failure(exc, chunk)
            if self._stopped.is_set() and chunk is None:
                return

    def _deliver_event(self, event: CaptureEvent) -> None:
        try:
            self._on_event(event)  # type: ignore[misc]
        except Exception:
            # Subscriber faults are theirs to own; capture keeps running.
            self.dispatch_failures += 1

    def _report_dispatch_failure(self, exc: Exception, chunk: PcmChunk) -> None:
        self.dispatch_failures += 1
        detail = f"chunk delivery failed: {type(exc).__name__}: {exc}"
        if detail in self._reported_failures:
            return
        self._reported_failures.add(detail)
        if self._on_event is None:
            return
        self._deliver_event(
            CaptureEvent(
                CaptureEventKind.STATUS,
                self.source,
                chunk.sample_offset,
                chunk.timestamp_ns,
                detail,
            )
        )
