import pytest

from src.core.asr.onnx_provider import onnx_session_providers, resolve_onnx_providers


def test_auto_provider_prefers_cuda_then_cpu_on_linux():
    selection = resolve_onnx_providers(
        "auto",
        available=("CPUExecutionProvider", "CUDAExecutionProvider"),
        platform_name="linux",
    )

    assert selection.requested == "auto"
    assert selection.active == "cuda"
    assert selection.providers == ("CUDAExecutionProvider", "CPUExecutionProvider")
    assert selection.fallback_reason is None


def test_auto_provider_prefers_coreml_then_cpu_on_macos():
    selection = resolve_onnx_providers(
        "auto",
        available=("CPUExecutionProvider", "CoreMLExecutionProvider"),
        platform_name="darwin",
    )

    assert selection.active == "coreml"
    assert selection.providers == ("CoreMLExecutionProvider", "CPUExecutionProvider")
    assert onnx_session_providers(selection) == [
        (
            "CoreMLExecutionProvider",
            {
                "ModelFormat": "MLProgram",
                "MLComputeUnits": "ALL",
                "RequireStaticInputShapes": "1",
            },
        ),
        "CPUExecutionProvider",
    ]


def test_auto_provider_prefers_directml_then_cpu_on_windows():
    selection = resolve_onnx_providers(
        "auto",
        available=("CPUExecutionProvider", "DmlExecutionProvider"),
        platform_name="win32",
    )

    assert selection.active == "directml"
    assert selection.providers == ("DmlExecutionProvider", "CPUExecutionProvider")


def test_auto_provider_uses_cpu_when_accelerator_is_unavailable():
    selection = resolve_onnx_providers(
        "auto",
        available=("CPUExecutionProvider",),
        platform_name="linux",
    )

    assert selection.active == "cpu"
    assert selection.providers == ("CPUExecutionProvider",)
    assert selection.fallback_reason == "Ускоренный ONNX provider недоступен; использован CPUExecutionProvider"


def test_explicit_provider_does_not_add_silent_cpu_fallback():
    selection = resolve_onnx_providers(
        "CUDA",
        available=("CUDAExecutionProvider", "CPUExecutionProvider"),
        platform_name="linux",
    )

    assert selection.requested == "cuda"
    assert selection.active == "cuda"
    assert selection.providers == ("CUDAExecutionProvider",)
    assert selection.fallback_reason is None


def test_explicit_missing_provider_raises():
    with pytest.raises(RuntimeError, match="CoreMLExecutionProvider"):
        resolve_onnx_providers(
            "coreml",
            available=("CPUExecutionProvider",),
            platform_name="darwin",
        )


def test_unknown_provider_is_rejected():
    with pytest.raises(ValueError, match="Unsupported ONNX provider"):
        resolve_onnx_providers(
            "openvino",
            available=("CPUExecutionProvider",),
            platform_name="linux",
        )


def test_auto_without_supported_providers_raises():
    with pytest.raises(RuntimeError, match="не найдено"):
        resolve_onnx_providers(
            "auto",
            available=("AzureExecutionProvider",),
            platform_name="linux",
        )
