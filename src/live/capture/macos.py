"""macOS Core Audio and ScreenCaptureKit adapter entry points."""

from __future__ import annotations

import platform
from collections.abc import Callable
from threading import Event
from typing import Any

import numpy as np

from ..types import CaptureSource
from .common import NativeCaptureApi, QueuedCaptureAdapter, SoundDeviceCapture
from .factory import CaptureUnavailable

_SCREEN_CAPTURE_OUTPUT_CLASS: type[Any] | None = None


def _screen_capture_output_class(foundation: Any, objc: Any) -> type[Any]:
    """Create the PyObjC delegate once; Objective-C class names are process-global."""
    global _SCREEN_CAPTURE_OUTPUT_CLASS
    if _SCREEN_CAPTURE_OUTPUT_CLASS is None:
        class ScreenCaptureOutput(foundation.NSObject):
            def initWithOwner_callback_(self, owner: Any, output_callback: Callable[..., None]) -> Any:
                self = objc.super(ScreenCaptureOutput, self).init()
                if self is not None:
                    self._owner = owner
                    self._callback = output_callback
                return self

            def stream_didOutputSampleBuffer_ofType_(self, _stream: Any, sample_buffer: Any, output_type: Any) -> None:
                if output_type != self._owner._sck.SCStreamOutputTypeAudio:
                    return
                self._owner._deliver_audio(sample_buffer, self._callback)

        _SCREEN_CAPTURE_OUTPUT_CLASS = ScreenCaptureOutput
    return _SCREEN_CAPTURE_OUTPUT_CLASS


def _load_macos_microphone_api() -> NativeCaptureApi:
    try:
        import sounddevice
    except ImportError as exc:
        raise CaptureUnavailable(
            "macOS microphone capture requires sounddevice. Install requirements-live-macos.txt "
            "and grant Microphone permission."
        ) from exc
    return SoundDeviceCapture(sounddevice)


def _load_macos_system_api() -> NativeCaptureApi:
    version = platform.mac_ver()[0]
    if version and int(version.split(".", maxsplit=1)[0]) < 13:
        raise CaptureUnavailable(
            "macOS system capture requires macOS 13+ with ScreenCaptureKit. "
            "Upgrade macOS or select a virtual audio device."
        )
    try:
        import AVFoundation
        import CoreMedia
        import Foundation
        import ScreenCaptureKit
    except ImportError as exc:
        raise CaptureUnavailable(
            "macOS system capture requires macOS 13+, PyObjC ScreenCaptureKit, and Screen Recording permission; "
            "install requirements-live-macos.txt or select a virtual audio device."
        ) from exc
    if not hasattr(ScreenCaptureKit, "SCStream"):
        raise CaptureUnavailable(
            "ScreenCaptureKit is unavailable on this macOS version. Upgrade to macOS 13+ "
            "or select a virtual audio device."
        )
    return _ScreenCaptureKitCapture(AVFoundation, CoreMedia, Foundation, ScreenCaptureKit)


