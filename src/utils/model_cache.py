"""Разделяет встроенный read-only кэш моделей и пользовательский кэш."""

from __future__ import annotations

import os
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


def resolve_bundled_snapshot(
    repo_id: str,
    *,
    bundled_root: str | Path | None = None,
) -> Path | None:
    """Найти готовый snapshot репозитория во встроенном кэше."""
    root = Path(bundled_root) if bundled_root is not None else bundled_hf_cache_dir()
    if root is None:
        return None

    repository = root / "hub" / hf_repo_cache_name(repo_id)
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
    if user_root is None:
        user_root = os.environ.get("HF_HOME") or hf_cache_dir()
    return resolve_bundled_snapshot(repo_id, bundled_root=user_root) is not None
