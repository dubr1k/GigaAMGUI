"""Issue #42: the default system-audio device must follow the default speakers."""

import pytest

from src.live.types import CaptureSource


class FakePyAudioModule:
    paWASAPI = 13
    paFloat32 = 1
    paContinue = 0

    def __init__(self, devices, default_input=0, default_output=1):
        self._devices = devices
        self._default_input = default_input
        self._default_output = default_output

    def PyAudio(self):  # noqa: N802 - mirrors the PyAudio API
        return FakePyAudio(self._devices, self._default_input, self._default_output)


class FakePyAudio:
    def __init__(self, devices, default_input, default_output):
        self._devices = devices
        self._default_input = default_input
        self._default_output = default_output
        self.opened = None
        self.terminated = False

    def get_device_count(self):
        return len(self._devices)

    def get_device_info_by_index(self, index):
        return self._devices[index]

    def get_default_input_device_info(self):
        return self._devices[self._default_input]

    def get_host_api_info_by_type(self, _host_api_type):
        return {"defaultOutputDevice": self._default_output}

    def open(self, **kwargs):
        self.opened = kwargs
        return FakeStream()

    def terminate(self):
        self.terminated = True


class FakeStream:
    def start_stream(self):
        return None

    def stop_stream(self):
        return None

    def close(self):
        return None


def device(index, name, *, loopback=False, inputs=2, rate=48_000):
    return {
        "index": index,
        "name": name,
        "isLoopbackDevice": loopback,
        "maxInputChannels": inputs,
        "maxOutputChannels": 0 if loopback or inputs else 2,
        "defaultSampleRate": rate,
    }


DEVICES = [
    device(0, "Headset Microphone"),
    device(1, "Speakers (Realtek)", inputs=0),
    device(2, "Digital Output (S/PDIF) [Loopback]", loopback=True),
    device(3, "Speakers (Realtek) [Loopback]", loopback=True),
]


def _api(**kwargs):
    from src.live.capture.windows import _PyAudioWASAPI

    return _PyAudioWASAPI(FakePyAudioModule(DEVICES, **kwargs))


def test_default_system_device_follows_the_default_playback_endpoint():
    devices = _api().devices(CaptureSource.SYSTEM)

    default = [item for item in devices if item["is_default"]]
    assert [item["name"] for item in default] == ["Speakers (Realtek) [Loopback]"]


def test_system_capture_without_a_device_id_opens_the_default_loopback():
    api = _api()

    api.start(CaptureSource.SYSTEM, None, lambda *args: None)

    assert api._audio.opened["input_device_index"] == 3


def test_microphone_default_still_follows_the_default_input_device():
    devices = _api().devices(CaptureSource.MIC)

    assert [item["name"] for item in devices] == ["Headset Microphone"]
    assert devices[0]["is_default"] is True


def test_missing_loopback_endpoint_reports_an_actionable_error():
    from src.live.capture.windows import _PyAudioWASAPI

    api = _PyAudioWASAPI(FakePyAudioModule([device(0, "Headset Microphone")], default_output=0))

    with pytest.raises(OSError, match="loopback"):
        api.start(CaptureSource.SYSTEM, None, lambda *args: None)


def test_releasing_a_probe_adapter_frees_the_native_handle():
    from src.live.capture.windows import WindowsSystemAudioAdapter

    api = _api()
    adapter = WindowsSystemAudioAdapter(api=api)
    native = api._audio

    adapter.devices()
    adapter.release()

    assert native.terminated is True
    assert adapter._api is None


def test_releasing_a_running_adapter_keeps_capture_alive():
    from src.live.capture.windows import WindowsMicrophoneAdapter

    api = _api()
    adapter = WindowsMicrophoneAdapter(api=api)
    adapter.start(lambda chunk: None, lambda event: None)
    try:
        adapter.release()
        assert adapter._api is api
    finally:
        adapter.stop()
