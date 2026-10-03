"""Разделяет встроенный read-only кэш моделей и пользовательский кэш."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .runtime_manager import bundled_hf_cache_dir, hf_cache_dir


def hf_repo_cache_name(repo_id: str) -> str:
    """Имя каталога репозитория в стандартном кэше Hugging Face Hub."""
    parts = repo_id.strip().split("/")
    if len(parts) != 2 or any(not part or part in {".", ".."} for part in parts):
        raise ValueError(f"Некорректный Hugging Face repo_id: {repo_id!r}")
    if any("\\" in part or "--" in part for part in parts):
        raise ValueError(f"Некорректный Hugging Face repo_id: {repo_id!r}")
    return "models--" + "--".join(parts)


def _contains_files(path: Path) -> bool:
    return path.is_dir() and any(candidate.is_file() for candidate in path.rglob("*"))


def _resolve_snapshot_from_hub(repo_id: str, hub: Path) -> Path | None:
    """Найти готовый snapshot внутри конкретного каталога Hugging Face Hub."""
    repository = hub / hf_repo_cache_name(repo_id)
    snapshots = repository / "snapshots"
    main_ref = repository / "refs" / "main"
    if main_ref.is_file():
        revision = main_ref.read_text(encoding="utf-8").strip()
        candidate = snapshots / revision
        if revision and _contains_files(candidate):
            return candidate

    complete = sorted(
        (candidate for candidate in snapshots.iterdir() if _contains_files(candidate)),
        key=lambda candidate: candidate.name,
    ) if snapshots.is_dir() else []
    if len(complete) == 1:
        return complete[0]
    return None


def resolve_bundled_snapshot(
    repo_id: str,
    *,
    bundled_root: str | Path | None = None,
) -> Path | None:
    """Найти готовый snapshot репозитория во встроенном кэше."""
    root = Path(bundled_root) if bundled_root is not None else bundled_hf_cache_dir()
    if root is None:
        return None
    return _resolve_snapshot_from_hub(repo_id, root / "hub")


def resolve_model_dir(
    repo_id: str,
    *,
    explicit: str | Path | None = None,
    bundled_root: str | Path | None = None,
) -> Path | None:
    """Явный путь важнее встроенного snapshot; None разрешает сетевую загрузку."""
    if explicit is not None:
        return Path(explicit)
    return resolve_bundled_snapshot(repo_id, bundled_root=bundled_root)


def hf_repo_is_cached(
    repo_id: str,
    *,
    bundled_root: str | Path | None = None,
    user_root: str | Path | None = None,
) -> bool:
    """Есть ли snapshot хотя бы в одном доступном локальном кэше."""
    if resolve_bundled_snapshot(repo_id, bundled_root=bundled_root) is not None:
        return True
    user_hub = Path(user_root) / "hub" if user_root is not None else hf_hub_cache_dir()
    return _resolve_snapshot_from_hub(repo_id, user_hub) is not None


def hf_hub_cache_dir() -> Path:
    """hub-кэш, в который на самом деле качает huggingface_hub.

    Это ``HF_HUB_CACHE`` библиотеки (он учитывает HUGGINGFACE_HUB_CACHE и
    HF_HUB_CACHE, а не только HF_HOME). Проверки «модель уже скачана» и
    собственные загрузки приложения (Sortformer) должны смотреть туда же,
    иначе одна и та же модель могла лежать в одном кэше, а искаться в другом.
    """
    try:
        from huggingface_hub.constants import HF_HUB_CACHE

        return Path(HF_HUB_CACHE)
    except ImportError:
        return hf_cache_dir() / "hub"


# Те же значения, что huggingface_hub считает «истиной» для HF_HUB_OFFLINE.
_ENV_TRUE_VALUES = {"1", "ON", "YES", "TRUE"}


def hf_hub_offline() -> bool:
    return os.environ.get("HF_HUB_OFFLINE", "").strip().upper() in _ENV_TRUE_VALUES


@dataclass(frozen=True)
class OnnxModelLocation:
    """Куда onnx-asr смотрит за моделью одного репозитория.

    ``path=None`` — стандартный кэш Hugging Face. ``offline=None`` оставляет
    поведение onnx-asr по умолчанию: существующий каталог считается полным и
    читается без сети. ``offline=False`` ставится только для каталога, которым
    управляет приложение: он может быть пустым или недокачанным, и onnx-asr
    должен сначала проверить локальные файлы, а затем докачать недостающие.
    """

    path: Path | None
    offline: bool | None = None


def onnx_model_subdir(root: str | Path, repo_id: str) -> Path:
    """Отдельный каталог модели внутри корня ONNX_MODEL_DIR."""
    hf_repo_cache_name(repo_id)  # та же проверка repo_id, без «..» и «\\»
    return Path(root) / repo_id.replace("/", "--")


def onnx_model_location(
    repo_id: str,
    *,
    root: str | Path | None,
    accept_flat_root: bool = False,
    bundled_root: str | Path | None = None,
    offline: bool | None = None,
) -> OnnxModelLocation:
    """Выбрать каталог модели ``repo_id`` для onnx-asr.

    ``ONNX_MODEL_DIR`` (каталог данных и Docker задают ``<root>/models/onnx``) —
    корень, а не каталог одной модели. Раньше он передавался onnx-asr как есть
    сразу ASR, VAD и обеим моделям диаризации. onnx-asr 0.12 считает любой
    существующий ``local_dir`` полным и уходит в офлайн, поэтому пустой каталог
    давал «v3_e2e_rnnt_encoder.onnx not found», а config.json разных
    репозиториев сталкивались в одном каталоге. Теперь у каждого репозитория
    свой подкаталог ``<root>/<org>--<name>``; пустой или недокачанный
    подкаталог дозагружается.

    ``accept_flat_root`` сохраняет старую раскладку для ASR: если в корне уже
    лежит ``config.json``, пользователь указал каталог одной модели. Остальные
    модели такой каталог не трогают и берут встроенный snapshot или HF-кэш.
    """
    if root is None:
        return OnnxModelLocation(resolve_bundled_snapshot(repo_id, bundled_root=bundled_root))

    root_path = Path(root)
    flat_root = (root_path / "config.json").is_file()
    if flat_root and accept_flat_root:
        return OnnxModelLocation(root_path)

    managed = onnx_model_subdir(root_path, repo_id)
    if not flat_root and _contains_files(managed):
        return OnnxModelLocation(managed, offline=False)

    # Готовая копия где угодно лучше новой загрузки: офлайн-набор рядом с
    # приложением или уже заполненный HF-кэш.
    bundled = resolve_bundled_snapshot(repo_id, bundled_root=bundled_root)
    if bundled is not None:
        return OnnxModelLocation(bundled)
    if offline is None:
        offline = hf_hub_offline()
    if flat_root or offline or hf_repo_is_cached(repo_id, bundled_root=bundled_root):
        return OnnxModelLocation(None)
    return OnnxModelLocation(managed, offline=False)


def onnx_model_is_local(
    repo_id: str,
    *,
    root: str | Path | None = None,
    accept_flat_root: bool = False,
) -> bool:
    """Лежит ли модель локально (для статуса «скачивается» при подготовке)."""
    location = onnx_model_location(repo_id, root=root, accept_flat_root=accept_flat_root)
    if location.path is not None and _contains_files(location.path):
        return True
    return hf_repo_is_cached(repo_id)
