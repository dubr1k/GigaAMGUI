"""Единая фабрика TranscriptionProcessor.

Все четыре поверхности (api/web/cli/gui) собирали процессор вручную одинаковым
конструктором. Фабрика убирает это дублирование, не меняя сигнатуру процессора:
управление форматами/диаризацией остаётся за каждой поверхностью через process_file.
"""
from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

from src.core.asr.models import validate_asr_model
from src.core.asr.types import validate_backend_name
from src.core.model_preparation import (
    ModelPreparationPlan,
    PreparationCancelled,
    PreparationState,
    PreparationStep,
)
from src.core.processor import TranscriptionProcessor

ONNX_PROVIDERS = ("auto", "cpu", "cuda", "tensorrt", "coreml", "directml")


def available_asr_backends(
    *,
    platform_name: str | None = None,
    machine_name: str | None = None,
) -> list[str]:
    """Backend-ы, которые реально выбираются на этой машине.

    MLX существует только на macOS Apple Silicon. Без фильтра web-клиент
    предлагал его на Linux/Windows-сервере, валидация пропускала, и каждая
    задача падала уже на загрузке backend.
    """
    import platform as platform_module  # noqa: PLC0415
    import sys  # noqa: PLC0415

    system = platform_name if platform_name is not None else sys.platform
    machine = machine_name if machine_name is not None else platform_module.machine()

    backends = ["auto", "onnx", "pytorch"]
    if system == "darwin" and machine == "arm64":
        backends.insert(2, "mlx")
    return backends


@dataclass(frozen=True)
class AsrSelection:
    """Нормализованный ASR runtime для одной задачи."""

    backend: str
    model: str
    onnx_provider: str

    def as_dict(self) -> dict[str, str]:
        return {
            "asr_backend": self.backend,
            "asr_model": self.model,
            "onnx_provider": self.onnx_provider,
        }


def normalize_asr_selection(
    default_loader,
    *,
    backend: str | None = None,
    model: str | None = None,
    onnx_provider: str | None = None,
) -> AsrSelection:
    """Заполнить пустые поля настройками серверного loader и проверить значения."""

    selected_backend = validate_backend_name(backend or default_loader.requested_backend)
    selected_model = validate_asr_model(model or default_loader.requested_model)
    selected_provider = (onnx_provider or default_loader.requested_provider).strip().lower()
    if selected_provider not in ONNX_PROVIDERS:
        raise ValueError(f"Unsupported ONNX provider: {selected_provider}")
    if selected_backend != "onnx":
        # Provider управляет только ONNX Runtime. Без этой нормализации
        # «залипший» в UI провайдер делал бы выбор PyTorch/MLX отличным от
        # серверного loader, и каждая задача грузила бы модель заново.
        selected_provider = "auto"
    return AsrSelection(selected_backend, selected_model, selected_provider)


def acquire_request_model_loader(
    default_loader,
    selection: AsrSelection,
    *,
    loader_factory,
) -> tuple[object, bool]:
    """Переиспользовать default loader либо создать изолированный loader задачи."""

    provider_matters = selection.backend == "onnx"
    matches_default = (
        selection.backend == default_loader.requested_backend
        and selection.model == default_loader.requested_model
        and (
            not provider_matters
            or selection.onnx_provider == default_loader.requested_provider
        )
    )
    if matches_default:
        return default_loader, False
    return (
        loader_factory(
            requested_backend=selection.backend,
            model_name=selection.model,
            model_revision=selection.model,
            onnx_provider=selection.onnx_provider,
        ),
        True,
    )


def build_processor(
    model_loader,
    stats_manager,
    *,
    logger: Callable | None = None,
    progress_callback: Callable | None = None,
    diarization_manager=None,
    diarization_backend: str | None = None,
) -> TranscriptionProcessor:
    return TranscriptionProcessor(
        model_loader,
        stats_manager,
        logger=logger,
        progress_callback=progress_callback,
        diarization_manager=diarization_manager,
        diarization_backend=diarization_backend,
    )


def build_processing_preparation_plan(
    model_loader,
    *,
    enable_diarization: bool,
    diarization_backend: str,
    audio_preprocessing_mode: str,
    hf_token: str | None = None,
    onnx_model_dir: str | None = None,
    diarization_factory=None,
    deepfilter_manager_factory=None,
) -> ModelPreparationPlan:
    """Собрать ленивый план ровно для выбранной пользователем конфигурации."""
    from src.core.diarization.factory import create_diarization_backend  # noqa: PLC0415
    from src.utils.audio_preprocessing import normalize_preprocessing_mode  # noqa: PLC0415
    from src.utils.deepfilter_backend import DeepFilterBinaryManager  # noqa: PLC0415

    steps: list[PreparationStep] = []

    def prepare_asr(report, cancelled):
        if cancelled():
            raise PreparationCancelled("Подготовка ASR отменена")
        missing_resources = getattr(model_loader, "missing_asr_resources", lambda: ())()
        for resource in missing_resources:
            report(PreparationState.DOWNLOADING, message=str(resource))
        report(PreparationState.LOADING, message="Загрузка выбранной ASR-модели")

        def logger(message):
            report(PreparationState.LOADING, message=str(message))

        if not model_loader.load_model(logger=logger):
            raise RuntimeError("Не удалось загрузить выбранную ASR-модель")
        return model_loader

    steps.append(PreparationStep("asr", prepare_asr))

    preprocessing_mode = normalize_preprocessing_mode(audio_preprocessing_mode)
    if preprocessing_mode in {"auto", "denoise"}:

        def prepare_deepfilter(report, cancelled):
            manager_factory = deepfilter_manager_factory or DeepFilterBinaryManager
            manager = manager_factory()
            if manager.is_ready():
                report(PreparationState.LOADING, message="DeepFilterNet найден в кэше", cached=True)
            else:
                report(PreparationState.DOWNLOADING, message="DeepFilterNet")
            try:
                return manager.ensure(
                    progress_callback=lambda completed, total: report(
                        PreparationState.DOWNLOADING,
                        message="DeepFilterNet",
                        completed_bytes=completed,
                        total_bytes=total,
                    ),
                    cancel_check=cancelled,
                )
            except InterruptedError as exc:
                raise PreparationCancelled(str(exc)) from exc

        steps.append(PreparationStep("audio-preprocessing", prepare_deepfilter))

    if enable_diarization:

        def prepare_diarization(report, cancelled):
            from src.config import ONNX_MODEL_DIR, ONNX_PROVIDER  # noqa: PLC0415

            factory = diarization_factory or create_diarization_backend
            manager = factory(
                backend=diarization_backend,
                hf_token=hf_token if hf_token is not None else os.getenv("HF_TOKEN", "").strip() or None,
                device="auto",
                provider=getattr(model_loader, "requested_provider", None) or ONNX_PROVIDER,
                model_dir=onnx_model_dir if onnx_model_dir is not None else ONNX_MODEL_DIR,
            )
            prepare = getattr(manager, "prepare", None)
            if not callable(prepare):
                raise RuntimeError(
                    f"Backend диаризации {diarization_backend!r} не поддерживает подготовку"
                )
            prepared = prepare(report=report, cancel_check=cancelled)
            return manager if prepared is None else prepared

        steps.append(PreparationStep("diarization", prepare_diarization))

    return ModelPreparationPlan(steps)
