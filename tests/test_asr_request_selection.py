import pytest

from src.services.transcription_service import (
    acquire_request_model_loader,
    normalize_asr_selection,
)


class _DefaultLoader:
    requested_backend = "auto"
    requested_model = "v3_e2e_rnnt"
    requested_provider = "auto"


def test_blank_asr_selection_reuses_default_loader():
    default = _DefaultLoader()
    selection = normalize_asr_selection(default)

    loader, owned = acquire_request_model_loader(
        default,
        selection,
        loader_factory=lambda **kwargs: pytest.fail(f"unexpected loader: {kwargs}"),
    )

    assert loader is default
    assert owned is False
    assert selection.as_dict() == {
        "asr_backend": "auto",
        "asr_model": "v3_e2e_rnnt",
        "onnx_provider": "auto",
    }


def test_request_override_gets_isolated_loader():
    created = []

    def factory(**kwargs):
        created.append(kwargs)
        return object()

    selection = normalize_asr_selection(
        _DefaultLoader(),
        backend="onnx",
        model="multilingual_ctc",
        onnx_provider="coreml",
    )
    loader, owned = acquire_request_model_loader(
        _DefaultLoader(),
        selection,
        loader_factory=factory,
    )

    assert owned is True
    assert loader is not None
    assert created == [{
        "requested_backend": "onnx",
        "model_name": "multilingual_ctc",
        "model_revision": "multilingual_ctc",
        "onnx_provider": "coreml",
    }]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"backend": "whisper"}, "backend"),
        ({"model": "unknown"}, "model"),
        ({"onnx_provider": "metal"}, "provider"),
    ],
)
def test_invalid_asr_selection_is_rejected(kwargs, message):
    with pytest.raises(ValueError, match=message):
        normalize_asr_selection(_DefaultLoader(), **kwargs)