class _ScreenCaptureKitCapture:
    """Capture desktop audio with ScreenCaptureKit without importing PyObjC on startup."""

    def __init__(self, avfoundation: Any, coremedia: Any, foundation: Any, screen_capture_kit: Any) -> None:
        self._avfoundation = avfoundation
        self._coremedia = coremedia
        self._foundation = foundation
        self._sck = screen_capture_kit
        self._stream: Any = None
        self._output: Any = None
        self._error_handler: Callable[[Exception], None] | None = None
        self._resume_args: tuple[CaptureSource, str | None, Callable[..., None]] | None = None
        self._audio_failure_count = 0
        self._audio_failure_reported = False

    def devices(self, source: CaptureSource) -> list[dict[str, Any]]:
        if source is not CaptureSource.SYSTEM:
            return []
        self._shareable_content()
        return [{"id": "default", "name": "Desktop system audio", "sample_rate": 48_000, "channels": 2, "is_default": True}]

    def start(self, source: CaptureSource, _device_id: str | None, callback: Callable[..., None]) -> None:
        if source is not CaptureSource.SYSTEM:
            raise OSError("ScreenCaptureKit only captures system audio")
        self._resume_args = (source, _device_id, callback)
        import objc

        content = self._shareable_content()
        displays = list(content.displays())
        if not displays:
            raise OSError("ScreenCaptureKit found no capturable display")
        configuration = self._sck.SCStreamConfiguration.alloc().init()
        configuration.setCapturesAudio_(True)
        configuration.setSampleRate_(48_000)
        configuration.setChannelCount_(2)
        stream_filter = self._sck.SCContentFilter.alloc().initWithDisplay_excludingWindows_(displays[0], [])
        output_class = _screen_capture_output_class(self._foundation, objc)
        self._output = output_class.alloc().initWithOwner_callback_(self, callback)
        self._stream = self._sck.SCStream.alloc().initWithFilter_configuration_delegate_(stream_filter, configuration, None)
        add_result = self._stream.addStreamOutput_type_sampleHandlerQueue_error_(
            self._output, self._sck.SCStreamOutputTypeAudio, None, None
        )
        if isinstance(add_result, tuple):
            added, error = add_result
        else:
            added, error = add_result, None
        if not added:
            self._raise_capture_error(error)
        completed = Event()
        result: list[Any] = []
        def on_start(start_error: Any) -> None:
            result.append(start_error)
            completed.set()

        self._stream.startCaptureWithCompletionHandler_(on_start)
        if not completed.wait(5):
            raise OSError("ScreenCaptureKit did not confirm capture startup")
        if result and result[0]:
            self._raise_capture_error(result[0])

    def pause(self) -> None:
        self.stop()

    def resume(self) -> None:
        if self._resume_args is None:
            raise RuntimeError("ScreenCaptureKit capture has not started")
        self.start(*self._resume_args)

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stopCaptureWithCompletionHandler_(lambda _error: None)
            self._stream = None
        self._output = None

    def set_error_handler(self, handler: Callable[[Exception], None]) -> None:
        self._error_handler = handler

    def _shareable_content(self) -> Any:
        completed = Event()
        result: list[Any] = []
        def on_shareable_content(content: Any, error: Any) -> None:
            result.extend((content, error))
            completed.set()

        self._sck.SCShareableContent.getShareableContentWithCompletionHandler_(on_shareable_content)
        if not completed.wait(5):
            raise OSError("ScreenCaptureKit did not return shareable content")
        if len(result) != 2 or result[1]:
            self._raise_capture_error(result[1] if len(result) == 2 else "unknown error")
        return result[0]

    def _deliver_audio(self, sample_buffer: Any, callback: Callable[..., None]) -> None:
        try:
            block = self._avfoundation.CMSampleBufferGetDataBuffer(sample_buffer)
            if block is None:
                return
            length = self._coremedia.CMBlockBufferGetDataLength(block)
            if length <= 0:
                return
            if length % 8:
                raise OSError(f"ScreenCaptureKit audio buffer has invalid PCM frame size: {length} bytes")
            status, data = self._coremedia.CMBlockBufferCopyDataBytes(block, 0, length, None)
            if status != 0:
                raise OSError(f"ScreenCaptureKit audio buffer copy failed (OSStatus {status})")
            if data is None or len(data) != length:
                actual_length = 0 if data is None else len(data)
                raise OSError(
                    f"ScreenCaptureKit audio buffer copy returned {actual_length} bytes; expected {length} bytes"
                )
            frames = np.frombuffer(data, dtype=np.float32).reshape(-1, 2)
        except Exception as exc:
            self._report_audio_failure(exc)
            return
        self._audio_failure_count = 0
        self._audio_failure_reported = False
        try:
            callback(frames, None, 48_000)
        except Exception as exc:
            if self._error_handler is not None:
                self._error_handler(exc)

    def _report_audio_failure(self, error: Exception) -> None:
        self._audio_failure_count += 1
        if self._audio_failure_count < 3 or self._audio_failure_reported:
            return
        self._audio_failure_reported = True
        if self._error_handler is not None:
            self._error_handler(error)

    @staticmethod
    def _raise_capture_error(error: Any) -> None:
        detail = str(error)
        if any(word in detail.casefold() for word in ("permission", "screen recording", "tcc", "not authorized")):
            raise PermissionError(f"Screen Recording permission denied: {detail}")
        raise OSError(f"ScreenCaptureKit capture failed: {detail}")


class _MacAdapter(QueuedCaptureAdapter):
    def __init__(
        self,
        source: CaptureSource,
        device_id: str | None = None,
        *,
        api: NativeCaptureApi | None = None,
        api_loader: Callable[[], NativeCaptureApi] = _load_macos_system_api,
    ) -> None:
        super().__init__(source, api, device_id, api_loader=api_loader)


class MacMicrophoneAdapter(_MacAdapter):
    def __init__(self, device_id: str | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("api_loader", _load_macos_microphone_api)
        super().__init__(CaptureSource.MIC, device_id, **kwargs)


class MacSystemAudioAdapter(_MacAdapter):
    def __init__(self, device_id: str | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("api_loader", _load_macos_system_api)
        super().__init__(CaptureSource.SYSTEM, device_id, **kwargs)

    def devices(self):
        try:
            return super().devices()
        except ImportError as exc:
            raise CaptureUnavailable(
                "macOS system capture requires macOS 13+ with ScreenCaptureKit; "
                "install requirements-live-macos.txt or select a virtual audio device."
            ) from exc
