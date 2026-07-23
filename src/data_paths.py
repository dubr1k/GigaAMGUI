"""Ранний выбор единого каталога данных без импорта Qt, torch или config."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

DATA_DIR_ENV = "GIGAAM_DATA_DIR"
DATA_DIR_RECOVERY_ENV = "GIGAAM_DATA_DIR_RECOVERY_REQUIRED"
_SELECTION_FILE = "data_directory.json"


@dataclass(frozen=True)
class DataLayout:
    root: Path
    runtime_dir: Path
    models_dir: Path
    pytorch_model_dir: Path
    huggingface_dir: Path
    onnx_model_dir: Path
    torch_home: Path
    nemo_home: Path
    deepfilter_dir: Path


def default_config_dir() -> Path:
    """Системный config-каталог, где хранится только указатель на выбранный root."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "GigaAMTranscriber"
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "GigaAMTranscriber"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "GigaAMTranscriber"


def selection_path() -> Path:
    return default_config_dir() / _SELECTION_FILE


def _normalized(path: str | os.PathLike[str]) -> Path:
    text = os.path.expandvars(os.path.expanduser(os.fspath(path).strip()))
    if not text:
        raise ValueError("Каталог данных не задан / Data directory is empty")
    result = Path(text).absolute()
    if sys.platform == "win32" and re.search(r"[а-яА-ЯёЁ]", str(result)):
        raise ValueError(
            "Путь к данным не должен содержать кириллицу в Windows / "
            "The Windows data path must not contain Cyrillic characters"
        )
    return result


def layout_for(path: str | os.PathLike[str]) -> DataLayout:
    root = _normalized(path)
    models = root / "models"
    return DataLayout(
        root=root,
        runtime_dir=root / "runtimes",
        models_dir=models,
        pytorch_model_dir=models / "gigaam",
        huggingface_dir=models / "huggingface",
        onnx_model_dir=models / "onnx",
        torch_home=models / "torch",
        nemo_home=models / "nemo",
        deepfilter_dir=models / "deepfilter",
    )


def ensure_data_layout(layout: DataLayout) -> None:
    """Создать layout и проверить запись безопасным уникальным temp-файлом."""
    for directory in (
        layout.root,
        layout.runtime_dir,
        layout.pytorch_model_dir,
        layout.huggingface_dir,
        layout.onnx_model_dir,
        layout.torch_home,
        layout.nemo_home,
        layout.deepfilter_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=".gigaam-write-test-", dir=layout.root):
        pass


def apply_data_dir(
    path: str | os.PathLike[str],
    *,
    force_specialized: bool = False,
    create: bool = True,
) -> DataLayout:
    """Направить все крупные кэши и runtime под единый root.

    Специализированные переменные окружения сохраняют приоритет, если вызывающий
    код не запросил принудительное переключение (GUI использует force при выборе).
    """
    layout = layout_for(path)
    if create:
        # Проверяем root до изменения процесса: нерабочий путь не должен
        # оставить частично переключённое окружение.
        ensure_data_layout(layout)
    values = {
        DATA_DIR_ENV: layout.root,
        "GIGAAM_RUNTIME_DIR": layout.runtime_dir,
        "GIGAAM_PYTORCH_MODEL_DIR": layout.pytorch_model_dir,
        "HF_HOME": layout.huggingface_dir,
        "TORCH_HOME": layout.torch_home,
        "NEMO_HOME": layout.nemo_home,
        "ONNX_MODEL_DIR": layout.onnx_model_dir,
        "GIGAAM_DEEPFILTER_DIR": layout.deepfilter_dir,
    }
    for key, value in values.items():
        if key == DATA_DIR_ENV or force_specialized or key not in os.environ:
            os.environ[key] = str(value)
    # Если пользователь переопределил HF_HOME, старые cache-переменные должны
    # следовать за ним, а не внезапно возвращаться под GIGAAM_DATA_DIR.
    hf_hub = Path(os.environ["HF_HOME"]) / "hub"
    for key in ("HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE"):
        if force_specialized or key not in os.environ:
            os.environ[key] = str(hf_hub)
    return layout


def data_dir_from_argv(argv: list[str] | tuple[str, ...] | None = None) -> str | None:
    args = list(sys.argv if argv is None else argv)
    for index, arg in enumerate(args[1:], start=1):
        if arg.startswith("--data-dir="):
            value = arg.split("=", 1)[1]
            if not value:
                raise ValueError("Для --data-dir требуется путь / --data-dir requires a path")
            return value
        if arg == "--data-dir":
            if index + 1 >= len(args):
                raise ValueError("Для --data-dir требуется путь / --data-dir requires a path")
            value = args[index + 1]
            if not value or value.startswith("-"):
                raise ValueError("Для --data-dir требуется путь / --data-dir requires a path")
            return value
    return None


def _locator(path: str | os.PathLike[str] | None) -> Path:
    return Path(path) if path is not None else selection_path()


def has_data_dir_selection(*, locator_path: str | os.PathLike[str] | None = None) -> bool:
    return _locator(locator_path).is_file()


def load_data_dir_selection(*, locator_path: str | os.PathLike[str] | None = None) -> str | None:
    target = _locator(locator_path)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("data_dir")
    return str(value) if isinstance(value, str) and value.strip() else None


def _save_locator(value: str | None, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps({"schema": 1, "data_dir": value}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)
    os.environ.pop(DATA_DIR_RECOVERY_ENV, None)
    return target


def save_data_dir_selection(
    path: str | os.PathLike[str],
    *,
    locator_path: str | os.PathLike[str] | None = None,
) -> Path:
    layout = layout_for(path)
    ensure_data_layout(layout)
    return _save_locator(str(layout.root), _locator(locator_path))


def save_default_data_dir_selection(
    *,
    locator_path: str | os.PathLike[str] | None = None,
) -> Path:
    """Запомнить осознанный выбор legacy/default, чтобы не спрашивать снова."""
    return _save_locator(None, _locator(locator_path))


def bootstrap_data_dir(
    argv: list[str] | tuple[str, ...] | None = None,
    *,
    locator_path: str | os.PathLike[str] | None = None,
) -> DataLayout | None:
    """Применить каталог с приоритетом CLI > env > сохранённый GUI-выбор."""
    command_line = data_dir_from_argv(argv)
    selected = command_line or os.environ.get(DATA_DIR_ENV)
    if selected:
        return apply_data_dir(selected)

    saved = load_data_dir_selection(locator_path=locator_path)
    if not saved:
        return None
    try:
        return apply_data_dir(saved)
    except OSError:
        # Съёмный диск может быть отключён. Запускаемся на legacy-default,
        # чтобы пользователь мог выбрать доступный каталог через GUI.
        os.environ[DATA_DIR_RECOVERY_ENV] = "1"
        return None
