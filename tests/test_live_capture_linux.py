import sys
import time

import numpy as np
import pytest

from src.live.types import CaptureEventKind, CaptureSource


class RemovedLinuxApi:
    def devices(self, source):
        return [{"id": "monitor-1", "name": "Built-in Monitor", "sample_rate": 48_000, "channels": 2, "is_default": True}]

    def start(self, source, device_id, callback):
        raise OSError("device removed")

    def pause(self):
        pass

    def stop(self):
        pass


def wait_until(predicate):
    end = time.monotonic() + 1
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


def test_linux_monitor_device_is_enumerated_without_starting_capture():
    from src.live.capture.linux import LinuxSystemAudioAdapter

    adapter = LinuxSystemAudioAdapter(api=RemovedLinuxApi())

    assert adapter.devices()[0].id == "monitor-1"


def test_linux_removed_device_emits_source_local_event():
    from src.live.capture.linux import LinuxSystemAudioAdapter

    events = []
    adapter = LinuxSystemAudioAdapter(api=RemovedLinuxApi())
    adapter.start(lambda chunk: None, events.append)
    wait_until(lambda: events)
    adapter.stop()

    assert events[-1].kind is CaptureEventKind.DEVICE_REMOVED
    assert events[-1].source is CaptureSource.SYSTEM


def test_linux_missing_runtime_has_monitor_setup_instructions(monkeypatch):
    # Раньше тест подменял api_loader и проверял ImportError, минуя _load_linux_api,
    # то есть сообщение с инструкцией не проверялось вообще.
    from src.live.capture.factory import CaptureUnavailable
    from src.live.capture.linux import LinuxSystemAudioAdapter

    monkeypatch.setitem(sys.modules, "sounddevice", None)

    with pytest.raises(CaptureUnavailable, match="PipeWire|PulseAudio"):
        LinuxSystemAudioAdapter().devices()


class FakeSoundDevice:
    def __init__(self):
        self.started = []

    def query_devices(self):
        return [
            {"name": "USB microphone", "max_input_channels": 1, "default_samplerate": 44_100},
            {"name": "Monitor of Built-in Audio", "max_input_channels": 2, "default_samplerate": 48_000},
            {"name": "Speakers", "max_input_channels": 0, "default_samplerate": 48_000},
        ]

    def InputStream(self, **kwargs):
        self.started.append(kwargs)

        class Stream:
            def start(self):
                kwargs["callback"](np.ones((3, kwargs["channels"]), dtype=np.float32), 3, None, None)

            def stop(self):
                pass

            def close(self):
                pass

        return Stream()


def test_linux_sounddevice_selects_monitor_and_delivers_float32_frames():
    from src.live.capture.linux import SoundDeviceCapture

    native = SoundDeviceCapture(FakeSoundDevice())
    devices = native.devices(CaptureSource.SYSTEM)
    frames = []

    native.start(CaptureSource.SYSTEM, devices[0]["id"], lambda data, timestamp_ns, rate: frames.append((data, timestamp_ns, rate)))

    assert [device["name"] for device in devices] == ["Monitor of Built-in Audio"]
    assert frames[0][0].dtype == np.float32
    assert frames[0][0].shape == (3, 2)
    assert frames[0][1] is None
    assert frames[0][2] == 48_000


def test_linux_sounddevice_caps_capture_channels_for_pulse_aggregate():
    """Pulse/PipeWire aggregates report 32 input channels; FLAC accepts at most 8."""
    from src.live.capture.linux import SoundDeviceCapture

    sounddevice = FakeSoundDevice()
    sounddevice.query_devices = lambda: [
        {"name": "pulse", "max_input_channels": 32, "default_samplerate": 48_000},
        {"name": "Monitor of pulse aggregate", "max_input_channels": 32, "default_samplerate": 48_000},
    ]
    native = SoundDeviceCapture(sounddevice)
    frames = []

    native.start(CaptureSource.MIC, None, lambda data, timestamp_ns, rate: frames.append(data))
    native.start(CaptureSource.SYSTEM, None, lambda data, timestamp_ns, rate: frames.append(data))

    assert [kwargs["channels"] for kwargs in sounddevice.started] == [2, 2]
    assert [chunk.shape[1] for chunk in frames] == [2, 2]


def test_linux_system_capture_rejects_missing_monitor_source():
    from src.live.capture.factory import CaptureUnavailable
    from src.live.capture.linux import SoundDeviceCapture

    native = SoundDeviceCapture(FakeSoundDevice())
    native._sounddevice.query_devices = lambda: [{"name": "USB microphone", "max_input_channels": 1, "default_samplerate": 48_000}]

    with pytest.raises(CaptureUnavailable, match="monitor source"):
        native.start(CaptureSource.SYSTEM, None, lambda *_: None)


def test_linux_bundled_portaudio_is_resolved_only_inside_a_frozen_bundle(monkeypatch, tmp_path):
    """Вне бандла подмена ctypes не нужна и не должна происходить (issue #47)."""
    import ctypes.util

    from src.live.capture import linux

    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    original = ctypes.util.find_library

    with linux.bundled_portaudio_resolution():
        assert ctypes.util.find_library is original


def test_linux_bundled_portaudio_is_offered_to_sounddevice_and_restored(monkeypatch, tmp_path):
    """Внутри бандла ldconfig не видит вшитую копию — подставляем путь сами (issue #47)."""
    import ctypes.util

    from src.live.capture import linux

    library = tmp_path / linux._BUNDLED_PORTAUDIO_RELPATH
    library.parent.mkdir(parents=True)
    library.write_bytes(b"")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(ctypes.util, "find_library", lambda name: None)

    with linux.bundled_portaudio_resolution():
        assert ctypes.util.find_library("portaudio") == str(library)
        assert ctypes.util.find_library("ssl") is None

    assert ctypes.util.find_library("portaudio") is None


def test_linux_missing_portaudio_names_both_the_wheel_and_the_system_package(monkeypatch):
    """Сообщение из issue #47 советовало только несуществующий файл требований."""
    from src.live.capture.factory import CaptureUnavailable
    from src.live.capture.linux import _load_linux_api

    monkeypatch.setitem(sys.modules, "sounddevice", None)

    with pytest.raises(CaptureUnavailable, match="requirements-live-linux.txt"):
        _load_linux_api()
    with pytest.raises(CaptureUnavailable, match="libportaudio2"):
        _load_linux_api()
