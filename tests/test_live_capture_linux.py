import subprocess
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


class FakePactl:
    """Настоящий формат вывода pactl — парсинг должен проверяться, а не мокаться."""

    SOURCES_SHORT = (
        "0\talsa_output.pci-0000_04_00.6.analog-stereo.monitor\tPipeWire\ts32le 2ch 48000Hz\tIDLE\n"
        "1\talsa_input.usb-Logitech_C920.analog-stereo\tPipeWire\ts32le 1ch 44100Hz\tSUSPENDED\n"
        "2\talsa_output.usb-Logi_USB_Headset.analog-stereo.monitor\tPipeWire\ts32le 2ch 44100Hz\tIDLE\n"
    )

    def __init__(self, *, pid=4242, move_succeeds=True):
        self.pid = pid
        self.move_succeeds = move_succeeds
        #: Наша запись появляется у сервера только после открытия потока —
        #: именно по этому признаку она и опознаётся.
        self.open_streams = 0
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        command = args[1:]
        if command == ["list", "sources", "short"]:
            return self._ok(self.SOURCES_SHORT)
        if command == ["get-default-sink"]:
            return self._ok("alsa_output.usb-Logi_USB_Headset.analog-stereo\n")
        if command == ["list", "source-outputs"]:
            listing = "Source Output #7\n" '\tapplication.process.id = "1"\n'
            # Микрофон того же процесса, открытый до системного звука.
            listing += "Source Output #9\n" f'\tapplication.process.id = "{self.pid}"\n'
            if self.open_streams:
                listing += "Source Output #11\n" f'\tapplication.process.id = "{self.pid}"\n'
            return self._ok(listing)
        if command[:1] == ["move-source-output"]:
            return self._ok("") if self.move_succeeds else self._fail()
        return self._fail()

    @staticmethod
    def _ok(stdout):
        return subprocess.CompletedProcess(["pactl"], 0, stdout, "")

    @staticmethod
    def _fail():
        return subprocess.CompletedProcess(["pactl"], 1, "", "error")


class PulseOnlySoundDevice(FakeSoundDevice):
    """Перечисление PortAudio из issue #49: агрегаты есть, мониторов нет."""

    def __init__(self, pactl=None):
        super().__init__()
        self._pactl = pactl

    def InputStream(self, **kwargs):
        if self._pactl is not None:
            self._pactl.open_streams += 1
        return super().InputStream(**kwargs)

    def query_devices(self):
        return [
            {"name": "HD Pro Webcam C920: USB Audio (hw:3,0)", "max_input_channels": 2, "default_samplerate": 32_000},
            {"name": "default", "max_input_channels": 32, "default_samplerate": 48_000},
            {"name": "pulse", "max_input_channels": 32, "default_samplerate": 48_000},
        ]


@pytest.fixture
def pactl(monkeypatch):
    from src.live.capture import pulse

    fake = FakePactl()
    monkeypatch.setattr(pulse.subprocess, "run", fake)
    monkeypatch.setattr("src.live.capture.linux.os.getpid", lambda: fake.pid)
    return fake


def test_linux_system_devices_include_monitors_absent_from_portaudio(pactl):
    """Мониторы PipeWire не попадают в перечисление PortAudio (issue #49)."""
    from src.live.capture.linux import LinuxSoundDeviceCapture

    native = LinuxSoundDeviceCapture(PulseOnlySoundDevice(pactl))

    devices = native.devices(CaptureSource.SYSTEM)

    assert [device["name"] for device in devices] == [
        "alsa_output.pci-0000_04_00.6.analog-stereo.monitor",
        "alsa_output.usb-Logi_USB_Headset.analog-stereo.monitor",
    ]
    assert [device["sample_rate"] for device in devices] == [48_000, 44_100]
    # Монитор текущего sink по умолчанию — предвыбранный источник.
    assert [device["is_default"] for device in devices] == [False, True]
    # Микрофоны по-прежнему перечисляет PortAudio, звуковой сервер не спрашивается.
    assert [device["name"] for device in native.devices(CaptureSource.MIC)] == [
        "HD Pro Webcam C920: USB Audio (hw:3,0)",
        "default",
        "pulse",
    ]


def test_linux_pulse_monitor_opens_the_aggregate_and_moves_the_stream(pactl):
    """Монитор открывается через агрегат pulse и перецепляется на себя."""
    from src.live.capture.linux import LinuxSoundDeviceCapture

    sounddevice = PulseOnlySoundDevice(pactl)
    native = LinuxSoundDeviceCapture(sounddevice)
    frames = []

    native.start(CaptureSource.SYSTEM, None, lambda data, timestamp_ns, rate: frames.append((data, rate)))

    assert sounddevice.started[0]["device"] == 2  # индекс "pulse", а не монитора
    assert sounddevice.started[0]["channels"] == 2
    assert frames[0][1] == 44_100
    # Перецепляется именно новая запись, а не микрофон того же процесса (#9).
    assert pactl.calls[-1] == [
        "pactl",
        "move-source-output",
        "11",
        "alsa_output.usb-Logi_USB_Headset.analog-stereo.monitor",
    ]


def test_linux_pulse_monitor_start_fails_instead_of_recording_the_microphone(pactl):
    """Не перецепив поток, захват писал бы источник по умолчанию — то есть микрофон."""
    from src.live.capture.factory import CaptureUnavailable
    from src.live.capture.linux import LinuxSoundDeviceCapture

    pactl.move_succeeds = False
    sounddevice = PulseOnlySoundDevice(pactl)
    native = LinuxSoundDeviceCapture(sounddevice)

    with pytest.raises(CaptureUnavailable, match="move the capture stream"):
        native.start(CaptureSource.SYSTEM, None, lambda *_: None)

    assert native._stream is None


def test_linux_without_pactl_reports_how_to_enumerate_monitors(monkeypatch):
    """Без pactl список пуст — сообщение должно называть причину, а не только «включите монитор»."""
    from src.live.capture import pulse
    from src.live.capture.factory import CaptureUnavailable
    from src.live.capture.linux import LinuxSoundDeviceCapture

    monkeypatch.setattr(pulse.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("pactl")))
    native = LinuxSoundDeviceCapture(PulseOnlySoundDevice())

    assert native.devices(CaptureSource.SYSTEM) == []
    with pytest.raises(CaptureUnavailable, match="pulseaudio-utils"):
        native.start(CaptureSource.SYSTEM, None, lambda *_: None)
