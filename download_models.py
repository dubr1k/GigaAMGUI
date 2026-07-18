#!/usr/bin/env python3
"""Предварительная загрузка ONNX и legacy PyTorch моделей GigaAM."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable

from src.core.asr.models import ONNX_ASR_MODELS, onnx_model_name, validate_asr_model
from src.core.asr.onnx_provider import (
    available_onnx_providers,
    onnx_session_providers,
    resolve_onnx_providers,
)

DEFAULT_ONNX_MODELS = tuple(ONNX_ASR_MODELS)


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


def download_pytorch_models() -> tuple[list[str], list[str]]:
    """Скачать прежние GigaAM-модели только по явному запросу."""
    from src.utils.torch_patch import apply_torch_load_patch

    apply_torch_load_patch()
    from gigaam import load_model

    models = ("v3_e2e_rnnt", "v3_e2e_ctc", "v3_ctc", "v3_rnnt")
    downloaded: list[str] = []
    failed: list[str] = []
    for model in models:
        try:
            loaded = load_model(model, fp16_encoder=True)
            del loaded
            downloaded.append(model)
            print(f"✓ PyTorch модель {model} загружена")
        except Exception as exc:
            failed.append(model)
            print(f"✗ PyTorch модель {model}: {exc}")
    return downloaded, failed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("onnx", "pytorch", "all"), default="onnx")
    parser.add_argument("--provider", default="cpu")
    parser.add_argument("--quantization", choices=("int8",), default=None)
    parser.add_argument("--model-dir", default=None)
    parser.add_argument("--model", action="append", choices=DEFAULT_ONNX_MODELS)
    args = parser.parse_args()

    failed: list[str] = []
    if args.backend in {"onnx", "all"}:
        _, onnx_failed = download_onnx_models(
            args.model or DEFAULT_ONNX_MODELS,
            provider=args.provider,
            quantization=args.quantization,
            model_dir=args.model_dir,
        )
        failed.extend(onnx_failed)
    if args.backend in {"pytorch", "all"}:
        _, pytorch_failed = download_pytorch_models()
        failed.extend(pytorch_failed)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
