import json
import os

import pytest

from src import data_paths

_MANAGED_ENV = (
    "GIGAAM_DATA_DIR",
    "GIGAAM_DATA_DIR_RECOVERY_REQUIRED",
    "GIGAAM_RUNTIME_DIR",
    "GIGAAM_PYTORCH_MODEL_DIR",
    "HF_HOME",
    "HUGGINGFACE_HUB_CACHE",
    "TRANSFORMERS_CACHE",
    "TORCH_HOME",
    "NEMO_HOME",
    "ONNX_MODEL_DIR",
    "GIGAAM_DEEPFILTER_DIR",
)


@pytest.fixture(autouse=True)
def clean_managed_environment(monkeypatch):
    for key in _MANAGED_ENV:
        monkeypatch.delenv(key, raising=False)
    yield
    # apply_data_dir writes os.environ directly, outside monkeypatch bookkeeping.
    # Remove those values; monkeypatch then restores the process's initial state.
    for key in _MANAGED_ENV:
        os.environ.pop(key, None)


def test_apply_data_dir_routes_all_large_and_persistent_data(tmp_path):
    selected = tmp_path / "portable data"

    layout = data_paths.apply_data_dir(selected)

    assert layout.root == selected.resolve()
    assert os.environ["GIGAAM_DATA_DIR"] == str(selected.resolve())
    assert os.environ["GIGAAM_RUNTIME_DIR"] == str(selected.resolve() / "runtimes")

    assert os.environ["GIGAAM_PYTORCH_MODEL_DIR"] == str(selected.resolve() / "models" / "gigaam")
    assert os.environ["HF_HOME"] == str(selected.resolve() / "models" / "huggingface")
    assert os.environ["HUGGINGFACE_HUB_CACHE"] == str(
        selected.resolve() / "models" / "huggingface" / "hub"
    )
    assert os.environ["TRANSFORMERS_CACHE"] == os.environ["HUGGINGFACE_HUB_CACHE"]
    assert os.environ["TORCH_HOME"] == str(selected.resolve() / "models" / "torch")
    assert os.environ["NEMO_HOME"] == str(selected.resolve() / "models" / "nemo")
    assert os.environ["ONNX_MODEL_DIR"] == str(selected.resolve() / "models" / "onnx")
    assert os.environ["GIGAAM_DEEPFILTER_DIR"] == str(
        selected.resolve() / "models" / "deepfilter"
    )

    assert layout.runtime_dir.is_dir()
    assert layout.pytorch_model_dir.is_dir()
    assert layout.huggingface_dir.is_dir()
    assert layout.onnx_model_dir.is_dir()
    assert layout.torch_home.is_dir()
    assert layout.nemo_home.is_dir()
    assert layout.deepfilter_dir.is_dir()


def test_specialized_environment_override_wins_over_data_dir(tmp_path, monkeypatch):
    explicit_hf = tmp_path / "explicit-hf"
    monkeypatch.setenv("HF_HOME", str(explicit_hf))

    data_paths.apply_data_dir(tmp_path / "portable")

    assert os.environ["HF_HOME"] == str(explicit_hf)
    assert os.environ["HUGGINGFACE_HUB_CACHE"] == str(explicit_hf / "hub")
    assert os.environ["TRANSFORMERS_CACHE"] == str(explicit_hf / "hub")


def test_command_line_data_dir_has_priority_over_environment_and_saved_choice(tmp_path, monkeypatch):
    saved = tmp_path / "saved"
    env = tmp_path / "env"
    command_line = tmp_path / "command-line"
    locator = tmp_path / "locator.json"
    locator.write_text(json.dumps({"data_dir": str(saved)}), encoding="utf-8")
    monkeypatch.setenv("GIGAAM_DATA_DIR", str(env))

    layout = data_paths.bootstrap_data_dir(
        ["program", "--data-dir", str(command_line)],
        locator_path=locator,
    )

    assert layout is not None
    assert layout.root == command_line.resolve()


def test_saved_choice_is_loaded_without_command_line_or_environment(tmp_path):
    selected = tmp_path / "saved"
    locator = tmp_path / "locator.json"
    data_paths.save_data_dir_selection(selected, locator_path=locator)

    layout = data_paths.bootstrap_data_dir(["program"], locator_path=locator)

    assert layout is not None
    assert layout.root == selected.resolve()


def test_default_choice_is_persisted_without_forcing_a_custom_directory(tmp_path):
    locator = tmp_path / "locator.json"

    data_paths.save_default_data_dir_selection(locator_path=locator)

    assert data_paths.has_data_dir_selection(locator_path=locator) is True
    assert data_paths.load_data_dir_selection(locator_path=locator) is None


def test_missing_value_after_data_dir_is_rejected():
    with pytest.raises(ValueError, match="--data-dir"):
        data_paths.data_dir_from_argv(["program", "--data-dir"])
    with pytest.raises(ValueError, match="--data-dir"):
        data_paths.data_dir_from_argv(["program", "--data-dir="])
    with pytest.raises(ValueError, match="--data-dir"):
        data_paths.data_dir_from_argv(["program", "--data-dir", "--help"])


def test_unavailable_saved_directory_falls_back_without_poisoning_environment(
    tmp_path, monkeypatch
):
    saved = tmp_path / "unavailable"
    locator = tmp_path / "locator.json"
    locator.write_text(json.dumps({"data_dir": str(saved)}), encoding="utf-8")
    monkeypatch.setattr(
        data_paths,
        "ensure_data_layout",
        lambda _layout: (_ for _ in ()).throw(PermissionError("read-only")),
    )

    assert data_paths.bootstrap_data_dir(["program"], locator_path=locator) is None
    assert "GIGAAM_DATA_DIR" not in os.environ
    assert os.environ[data_paths.DATA_DIR_RECOVERY_ENV] == "1"


def test_windows_runtime_path_rejects_cyrillic(tmp_path, monkeypatch):
    monkeypatch.setattr(data_paths.sys, "platform", "win32", raising=False)

    with pytest.raises(ValueError, match="кириллиц|Cyrillic"):
        data_paths.apply_data_dir(tmp_path / "модели")
