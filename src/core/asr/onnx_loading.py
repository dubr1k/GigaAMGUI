"""Загрузка моделей onnx-asr с явным режимом докачки каталога.

Публичные ``onnx_asr.load_model``/``load_vad`` не принимают ``offline``, а
onnx-asr 0.12 считает любой существующий ``local_dir`` полным. Для каталога,
которым управляет приложение (см. ``OnnxModelLocation.offline=False``), нужен
``Manager.create_*(offline=False)``: он сначала берёт локальные файлы и только
при их нехватке скачивает недостающие в тот же каталог. ``Manager`` уже
используется для WeSpeaker (у SE нет публичной функции загрузки), версия
onnx-asr закреплена в requirements.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def load_asr_model(
    model: str,
    path: str | Path | None = None,
    *,
    offline: bool | None = None,
    quantization: str | None = None,
    providers: list[str] | None = None,
    preprocessor_config: dict | None = None,
) -> Any:
    if offline is None:
        import onnx_asr  # noqa: PLC0415

        return onnx_asr.load_model(
            model,
            path=path,
            quantization=quantization,
            providers=providers,
            preprocessor_config=preprocessor_config,
        )
    from onnx_asr.loader import Manager  # noqa: PLC0415

    return Manager(
        providers=providers,
        preprocessor_config=preprocessor_config,
    ).create_asr(model, path, quantization=quantization, offline=offline)


def load_vad_model(
    model: str,
    path: str | Path | None = None,
    *,
    offline: bool | None = None,
    quantization: str | None = None,
    providers: list[str] | None = None,
) -> Any:
    if offline is None:
        import onnx_asr  # noqa: PLC0415

        return onnx_asr.load_vad(model, path=path, quantization=quantization, providers=providers)
    from onnx_asr.loader import Manager  # noqa: PLC0415

    # Так же, как onnx_asr.load_vad: сессионные опции передаются конфигом VAD.
    config = {"sess_options": None, "providers": providers, "provider_options": None}
    return Manager().create_vad(
        model,
        path,
        quantization=quantization,
        offline=offline,
        config=config if providers is not None else None,
    )


def load_speaker_embedding_model(
    model: str,
    path: str | Path | None = None,
    *,
    offline: bool | None = None,
    providers: list[str] | None = None,
    preprocessor_config: dict | None = None,
) -> Any:
    from onnx_asr.loader import Manager  # noqa: PLC0415

    return Manager(
        providers=providers,
        preprocessor_config=preprocessor_config,
    ).create_se(model, local_dir=path, offline=offline)
