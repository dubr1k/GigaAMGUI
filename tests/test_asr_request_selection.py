import pytest

from src.services.transcription_service import (
    acquire_request_model_loader,
    available_asr_backends,
    normalize_asr_selection,
)


class _DefaultLoader:
    requested_backend = "auto"
    requested_model = "v3_e2e_rnnt"
    requested_provider = "auto"


class _PytorchDefaultLoader:
    requested_backend = "pytorch"
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


def test_provider_is_ignored_for_non_onnx_backends():
    """Провайдер, оставшийся в UI от ONNX, не должен плодить loader на задачу."""
    default = _DefaultLoader()
    selection = normalize_asr_selection(default, backend="pytorch", onnx_provider="cuda")

    assert selection.onnx_provider == "auto"

    loader, owned = acquire_request_model_loader(
        _PytorchDefaultLoader(),
        normalize_asr_selection(
            _PytorchDefaultLoader(), backend="pytorch", onnx_provider="cuda"
        ),
        loader_factory=lambda **kwargs: pytest.fail(f"unexpected loader: {kwargs}"),
    )

    assert owned is False
    assert loader is not None


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


def test_mlx_backend_is_offered_only_on_apple_silicon():
    """На Linux/Windows выбор MLX проходил валидацию, а задача падала на загрузке."""
    mac = available_asr_backends(platform_name="darwin", machine_name="arm64")
    linux = available_asr_backends(platform_name="linux", machine_name="x86_64")
    intel_mac = available_asr_backends(platform_name="darwin", machine_name="x86_64")

    assert mac == ["auto", "onnx", "mlx", "pytorch"]
    assert "mlx" not in linux
    assert "mlx" not in intel_mac
    assert linux == ["auto", "onnx", "pytorch"]
