"""Характеризующие тесты transcription_service — фабрика процессора."""
from src.core.processor import TranscriptionProcessor
from src.services import transcription_service


class _Loader:
    pass


class _Stats:
    pass


def test_build_processor_returns_processor_with_deps():
    loader, stats = _Loader(), _Stats()

    def my_logger(msg):
        pass

    def my_progress(evt):
        pass

    proc = transcription_service.build_processor(
        loader, stats, logger=my_logger, progress_callback=my_progress,
    )
    assert isinstance(proc, TranscriptionProcessor)
    assert proc.model_loader is loader
    assert proc.stats is stats
    assert proc.logger is my_logger
    assert proc.progress_callback is my_progress


def test_build_processor_defaults_logger_to_print():
    proc = transcription_service.build_processor(_Loader(), _Stats())
    # процессор подставляет print, если logger не задан
    assert proc.logger is print
    assert proc.progress_callback is None


def test_build_processor_accepts_prepared_diarization_backend():
    prepared = object()

    processor = transcription_service.build_processor(
        _Loader(),
        _Stats(),
        diarization_manager=prepared,
        diarization_backend="onnx",
    )

    assert processor._diarization_manager is prepared
    assert processor._active_diarization_backend == "onnx"
