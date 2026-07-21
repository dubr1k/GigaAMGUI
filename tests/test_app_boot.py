import json

import pytest

import app


@pytest.fixture(autouse=True)
def isolate_saved_boot_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("GIGAAM_CONFIG_DIR", str(tmp_path))


def test_boot_does_not_require_torch_when_mlx_is_available(monkeypatch):
    monkeypatch.setattr(app, "ASR_BACKEND", "auto")
    monkeypatch.setattr(app, "_is_mlx_available", lambda: True)
    monkeypatch.setattr(app.sys, "platform", "darwin", raising=False)
    monkeypatch.setattr(app.platform, "machine", lambda: "arm64")

    assert app._boot_requires_torch() is False


def test_boot_requires_torch_when_auto_and_mlx_is_unavailable(monkeypatch):
    monkeypatch.setattr(app, "ASR_BACKEND", "auto")
    monkeypatch.setattr(app, "_is_mlx_available", lambda: False)
    monkeypatch.setattr(app.sys, "platform", "darwin", raising=False)
    monkeypatch.setattr(app.platform, "machine", lambda: "arm64")

    assert app._boot_requires_torch() is True


def test_boot_does_not_require_torch_for_explicit_onnx(monkeypatch):
    monkeypatch.setattr(app, "ASR_BACKEND", "onnx")

    assert app._boot_requires_torch() is False


def test_boot_still_requires_torch_for_non_macos_auto_before_quality_gate(monkeypatch):
    monkeypatch.setattr(app, "ASR_BACKEND", "auto")
    monkeypatch.setattr(app, "_is_onnx_available", lambda: True)
    monkeypatch.setattr(app.sys, "platform", "linux", raising=False)

    assert app._boot_requires_torch() is True


def test_boot_uses_saved_onnx_backend_before_torch_activation(monkeypatch, tmp_path):
    monkeypatch.setenv("GIGAAM_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(app, "ASR_BACKEND", "pytorch")
    (tmp_path / "user_settings.json").write_text(
        json.dumps({"asr_backend": "onnx"}),
        encoding="utf-8",
    )

    assert app._boot_requires_torch() is False


def test_saved_onnx_provider_is_read_without_qt_or_torch(monkeypatch, tmp_path):
    monkeypatch.setenv("GIGAAM_CONFIG_DIR", str(tmp_path))
    (tmp_path / "user_settings.json").write_text(
        json.dumps({"onnx_provider": "CUDA"}),
        encoding="utf-8",
    )

    assert app._saved_onnx_provider() == "cuda"


def test_onnx_auto_activates_an_installed_selected_cuda_runtime(monkeypatch):
    fake_rm = type(
        "RuntimeManager",
        (),
        {
            "get_selected_variant": staticmethod(lambda: "cu124"),
            "is_installed": staticmethod(lambda variant: variant == "cu124"),
            "torch_device_for": staticmethod(lambda variant: "cuda"),
        },
    )
    monkeypatch.setattr(app.sys, "platform", "win32", raising=False)

    assert app._installed_onnx_cuda_variant("auto", runtime_manager=fake_rm) == "cu124"


def test_onnx_cpu_does_not_activate_or_download_cuda_runtime(monkeypatch):
    class RuntimeManager:
        @staticmethod
        def get_selected_variant():
            raise AssertionError("CPU provider must not inspect/install CUDA runtime")

    monkeypatch.setattr(app.sys, "platform", "linux", raising=False)

    assert app._installed_onnx_cuda_variant("cpu", runtime_manager=RuntimeManager) is None


def test_onnx_auto_does_not_download_when_cuda_runtime_is_missing(monkeypatch):
    fake_rm = type(
        "RuntimeManager",
        (),
        {
            "get_selected_variant": staticmethod(lambda: "cu128"),
            "is_installed": staticmethod(lambda _variant: False),
            "torch_device_for": staticmethod(lambda _variant: "cuda"),
        },
    )
    monkeypatch.setattr(app.sys, "platform", "linux", raising=False)

    assert app._installed_onnx_cuda_variant("auto", runtime_manager=fake_rm) is None
