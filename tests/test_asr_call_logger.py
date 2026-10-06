"""Предупреждения распознавания идут в журнал текущего файла.

Backend запоминал logger при load(), а load_model() при уже загруженной модели
возвращается сразу. На сервере модель грузит первая задача, и предупреждения
(резервное разбиение без VAD и т.п.) всех следующих задач попадали в журнал
первой.
"""

from types import SimpleNamespace

import numpy as np
import soundfile as sf

from src.core.asr.onnx_backend import OnnxBackend
from src.core.model_loader import ModelLoader
from src.core.processor import TranscriptionProcessor


class _FailingSegmenter:
    def segment_file(self, audio_path, *, audio_duration):
        raise RuntimeError("vad graph failed")


class _Model:
    def with_timestamps(self):
        return self

    def recognize(self, _waveform, *, sample_rate):
        return SimpleNamespace(text="текст", tokens=[" текст"], timestamps=[0.1])


def _wav(tmp_path):
    path = tmp_path / "a.wav"
    sf.write(path, np.zeros(16000, dtype=np.float32), 16000)
    return str(path)


def _onnx_backend():
    return OnnxBackend(
        segmentation_mode="vad",
        model_factory=lambda *args, **kwargs: _Model(),
        available_provider_probe=lambda: ("CPUExecutionProvider",),
        vad_segmenter_factory=lambda **kwargs: _FailingSegmenter(),
    )


def test_backend_warnings_go_to_the_logger_of_the_current_call(tmp_path):
    first_task: list[str] = []
    second_task: list[str] = []
    backend = _onnx_backend()
    assert backend.load(logger=first_task.append)
    first_task.clear()

    backend.transcribe_longform(_wav(tmp_path), logger=second_task.append)

    assert any("VAD недоступен" in line for line in second_task)
    assert not any("VAD недоступен" in line for line in first_task)


def test_model_loader_forwards_call_logger_to_backend(tmp_path):
    second_task: list[str] = []
    loader = ModelLoader(requested_backend="onnx")
    loader._backend = _onnx_backend()
    assert loader._backend.load(logger=lambda _m: None)

    loader.transcribe_longform(_wav(tmp_path), logger=second_task.append)

    assert any("VAD недоступен" in line for line in second_task)


def test_processor_passes_its_logger_for_each_file(tmp_path, monkeypatch):
    calls = []

    class Loader:
        def transcribe_longform(self, audio_path, progress_callback=None, logger=None):
            calls.append(logger)
            return [{"transcription": "x", "boundaries": (0.0, 1.0)}]

    logs: list[str] = []
    processor = TranscriptionProcessor(Loader(), object(), logger=logs.append)
    source = tmp_path / "in.wav"
    source.write_bytes(b"stub")
    monkeypatch.setattr(processor.audio_converter, "convert_to_wav", lambda *a, **k: str(source))
    monkeypatch.setattr("src.core.processor.AudioConverter.get_media_duration", lambda _p: 1.0)

    processor.process_file(str(source), str(tmp_path / "out"), 0, 1)

    assert calls == [logs.append]
