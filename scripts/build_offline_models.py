#!/usr/bin/env python3
"""Собрать папку models/hf для офлайн-варианта релиза.

Офлайн-сборка везёт полностью ONNX-цепочку: распознавание, VAD и оба движка
диаризации. Она единственная работает без torch и без токена HuggingFace,
то есть действительно не требует сети.

Модели сначала скачиваются в обычный кэш приложения, а затем переносятся в
целевую папку без каталога blobs: в кэше snapshots — символьные ссылки на
blobs, и упаковщик, разыменовывая их, удваивает размер (884 МБ против 1.7 ГБ).
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Репозитории ONNX-цепочки в раскладке кэша HuggingFace.
OFFLINE_REPOS = (
    "models--istupakov--gigaam-v3-onnx",
    "models--istupakov--silero-vad-onnx",
    "models--onnx-community--pyannote-segmentation-3.0",
    "models--wespeaker--wespeaker-voxceleb-resnet34",
)


def fetch_models(provider: str = "cpu") -> None:
    """Прогреть кэш: ASR, VAD и обе модели диаризации."""
    from download_models import download_onnx_models, download_onnx_vad

    _, failed = download_onnx_models(("v3_e2e_rnnt",), provider=provider)
    if failed:
        raise SystemExit(f"не скачаны ASR-модели: {failed}")

    _, vad_failed = download_onnx_vad(model="silero", provider=provider)
    if vad_failed:
        raise SystemExit(f"не скачан VAD: {vad_failed}")

    # Диаризацию download_models.py не покрывает — тянем через сами движки.
    from src.core.diarization.onnx_embeddings import OnnxSpeakerEmbeddings
    from src.core.diarization.onnx_segmentation import OnnxSegmentation

    OnnxSegmentation(provider=provider)._ensure_session()
    OnnxSpeakerEmbeddings(provider=provider)._ensure_model()
    print("✓ модели диаризации загружены")


def copy_without_blobs(source_hub: Path, target_hub: Path) -> int:
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
    return sum(path.stat().st_size for path in target_hub.rglob("*") if path.is_file())


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
    args = parser.parse_args()

    from download_models import force_utf8_output

    force_utf8_output()

    if not args.skip_download:
        fetch_models(args.provider)

    from src.utils.runtime_manager import hf_cache_dir

    source_hub = hf_cache_dir() / "hub"
    if not source_hub.is_dir():
        raise SystemExit(f"кэш приложения пуст: {source_hub}")

    total = copy_without_blobs(source_hub, args.output / "hub")
    print(f"итого {total / 1024 / 1024:.0f} МБ в {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
