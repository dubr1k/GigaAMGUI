"""Install Community-1 separately and publish only complete environments."""

from __future__ import annotations

import os
import platform
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

from filelock import FileLock, Timeout

from ...utils.atomic_json import load_json, save_json_atomic
from ...utils.runtime_manager import base_dir
from ..model_preparation import PreparationCancelled, PreparationState

_INSTALLER = Path(__file__).with_name("install_community_runtime.py")


def _runtime_root() -> Path:
    return base_dir() / "community-1"


def _venv_python(root: Path) -> Path:
    return root / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def runtime_python() -> Path:
    override = os.environ.get("GIGAAM_COMMUNITY_PYTHON")
    if override:
        return Path(override).expanduser()
    root = _runtime_root()
    marker = load_json(str(root / "current.json"), {})
    name = marker.get("directory") if isinstance(marker, dict) else None
    if isinstance(name, str) and name.startswith("install-") and Path(name).name == name:
        candidate = _venv_python(root / "versions" / name)
        if candidate.is_file():
            return candidate
    return _venv_python(root)


def install_hint() -> str:
    args = ["py", "-3.11", str(_INSTALLER), str(_runtime_root())] if sys.platform == "win32" else [
        "python3", str(_INSTALLER), str(_runtime_root())
    ]
    command = subprocess.list2cmdline(args) if sys.platform == "win32" else shlex.join(args)
    return (
        "Установите отдельный Community-1 runtime (Python 3.10–3.13, рекомендуется 3.11):\n"
        f"{command}\n"
        "Укажите путь к интерпретатору в GIGAAM_COMMUNITY_PYTHON; "
        "если переменная задана неверно, исправьте её или удалите."
    )


def isolated_environment() -> dict[str, str]:
    env = os.environ.copy()
    for key in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "PYTHONPATH", "PYTHONHOME", "_MEIPASS2"):
        env.pop(key, None)
    # A frozen app's library search paths are for its embedded Python, not this venv.
    if getattr(sys, "frozen", False):
        for key in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
            original = env.pop(f"{key}_ORIG", None)
            if original is None:
                env.pop(key, None)
            else:
                env[key] = original
    return env


def _bootstrap_python(cancel_check) -> str | None:
    if not getattr(sys, "frozen", False) and (3, 10) <= sys.version_info[:2] <= (3, 13):
        return sys.executable
    search_path = os.pathsep.join(filter(None, (os.environ.get("PATH"), "/opt/homebrew/bin", "/usr/local/bin")))
    names = ("python3.11", "python3.12", "python3.10", "python3.13", "python3")
    seen = set()
    for name in names:
        if cancel_check():
            raise PreparationCancelled("Установка Community-1 отменена")
        python = shutil.which(name, path=search_path)
        if not python or python in seen:
            continue
        seen.add(python)
        try:
            result = subprocess.run(
                [python, "-I", "-c", "import sys; sys.exit(not ((3, 10) <= sys.version_info[:2] <= (3, 13)))"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=isolated_environment(),
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return python
    return None


def _terminate_install(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    elif sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except OSError:
            process.terminate()
    else:
        process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        process.wait()


def ensure_runtime(report, cancel_check) -> Path:
    python = runtime_python()
    if python.is_file():
        return python
    if os.environ.get("GIGAAM_COMMUNITY_PYTHON"):
        raise RuntimeError(f"Community-1 runtime не найден: {python}\n{install_hint()}")
    if sys.platform == "darwin" and platform.machine().lower() in ("x86_64", "amd64"):
        raise RuntimeError(
            "Автоматическая установка Community-1 на Intel macOS не поддерживается: "
            "для закреплённого PyTorch 2.8 нет готового x86_64 macOS wheel. "
            "Используйте другой движок диаризации или укажите проверенный совместимый "
            "интерпретатор через GIGAAM_COMMUNITY_PYTHON."
        )
    if not _INSTALLER.is_file():
        raise RuntimeError("Установщик Community-1 отсутствует в приложении; переустановите сборку.")

    root = _runtime_root()
    root.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(root / ".install.lock"))
    while True:
        if cancel_check():
            raise PreparationCancelled("Установка Community-1 отменена")
        try:
            lock.acquire(timeout=0.2)
            break
        except Timeout:
            continue
    try:
        python = runtime_python()
        if python.is_file():
            return python
        bootstrap = _bootstrap_python(cancel_check)
        if not bootstrap:
            raise RuntimeError("Не найден Python 3.10–3.13 для установки Community-1.\n" + install_hint())

        report(
            PreparationState.DOWNLOADING,
            message="Установка отдельного runtime Community-1; требуются интернет и свободное место",
        )
        versions = root / "versions"
        versions.mkdir(parents=True, exist_ok=True)
        target = Path(tempfile.mkdtemp(prefix="install-", dir=versions))
        published = False
        process = None
        try:
            env = isolated_environment()
            process = subprocess.Popen(
                [bootstrap, "-I", str(_INSTALLER), str(target)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                start_new_session=os.name == "posix",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            while True:
                if cancel_check():
                    _terminate_install(process)
                    raise PreparationCancelled("Установка Community-1 отменена")
                try:
                    status = process.wait(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    continue
            if status != 0:
                raise RuntimeError(
                    f"Не удалось установить Community-1 (код {status}). "
                    "Для подробной ошибки запустите установку вручную:\n" + install_hint()
                )
            python = _venv_python(target)
            if not python.is_file():
                raise RuntimeError("Установщик Community-1 не создал интерпретатор.\n" + install_hint())
            if cancel_check():
                raise PreparationCancelled("Установка Community-1 отменена")
            save_json_atomic(str(root / "current.json"), {"directory": target.name})
            published = True
            return python
        finally:
            if process is not None and process.poll() is None:
                _terminate_install(process)
            if not published:
                shutil.rmtree(target, ignore_errors=True)
    finally:
        lock.release()
