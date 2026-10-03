import os
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = PROJECT_ROOT / "docker-entrypoint.sh"

_CACHE_ENV = (
    "GIGAAM_RUNTIME_DIR",
    "GIGAAM_PYTORCH_MODEL_DIR",
    "HF_HOME",
    "HUGGINGFACE_HUB_CACHE",
    "TORCH_HOME",
    "NEMO_HOME",
    "ONNX_MODEL_DIR",
    "GIGAAM_DEEPFILTER_DIR",
)


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _run_entrypoint(tmp_path: Path, *, extra_env: dict[str, str] | None = None):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    call_log = tmp_path / "calls.log"
    _write_executable(fake_bin / "id", "#!/bin/sh\nprintf '0\\n'\n")
    _write_executable(
        fake_bin / "mkdir",
        "#!/bin/sh\nprintf 'mkdir:%s\\n' \"$*\" >>\"$CALL_LOG\"\n",
    )
    _write_executable(
        fake_bin / "chown",
        "#!/bin/sh\nprintf 'chown:%s\\n' \"$*\" >>\"$CALL_LOG\"\n",
    )
    _write_executable(
        fake_bin / "gosu",
        "#!/bin/sh\nshift\nexport HOME=/home/gigaam\nexec \"$@\"\n",
    )

    data_root = tmp_path / "persistent data"
    env = os.environ.copy()
    for key in _CACHE_ENV:
        env.pop(key, None)
    env.update(
        {
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "CALL_LOG": str(call_log),
            "GIGAAM_DATA_DIR": str(data_root),
        }
    )
    env.update(extra_env or {})
    result = subprocess.run(
        [str(ENTRYPOINT), "env"],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    exported = dict(
        line.split("=", 1)
        for line in result.stdout.splitlines()
        if "=" in line
    )
    return data_root, exported, call_log.read_text(encoding="utf-8")


def test_entrypoint_exports_writable_data_layout_before_command(tmp_path):
    data_root, exported, calls = _run_entrypoint(tmp_path)

    assert exported["GIGAAM_RUNTIME_DIR"] == str(data_root / "runtimes")
    assert exported["GIGAAM_PYTORCH_MODEL_DIR"] == str(data_root / "models" / "gigaam")
    assert exported["HF_HOME"] == str(data_root / "models" / "huggingface")
    assert exported["HUGGINGFACE_HUB_CACHE"] == str(
        data_root / "models" / "huggingface" / "hub"
    )
    assert exported["TORCH_HOME"] == str(data_root / "models" / "torch")
    assert exported["NEMO_HOME"] == str(data_root / "models" / "nemo")
    assert exported["ONNX_MODEL_DIR"] == str(data_root / "models" / "onnx")
    assert exported["GIGAAM_DEEPFILTER_DIR"] == str(data_root / "models" / "deepfilter")
    assert exported["HOME"] == str(data_root / "runtime-home")
    assert str(data_root / "models" / "huggingface") in calls
    assert "/tmp/cache" in calls


def test_entrypoint_preserves_explicit_huggingface_override(tmp_path):
    explicit_hf = tmp_path / "explicit huggingface"

    _data_root, exported, calls = _run_entrypoint(
        tmp_path,
        extra_env={"HF_HOME": str(explicit_hf)},
    )

    assert exported["HF_HOME"] == str(explicit_hf)
    assert exported["HUGGINGFACE_HUB_CACHE"] == str(explicit_hf / "hub")
    assert str(explicit_hf) in calls


def test_entrypoint_hands_task_directories_to_the_app_user(tmp_path):
    # Bind mount ./uploads, ./results, ./logs создаётся Docker'ом как root:root,
    # если на хосте каталогов не было, — приложение под uid 1000 не могло писать.
    app_root = tmp_path / "app root"
    _data_root, _exported, calls = _run_entrypoint(
        tmp_path,
        extra_env={"GIGAAM_APP_DIR": str(app_root), "UPLOAD_DIR": "uploads", "RESULTS_DIR": "/srv/results"},
    )

    chowns = [line for line in calls.splitlines() if line.startswith("chown:")]
    task_dirs = (str(app_root / "uploads"), "/srv/results", str(app_root / "logs"))
    assert any(all(path in line for path in task_dirs) for line in chowns), chowns
    # Не рекурсивно: старые результаты и модели остаются как есть.
    assert all(" -R" not in line and "-R " not in line for line in chowns)
