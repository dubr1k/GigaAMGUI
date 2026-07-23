import importlib.util
import json
import sys
import types

import pytest

import app


def test_torch_availability_rejects_namespace_stub(monkeypatch):
    """PyInstaller may expose an empty torch namespace although no runtime is usable."""
    namespace_spec = type("Spec", (), {"origin": None, "loader": None})()
    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: namespace_spec)

    assert app._torch_is_available() is False


def test_prepare_torch_runtime_switches_saved_runtime_before_model_import(monkeypatch):
    calls = []
    runtime_manager = types.SimpleNamespace(
        get_selected_variant=lambda: "cpu",
        is_installed=lambda variant: variant == "cpu",
        switch_runtime=lambda variant: calls.append(variant),
    )
    monkeypatch.setattr(app, "_torch_is_available", lambda: True)

    ready = app._prepare_torch_runtime(runtime_manager, lambda: pytest.fail("unexpected chooser"))

    assert ready is True
    assert calls == ["cpu"]


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


def test_gui_data_directory_argument_is_not_treated_as_an_open_path(tmp_path):
    media = tmp_path / "audio.wav"
    media.write_bytes(b"audio")

    argv = ["app.py", "--data-dir", str(tmp_path), str(media)]

    assert app._qt_argv(argv) == ["app.py", str(media)]
    assert app._argv_open_paths(argv) == [str(media)]


def test_first_portable_launch_recovers_unavailable_saved_root_before_model_download(
    tmp_path, monkeypatch
):
    from src import config

    selected = tmp_path / "portable"
    calls = []
    monkeypatch.setattr(config, "ONNX_MODEL_DIR", None)

    class FakeMessageBox:
        class StandardButton:
            Yes = 1
            No = 2

        @staticmethod
        def question(*_args, **_kwargs):
            return FakeMessageBox.StandardButton.Yes

    fake_widgets = types.SimpleNamespace(
        QFileDialog=types.SimpleNamespace(
            getExistingDirectory=lambda *_args, **_kwargs: str(selected)
        ),
        QMessageBox=FakeMessageBox,
    )
    monkeypatch.setitem(sys.modules, "PyQt6.QtWidgets", fake_widgets)
    monkeypatch.setattr(app.sys, "frozen", True, raising=False)
    monkeypatch.delenv("GIGAAM_DATA_DIR", raising=False)
    monkeypatch.setenv(app.DATA_DIR_RECOVERY_ENV, "1")
    monkeypatch.setattr(app, "has_data_dir_selection", lambda: True)
    monkeypatch.setattr(
        app,
        "apply_data_dir",
        lambda path, **kwargs: (
            monkeypatch.setenv("ONNX_MODEL_DIR", str(selected / "models" / "onnx")),
            calls.append(("apply", path, kwargs)),
        )[-1],
    )
    monkeypatch.setattr(
        app,
        "save_data_dir_selection",
        lambda path: calls.append(("save", path)),
    )

    assert app._offer_data_directory_on_first_portable_launch() == str(selected)
    assert calls == [
        ("apply", str(selected), {"force_specialized": True}),
        ("save", str(selected)),
    ]
    assert config.ONNX_MODEL_DIR == str(selected / "models" / "onnx")
