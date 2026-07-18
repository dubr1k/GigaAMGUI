"""Выбор ONNX Runtime execution provider без тяжёлых импортов при старте."""

from __future__ import annotations

import sys
from collections.abc import Iterable
from dataclasses import dataclass

_PROVIDER_NAMES = {
    "cpu": "CPUExecutionProvider",
    "cuda": "CUDAExecutionProvider",
    "tensorrt": "TensorrtExecutionProvider",
    "coreml": "CoreMLExecutionProvider",
    "directml": "DmlExecutionProvider",
}

_COREML_OPTIONS = {
    "ModelFormat": "MLProgram",
    "MLComputeUnits": "ALL",
    # RNNT decoder/joiner have dynamic inputs. CoreML may claim those
    # subgraphs and then fail at prediction time; static-only delegation keeps
    # unsupported dynamic nodes on the following CPU provider.
    "RequireStaticInputShapes": "1",
}


@dataclass(frozen=True)
class ProviderSelection:
    """Запрошенный provider и фактическая упорядоченная цепочка ORT."""

    requested: str
    active: str
    providers: tuple[str, ...]
    fallback_reason: str | None = None


def onnx_session_providers(selection: ProviderSelection) -> list[object]:
    """Build ORT provider specs, including safe CoreML options."""

    return [
        (provider, dict(_COREML_OPTIONS))
        if provider == "CoreMLExecutionProvider"
        else provider
        for provider in selection.providers
    ]


def _auto_priority(platform_name: str) -> tuple[str, ...]:
    if platform_name == "darwin":
        return ("coreml", "cpu")
    if platform_name == "win32":
        return ("directml", "cuda", "cpu")
    return ("cuda", "cpu")


def resolve_onnx_providers(
    requested: str,
    *,
    available: Iterable[str],
    platform_name: str = sys.platform,
) -> ProviderSelection:
    """Проверить настройку и построить provider chain для ONNX Runtime."""

    normalized = (requested or "auto").strip().lower() or "auto"
    if normalized not in {"auto", *_PROVIDER_NAMES}:
        raise ValueError(f"Unsupported ONNX provider: {normalized}")

    available_names = tuple(dict.fromkeys(str(name) for name in available))
    available_set = set(available_names)

    if normalized != "auto":
        provider_name = _PROVIDER_NAMES[normalized]
        if provider_name not in available_set:
            raise RuntimeError(
                f"Запрошенный ONNX provider {provider_name} недоступен; "
                f"доступны: {', '.join(available_names) or 'нет'}"
            )
        return ProviderSelection(
            requested=normalized,
            active=normalized,
            providers=(provider_name,),
        )

    priority = _auto_priority(platform_name)
    providers = tuple(
        _PROVIDER_NAMES[alias]
        for alias in priority
        if _PROVIDER_NAMES[alias] in available_set
    )
    if not providers:
        raise RuntimeError("Среди установленных ONNX Runtime providers не найдено поддерживаемых")

    active = next(alias for alias in priority if _PROVIDER_NAMES[alias] == providers[0])
    fallback_reason = None
    if active == "cpu":
        fallback_reason = (
            "Ускоренный ONNX provider недоступен; использован CPUExecutionProvider"
        )
    return ProviderSelection(
        requested="auto",
        active=active,
        providers=providers,
        fallback_reason=fallback_reason,
    )


def available_onnx_providers() -> tuple[str, ...]:
    """Лениво получить providers, доступные в установленном ONNX Runtime."""

    import onnxruntime  # noqa: PLC0415

    return tuple(onnxruntime.get_available_providers())
