#!/usr/bin/env python3
"""Собрать папку models/hf для офлайн-варианта релиза.

По умолчанию офлайн-сборка везёт ONNX-цепочку: распознавание, VAD и
кластерную диаризацию. --include-transformers-asr дополнительно включает все
модели Liquid: две pinned Transformers и обе multilingual CTC (FP32 и int8).

Модели сначала скачиваются в обычный кэш приложения, а затем переносятся в
целевую папку без каталога blobs: в кэше snapshots — символьные ссылки на
blobs, и упаковщик, разыменовывая их, почти удваивает размер архива.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Репозитории ONNX-цепочки в раскладке кэша HuggingFace.
OFFLINE_REPOS = (
    "models--istupakov--gigaam-v3-onnx",
    "models--istupakov--silero-vad-onnx",
    "models--onnx-community--pyannote-segmentation-3.0",
    "models--wespeaker--wespeaker-voxceleb-resnet34",
)

# Pinned ONNX snapshots are used only by the larger, full Liquid offline set.
# The large FP32 graph stores its weights separately in the .onnx.data file.
EXTRA_LIQUID_ONNX_MODELS = {
    "multilingual_ctc": (
        "458860e1983aef670dd9795fb6af603c82767d5d",
        ("README.md", "config.json", "multilingual_ctc.onnx", "multilingual_ctc.int8.onnx",
         "multilingual_ctc.yaml", "multilingual_vocab.txt"),
    ),
    "multilingual_large_ctc": (
        "07665ab5e54371dd1ac7b8b10f06478003723573",
        ("README.md", "config.json", "multilingual_large_ctc.onnx", "multilingual_large_ctc.onnx.data",
         "multilingual_large_ctc.int8.onnx", "multilingual_large_ctc.yaml", "multilingual_vocab.txt"),
    ),
}


# HuggingFace с shared-раннеров GitHub периодически отвечает ошибкой сети или
# лимитом; huggingface_hub превращает это в LocalEntryNotFoundError, и без
# повтора офлайн-сборка падает на одной неудачной попытке (CI v2.3.0, Intel).
DOWNLOAD_ATTEMPTS = 4
RETRY_DELAYS = (10, 30, 60)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _download_with_retries(download: Callable[[], tuple[list[str], list[str]]], *, what: str):
    """Повторить загрузку, пока список неудач не опустеет или не кончатся попытки."""
    ok: list[str] = []
    failed: list[str] = []
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        ok, failed = download()
        if not failed:
            return ok, failed
        if attempt < DOWNLOAD_ATTEMPTS:
            delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS)) - 1]
            print(f"{what}: не скачано {failed}, попытка {attempt}/{DOWNLOAD_ATTEMPTS}, повтор через {delay} с")
            _sleep(delay)
    return ok, failed


def _retry_engine(load: Callable[[], object], *, what: str) -> None:
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            load()
            return
        except Exception as exc:  # noqa: BLE001 — сетевые ошибки HF не имеют общего базового класса
            if attempt == DOWNLOAD_ATTEMPTS:
                raise
            delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS)) - 1]
            print(f"{what}: {exc}, попытка {attempt}/{DOWNLOAD_ATTEMPTS}, повтор через {delay} с")
            _sleep(delay)


def fetch_models(provider: str = "cpu") -> None:
    """Прогреть кэш: ASR, VAD и обе ONNX-модели кластерной диаризации."""
    from download_models import download_onnx_models, download_onnx_vad

    _, failed = _download_with_retries(
        lambda: download_onnx_models(("v3_e2e_rnnt",), provider=provider), what="ASR"
    )
    if failed:
        raise SystemExit(f"не скачаны ASR-модели: {failed}")

    _, vad_failed = _download_with_retries(
        lambda: download_onnx_vad(model="silero", provider=provider), what="VAD"
    )
    if vad_failed:
        raise SystemExit(f"не скачан VAD: {vad_failed}")

    # Диаризацию download_models.py не покрывает — тянем через сами движки.
    from src.core.diarization.onnx_embeddings import OnnxSpeakerEmbeddings
    from src.core.diarization.onnx_segmentation import OnnxSegmentation

    _retry_engine(lambda: OnnxSegmentation(provider=provider)._ensure_session(), what="сегментация")
    _retry_engine(lambda: OnnxSpeakerEmbeddings(provider=provider)._ensure_model(), what="эмбеддинги")
    print("✓ модели диаризации загружены")


def _full_liquid_model_specs():
    from src.core.asr.models import TRANSFORMERS_ASR_FILES, TRANSFORMERS_ASR_MODELS, onnx_model_repo

    for repo_id, revision in TRANSFORMERS_ASR_MODELS.values():
        yield repo_id, revision, (*TRANSFORMERS_ASR_FILES, "LICENSE")
    for model, (revision, files) in EXTRA_LIQUID_ONNX_MODELS.items():
        yield onnx_model_repo(model), revision, files


def fetch_full_liquid_models(source_hub: Path) -> None:
    """Download the pinned extra model files, not other revisions or repository assets."""
    from huggingface_hub import snapshot_download
    from src.core.asr.models import snapshot_missing_files
    from src.utils.model_cache import hf_repo_cache_name

    for repo_id, revision, files in _full_liquid_model_specs():
        def download() -> None:
            snapshot_download(
                repo_id=repo_id,
                revision=revision,
                cache_dir=str(source_hub),
                allow_patterns=list(files),
            )
            snapshot = source_hub / hf_repo_cache_name(repo_id) / "snapshots" / revision
            missing = snapshot_missing_files(snapshot, files)
            if missing:
                raise RuntimeError(f"неполный snapshot {repo_id}@{revision}: {', '.join(missing)}")

        _retry_engine(download, what=repo_id)
        print(f"✓ {repo_id}@{revision}")


def _copy_full_liquid_models(source_hub: Path, target_hub: Path) -> None:
    from src.core.asr.models import snapshot_missing_files
    from src.utils.model_cache import hf_repo_cache_name

    snapshots = []
    for repo_id, revision, files in _full_liquid_model_specs():
        relative = Path(hf_repo_cache_name(repo_id)) / "snapshots" / revision
        source = source_hub / relative
        missing = snapshot_missing_files(source, files)
        if missing:
            raise SystemExit(f"неполный snapshot {repo_id}@{revision}: {', '.join(missing)}")
        snapshots.append((repo_id, source, target_hub / relative, files))

    for repo_id, source, target, files in snapshots:
        target.mkdir(parents=True, exist_ok=True)
        for name in files:
            destination = target / name
            if destination.is_symlink():
                raise SystemExit(f"целевой файл не должен быть символьной ссылкой: {destination}")
            shutil.copy2(source / name, destination, follow_symlinks=True)
        missing = snapshot_missing_files(target, files)
        if missing:
            raise SystemExit(f"неполная копия {repo_id}: {', '.join(missing)}")
        print(f"✓ {repo_id} (pinned snapshot)")


def _validate_base_offline_models(target_hub: Path) -> None:
    """Check the existing ONNX chain with the same offline resolvers as the worker."""
    from onnx_asr.loader import create_asr_resolver, create_se_resolver, create_vad_resolver
    from src.utils.model_cache import _resolve_snapshot_from_hub

    models = (
        ("istupakov/gigaam-v3-onnx", "gigaam-v3-e2e-rnnt", create_asr_resolver),
        ("istupakov/silero-vad-onnx", "silero", create_vad_resolver),
        ("onnx-community/pyannote-segmentation-3.0", "onnx-community/pyannote-segmentation-3.0", create_vad_resolver),
        ("wespeaker/wespeaker-voxceleb-resnet34", "wespeaker/wespeaker-voxceleb-resnet34", create_se_resolver),
    )
    for repo_id, model, resolver in models:
        snapshot = _resolve_snapshot_from_hub(repo_id, target_hub)
        if snapshot is None:
            raise SystemExit(f"нет однозначного snapshot {repo_id} в offline cache")
        try:
            files = resolver(model, snapshot, offline=True).resolve_model()
            missing = [str(path) for path in files.values() if not path.is_file() or path.stat().st_size == 0]
            if missing:
                raise ValueError(f"пустые или отсутствующие файлы: {', '.join(missing)}")
        except Exception as exc:
            raise SystemExit(f"неполный offline cache {repo_id}: {exc}") from exc



def copy_without_blobs(source_hub: Path, target_hub: Path, *, include_transformers_asr: bool = False) -> int:
    """Скопировать refs и snapshots реальными файлами, пропустив blobs."""
    target_hub.mkdir(parents=True, exist_ok=True)
    for repo in OFFLINE_REPOS:
        source_repo = source_hub / repo
        if not source_repo.is_dir():
            raise SystemExit(f"нет в кэше: {source_repo}")
        for part in ("refs", "snapshots"):
            source_part = source_repo / part
            if source_part.is_dir():
                shutil.copytree(
                    source_part,
                    target_hub / repo / part,
                    symlinks=False,
                    dirs_exist_ok=True,
                )
        print(f"✓ {repo}")
    if include_transformers_asr:
        _validate_base_offline_models(target_hub)
        _copy_full_liquid_models(source_hub, target_hub)
    return sum(path.stat().st_size for path in target_hub.rglob("*") if path.is_file())


def build_full_liquid_direct(source_hub: Path, target_hub: Path) -> int:
    """Stage baseline and pinned extras once, publishing the complete hub atomically."""
    from huggingface_hub import snapshot_download
    from src.core.asr.models import snapshot_missing_files
    from src.utils.model_cache import hf_repo_cache_name

    if target_hub.exists() or target_hub.is_symlink():
        raise SystemExit(f"целевой hub уже существует: {target_hub}")
    target_hub.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".gigaam-full-offline-", dir=target_hub.parent) as temporary:
        staged_hub = Path(temporary) / "hub"
        copy_without_blobs(source_hub, staged_hub)
        _validate_base_offline_models(staged_hub)

        for repo_id, revision, files in _full_liquid_model_specs():
            snapshot = staged_hub / hf_repo_cache_name(repo_id) / "snapshots" / revision

            def download() -> None:
                snapshot_download(
                    repo_id=repo_id,
                    revision=revision,
                    local_dir=str(snapshot),
                    allow_patterns=list(files),
                )
                missing = snapshot_missing_files(snapshot, files)
                if missing:
                    raise RuntimeError(f"неполный snapshot {repo_id}@{revision}: {', '.join(missing)}")

            _retry_engine(download, what=repo_id)
            metadata = snapshot / ".cache"
            if metadata.exists():
                shutil.rmtree(metadata)
            print(f"✓ {repo_id}@{revision}")

        total = sum(path.stat().st_size for path in staged_hub.rglob("*") if path.is_file())
        if target_hub.exists() or target_hub.is_symlink():
            raise SystemExit(f"целевой hub уже существует: {target_hub}")
        staged_hub.rename(target_hub)
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("models/hf"),
        help="Куда положить кэш (по умолчанию models/hf в корне репозитория)",
    )
    parser.add_argument("--provider", default="cpu")
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Только перенести уже скачанное из кэша приложения",
    )
    parser.add_argument(
        "--include-transformers-asr",
        action="store_true",
        help="Добавить все pinned модели для полного offline Liquid (включая CTC и custom code)",
    )
    parser.add_argument(
        "--direct-expanded-download",
        action="store_true",
        help="Скачать дополнительные модели прямо в новую offline-сборку без второго HF blob-кэша",
    )
    parser.add_argument(
        "--baseline-hub",
        type=Path,
        help="Существующий baseline hub для --direct-expanded-download (без повторной загрузки ONNX)",
    )
    args = parser.parse_args()
    if args.direct_expanded_download and (not args.include_transformers_asr or args.skip_download):
        parser.error("--direct-expanded-download требует --include-transformers-asr и загрузку дополнительных моделей")
    if args.baseline_hub is not None and not args.direct_expanded_download:
        parser.error("--baseline-hub требует --direct-expanded-download")

    from download_models import force_utf8_output

    force_utf8_output()

    from src.utils.model_cache import hf_hub_cache_dir

    source_hub = args.baseline_hub if args.baseline_hub is not None else hf_hub_cache_dir()
    if not args.skip_download:
        if args.baseline_hub is None:
            fetch_models(args.provider)
        if args.include_transformers_asr and not args.direct_expanded_download:
            fetch_full_liquid_models(source_hub)

    if not source_hub.is_dir():
        raise SystemExit(f"кэш приложения пуст: {source_hub}")

    if args.direct_expanded_download:
        total = build_full_liquid_direct(source_hub, args.output / "hub")
    else:
        total = copy_without_blobs(
            source_hub, args.output / "hub",
            include_transformers_asr=args.include_transformers_asr,
        )
    print(f"итого {total / 1024 / 1024:.0f} МБ в {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
