import types

import pytest

from src.core.asr.onnx_provider import (
    available_onnx_providers,
    onnx_session_providers,
    resolve_onnx_providers,
)


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


def test_auto_provider_prefers_cuda_then_cpu_on_windows():
    selection = resolve_onnx_providers(
        "auto",
        available=(
            "CPUExecutionProvider",
            "DmlExecutionProvider",
            "CUDAExecutionProvider",
        ),
        platform_name="win32",
    )

    assert selection.active == "cuda"
    assert selection.providers == ("CUDAExecutionProvider", "CPUExecutionProvider")


def test_directml_remains_available_as_an_explicit_provider():
    selection = resolve_onnx_providers(
        "directml",
        available=("CPUExecutionProvider", "DmlExecutionProvider"),
        platform_name="win32",
    )

    assert selection.active == "directml"
    assert selection.providers == ("DmlExecutionProvider",)


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


@pytest.mark.parametrize("platform_name", ["win32", "linux"])
def test_provider_discovery_preloads_cuda_dependencies_for_gpu_auto(platform_name):
    calls = []
    fake_ort = types.SimpleNamespace(
        preload_dlls=lambda: calls.append("preload"),
        get_available_providers=lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"],
    )

    providers = available_onnx_providers(
        requested="auto",
        platform_name=platform_name,
        ort_module=fake_ort,
    )

    assert providers == ("CUDAExecutionProvider", "CPUExecutionProvider")
    assert calls == ["preload"]


@pytest.mark.parametrize(
    ("requested", "platform_name"),
    [("cpu", "win32"), ("cpu", "linux"), ("auto", "darwin"), ("coreml", "darwin")],
)
def test_provider_discovery_does_not_preload_cuda_when_it_cannot_be_used(
    requested,
    platform_name,
):
    calls = []
    fake_ort = types.SimpleNamespace(
        preload_dlls=lambda: calls.append("preload"),
        get_available_providers=lambda: ["CPUExecutionProvider"],
    )

    available_onnx_providers(
        requested=requested,
        platform_name=platform_name,
        ort_module=fake_ort,
    )

    assert calls == []
