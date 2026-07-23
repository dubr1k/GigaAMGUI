import os
import subprocess
import sys
from pathlib import Path

from src.core.asr.pytorch_backend import PyTorchBackend  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_config_import_bootstraps_selected_data_directory(tmp_path):
    selected = tmp_path / "portable"
    env = os.environ.copy()
    env["GIGAAM_DATA_DIR"] = str(selected)
    managed = (
        "GIGAAM_RUNTIME_DIR",
        "GIGAAM_CONFIG_DIR",
        "GIGAAM_PYTORCH_MODEL_DIR",
        "HF_HOME",
        "HUGGINGFACE_HUB_CACHE",
        "TRANSFORMERS_CACHE",
        "TORCH_HOME",
        "NEMO_HOME",
        "ONNX_MODEL_DIR",
        "GIGAAM_DEEPFILTER_DIR",
    )
    for key in managed:
        env.pop(key, None)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json, os; import src.config as c; "
                f"print(json.dumps({{k: os.environ.get(k) for k in {managed!r}}}))"
            ),
        ],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    values = __import__("json").loads(result.stdout.strip().splitlines()[-1])
    assert values == {
        "GIGAAM_RUNTIME_DIR": str(selected.resolve() / "runtimes"),
        "GIGAAM_CONFIG_DIR": None,
        "GIGAAM_PYTORCH_MODEL_DIR": str(selected.resolve() / "models" / "gigaam"),
        "HF_HOME": str(selected.resolve() / "models" / "huggingface"),
        "HUGGINGFACE_HUB_CACHE": str(selected.resolve() / "models" / "huggingface" / "hub"),
        "TRANSFORMERS_CACHE": str(selected.resolve() / "models" / "huggingface" / "hub"),
        "TORCH_HOME": str(selected.resolve() / "models" / "torch"),
        "NEMO_HOME": str(selected.resolve() / "models" / "nemo"),
        "ONNX_MODEL_DIR": str(selected.resolve() / "models" / "onnx"),
        "GIGAAM_DEEPFILTER_DIR": str(selected.resolve() / "models" / "deepfilter"),
    }


def test_user_env_specialized_cache_override_beats_saved_data_layout(tmp_path):
    selected = tmp_path / "portable"
    explicit_hf = tmp_path / "explicit-hf"
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / ".env").write_text(
        f"GIGAAM_DATA_DIR={selected}\nHF_HOME={explicit_hf}\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["GIGAAM_CONFIG_DIR"] = str(config_dir)
    for key in (
        "GIGAAM_DATA_DIR",
        "HF_HOME",
        "HUGGINGFACE_HUB_CACHE",
        "TRANSFORMERS_CACHE",
    ):
        env.pop(key, None)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json, os, src.config; print(json.dumps({k: os.environ.get(k) for k in "
            "['GIGAAM_DATA_DIR','HF_HOME','HUGGINGFACE_HUB_CACHE']}))",
        ],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    values = __import__("json").loads(result.stdout.strip().splitlines()[-1])
    assert values == {
        "GIGAAM_DATA_DIR": str(selected.resolve()),
        "HF_HOME": str(explicit_hf),
        "HUGGINGFACE_HUB_CACHE": str(explicit_hf / "hub"),
    }


def test_pytorch_backend_uses_selected_writable_model_directory(tmp_path, monkeypatch):
    selected = tmp_path / "models" / "gigaam"
    monkeypatch.setenv("GIGAAM_PYTORCH_MODEL_DIR", str(selected))
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)

    root = PyTorchBackend()._bundled_download_root()

    assert root == str(selected)
    assert selected.is_dir()


def test_cli_help_exposes_data_directory_option():
    result = subprocess.run(
        [sys.executable, "cli.py", "--help"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    assert "--data-dir" in result.stdout
    assert "GIGAAM_DATA_DIR" in result.stdout


def test_docker_web_maps_the_selected_host_data_directory():
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "GIGAAM_DATA_DIR=/data" in compose
    assert "${GIGAAM_DATA_DIR:-./cache}:/data" in compose
    assert "ENV HF_HOME=/models/huggingface" not in dockerfile
    assert 'ENTRYPOINT ["/app/docker-entrypoint.sh"]' in dockerfile
    assert "gosu" in dockerfile


def test_model_downloader_help_exposes_data_directory_option():
    result = subprocess.run(
        [sys.executable, "download_models.py", "--help"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    assert "--data-dir" in result.stdout
