"""Проверки контрактов ASR и общих утилит."""

import pytest

from src.core.asr.types import BackendCapabilities, TranscriptionSegment, parse_bool, validate_backend_name


def test_validate_backend_name_accepts_known_values():
    assert validate_backend_name("auto") == "auto"
    assert validate_backend_name("MLX") == "mlx"
    assert validate_backend_name("ONNX") == "onnx"
    assert validate_backend_name("pytorch") == "pytorch"


def test_validate_backend_name_rejects_unknown():
    with pytest.raises(ValueError):
        validate_backend_name("openvino")


def test_parse_bool_variants():
    assert parse_bool("true") is True
    assert parse_bool("0") is False
    assert parse_bool("yes", default=False) is True
    assert parse_bool("maybe", default=True) is True
    assert parse_bool(None, default=True) is True


def test_transcription_segment_shape():
    segment: TranscriptionSegment = {"transcription": "тест", "boundaries": (0.0, 12.5)}
    start, end = segment["boundaries"]
    assert segment["transcription"]
    assert start >= 0.0
    assert end >= start
    assert isinstance(segment["boundaries"], tuple)


def test_backend_capabilities_fields_are_typed_and_readable():
    caps = BackendCapabilities(backend="pytorch", model="e2e_rnnt", device="cpu")
    assert caps.backend == "pytorch"
    assert caps.supports_local_asr is True
    assert caps.provider is None
    assert caps.quantization is None


def test_backend_capabilities_expose_onnx_runtime_details():
    caps = BackendCapabilities(
        backend="onnx",
        model="v3_e2e_rnnt",
        device="cuda",
        provider="CUDAExecutionProvider",
        quantization="int8",
        provider_fallback_reason=None,
    )

    assert caps.provider == "CUDAExecutionProvider"
    assert caps.quantization == "int8"


def test_progress_callback_signature_remains_optional():
    from src.core.asr.base import ASRBackend

    class _NoopBackend:
        name = "noop"

        def load(self, logger=None):
            return True

        def transcribe_longform(self, audio_path, progress_callback=None):
            return []

        def unload(self):
            return None

        def is_loaded(self):
            return True

        def capabilities(self):
            return BackendCapabilities(backend="noop", model="noop", device="cpu")

    backend: ASRBackend = _NoopBackend()
    assert backend.transcribe_longform("x.wav") == []
