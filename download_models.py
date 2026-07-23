#!/usr/bin/env python3
"""Предварительная загрузка ONNX и legacy PyTorch моделей GigaAM."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Iterable

from src.core.asr.models import ONNX_ASR_MODELS, onnx_model_name, validate_asr_model
from src.core.asr.onnx_provider import (
    available_onnx_providers,
    onnx_session_providers,
    resolve_onnx_providers,
)
from src.data_paths import apply_data_dir

DEFAULT_ONNX_MODELS = tuple(ONNX_ASR_MODELS)


def force_utf8_output() -> None:
    """Не дать кириллице уронить скрипт на консоли Windows.

    По умолчанию там cp1252, и первое же сообщение о загруженной модели
    падает UnicodeEncodeError — уже после того, как модель успешно скачана.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def download_onnx_models(
    models: Iterable[str] = DEFAULT_ONNX_MODELS,
    *,
    provider: str = "cpu",
    quantization: str | None = None,
    model_dir: str | None = None,
    loader: Callable | None = None,
    available_provider_probe: Callable[[], tuple[str, ...]] | None = None,
) -> tuple[list[str], list[str]]:
    """Скачать ONNX-модели, сохранив отдельный результат для каждой."""
    if loader is None:
        import onnx_asr

        loader = onnx_asr.load_model
    probe = available_provider_probe or available_onnx_providers
    selection = resolve_onnx_providers(provider, available=probe())
    downloaded: list[str] = []
    failed: list[str] = []
    for requested in models:
        model = validate_asr_model(requested)
        try:
            loaded = loader(
                onnx_model_name(model),
                path=model_dir,
                quantization=quantization,
            providers=onnx_session_providers(selection),
                preprocessor_config={"use_numpy_preprocessors": False},
            )
            del loaded
            downloaded.append(model)
            print(f"✓ ONNX модель {model} загружена")
        except Exception as exc:
            failed.append(model)
            print(f"✗ ONNX модель {model}: {exc}")
    return downloaded, failed


def download_onnx_vad(
    *,
    model: str = "silero",
    provider: str = "cpu",
    quantization: str | None = None,
    model_dir: str | None = None,
    loader: Callable | None = None,
    available_provider_probe: Callable[[], tuple[str, ...]] | None = None,
) -> tuple[list[str], list[str]]:
    """Скачать VAD-модель ONNX-сегментации.

    Без неё offline-развёртывание падает на первом же файле: режим сегментации
    по умолчанию — `vad`, и модель тянулась бы из сети уже во время работы.
    """
    if loader is None:
        import onnx_asr

        loader = onnx_asr.load_vad
    probe = available_provider_probe or available_onnx_providers
    selection = resolve_onnx_providers(provider, available=probe())
    try:
        loaded = loader(
            model,
            path=model_dir,
            quantization=quantization,
            providers=onnx_session_providers(selection),
        )
        del loaded
        print(f"✓ ONNX VAD {model} загружен")
        return [model], []
    except Exception as exc:
        print(f"✗ ONNX VAD {model}: {exc}")
        return [], [model]


def download_pytorch_models(*, loader: Callable | None = None) -> tuple[list[str], list[str]]:
    """Скачать прежние GigaAM-модели только по явному запросу."""
    if loader is None:
        from src.utils.torch_patch import apply_torch_load_patch

        apply_torch_load_patch()
        from gigaam import load_model

        loader = load_model

    assert loader is not None
    models = ("v3_e2e_rnnt", "v3_e2e_ctc", "v3_ctc", "v3_rnnt")
    download_root = os.environ.get("GIGAAM_PYTORCH_MODEL_DIR")
    downloaded: list[str] = []
    failed: list[str] = []
    for model in models:
        try:
            loaded = loader(model, fp16_encoder=True, download_root=download_root)
            del loaded
            downloaded.append(model)
            print(f"✓ PyTorch модель {model} загружена")
        except Exception as exc:
            failed.append(model)
            print(f"✗ PyTorch модель {model}: {exc}")
    return downloaded, failed


def main() -> int:
    force_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        help="Единая папка runtime и моделей (также GIGAAM_DATA_DIR)",
    )
    parser.add_argument("--backend", choices=("onnx", "pytorch", "all"), default="onnx")
    parser.add_argument("--provider", default="cpu")
    parser.add_argument("--quantization", choices=("int8",), default=None)
    parser.add_argument("--model-dir", default=None)
    parser.add_argument("--model", action="append", choices=DEFAULT_ONNX_MODELS)
    parser.add_argument("--skip-vad", action="store_true")
    args = parser.parse_args()

    if args.data_dir:
        apply_data_dir(args.data_dir, force_specialized=True)

    # Импорт после --data-dir: иначе config успевает назначить старый HF_HOME.
    from src.config import ONNX_MODEL_DIR, ONNX_QUANTIZATION, ONNX_VAD_MODEL

    quantization = args.quantization or ONNX_QUANTIZATION
    model_dir = args.model_dir if args.model_dir is not None else ONNX_MODEL_DIR

    failed: list[str] = []
    if args.backend in {"onnx", "all"}:
        _, onnx_failed = download_onnx_models(
            args.model or DEFAULT_ONNX_MODELS,
            provider=args.provider,
            quantization=quantization,
            model_dir=model_dir,
        )
        failed.extend(onnx_failed)
        if not args.skip_vad:
            _, vad_failed = download_onnx_vad(
                model=ONNX_VAD_MODEL,
                provider=args.provider,
                quantization=quantization,
                model_dir=model_dir,
            )
            failed.extend(vad_failed)
    if args.backend in {"pytorch", "all"}:
        _, pytorch_failed = download_pytorch_models()
        failed.extend(pytorch_failed)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
