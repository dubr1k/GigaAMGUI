"""Сбой инициализации VAD запоминается, но не до перезапуска процесса.

Запоминание нужно, чтобы батч не качал недоступную модель на каждом файле.
Но на долгоживущем сервере один сетевой сбой отключал VAD навсегда: все
следующие файлы резались без детектора речи, пока процесс не перезапустят.
"""

from types import SimpleNamespace

import numpy as np
import soundfile as sf
import torch

from src.core.asr import longform
from src.core.asr.onnx_backend import OnnxBackend
from src.core.asr.pytorch_backend import PyTorchBackend
from src.core.asr.vad import VadUnavailableError
from tests.test_mlx_backend import _ready_backend


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class _Segmenter:
    def segment_file(self, _audio_path, *, audio_duration):
        return [(0.0, audio_duration)]


def _wav(tmp_path):
    path = tmp_path / "a.wav"
    sf.write(path, np.zeros(16000, dtype=np.float32), 16000)
    return str(path)


def _flaky_factory(calls):
    def factory(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise VadUnavailableError("network blip")
        return _Segmenter()

    return factory


def test_pytorch_vad_is_retried_after_cooldown(tmp_path, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(longform, "_now", clock)
    monkeypatch.setenv("HF_TOKEN", "hf_x")
    calls = []
    backend = PyTorchBackend(vad_segmenter_factory=_flaky_factory(calls))
    backend.model = SimpleNamespace(
        _device="cpu",
        _dtype=torch.float32,
        head=object(),
        forward=lambda wav, length: (wav, length),
        decoding=SimpleNamespace(decode=lambda *_args: ["текст"]),
    )
    backend.device = "cpu"
    path = _wav(tmp_path)

    backend.transcribe_longform(path)
    backend.transcribe_longform(path)
    assert len(calls) == 1
    assert backend.segmentation_mode == "overlap_chunks"

    clock.now += longform.VAD_RETRY_COOLDOWN_SECONDS + 1
    backend.transcribe_longform(path)

    assert len(calls) == 2
    assert backend.segmentation_mode == "vad"


def test_mlx_vad_is_retried_after_cooldown(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(longform, "_now", clock)
    monkeypatch.setenv("HF_TOKEN", "hf_x")
    calls = []
    backend, _ = _ready_backend(
        monkeypatch, duration_seconds=1.0, vad_segmenter_factory=_flaky_factory(calls)
    )

    backend.transcribe_longform("/tmp/a.wav")
    backend.transcribe_longform("/tmp/a.wav")
    assert len(calls) == 1

    clock.now += longform.VAD_RETRY_COOLDOWN_SECONDS + 1
    backend.transcribe_longform("/tmp/a.wav")

    assert len(calls) == 2
    assert backend.segmentation_mode == "vad"


def test_onnx_vad_is_retried_after_cooldown(tmp_path, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(longform, "_now", clock)

    class Model:
        def with_timestamps(self):
            return self

        def recognize(self, _waveform, *, sample_rate):
            return SimpleNamespace(text="текст", tokens=[" текст"], timestamps=[0.1])

    calls = []
    backend = OnnxBackend(
        segmentation_mode="vad",
        model_factory=lambda *args, **kwargs: Model(),
        available_provider_probe=lambda: ("CPUExecutionProvider",),
        vad_segmenter_factory=_flaky_factory(calls),
    )
    assert backend.load()
    path = _wav(tmp_path)

    backend.transcribe_longform(path)
    backend.transcribe_longform(path)
    assert len(calls) == 1

    clock.now += longform.VAD_RETRY_COOLDOWN_SECONDS + 1
    backend.transcribe_longform(path)

    assert len(calls) == 2
    assert backend.segmentation_mode == "vad"
