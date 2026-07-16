"""Модуль загрузки и управления моделью GigaAM."""


from ..config import (
    ASR_ALLOW_FALLBACK,
    ASR_BACKEND,
    ASR_MODEL,
    MLX_MODEL_REPO,
)
from .asr.factory import create_backend_from_config
from .asr.models import validate_asr_model
from .asr.pytorch_backend import PyTorchBackend
from .asr.types import ProgressCallback


class ModelLoader:
    """Класс для загрузки и управления моделью GigaAM."""

    def __init__(
        self,
        requested_backend: str | None = None,
        *,
        allow_fallback: bool | None = None,
        model_name: str | None = None,
        model_revision: str | None = None,
        mlx_model_repo: str | None = None,
    ):
        self.model = None
        self.device = None
        self._backend = None
        self._requested_backend = (requested_backend or ASR_BACKEND).strip().lower() or ASR_BACKEND
        self._allow_fallback = ASR_ALLOW_FALLBACK if allow_fallback is None else bool(allow_fallback)
        self._model_name = model_name or ASR_MODEL
        self._model_revision = validate_asr_model(model_revision or ASR_MODEL)
        self._mlx_model_repo = mlx_model_repo or MLX_MODEL_REPO
        self._fallback_reason = None
        self._factory_error: str | None = None

    @property
    def requested_backend(self) -> str:
        return self._requested_backend

    @property
    def active_backend(self) -> str:
        return self.backend_name

    @property
    def backend_name(self) -> str:
        return self._backend.name if self._backend is not None else self._requested_backend

    def _apply_requested_backend(self, requested_backend: str) -> None:
        requested = requested_backend.strip().lower()
        if not requested:
            requested = ASR_BACKEND
        if requested == self._requested_backend and self._backend is not None:
            return
        self._requested_backend = requested
        self.unload()
        self._fallback_reason = None
        self._factory_error = None
        self._backend = None

    def _ensure_backend(self) -> None:
        if self._backend is not None:
            return
        backend, fallback_reason = create_backend_from_config(
            requested_backend=self._requested_backend,
            model_name=self._model_name,
            model_revision=self._model_revision,
            mlx_model_repo=self._mlx_model_repo,
            allow_fallback=self._allow_fallback,
        )
        self._backend = backend
        self._fallback_reason = fallback_reason

    def _sync_from_backend(self) -> None:
        if self._backend is None:
            self.model = None
            self.device = None
            return

        if hasattr(self._backend, "model"):
            self.model = self._backend.model
        if hasattr(self._backend, "device"):
            self.device = self._backend.device

    def _switch_to_pytorch_fallback(self) -> bool:
        self._backend = PyTorchBackend(model=self._model_name, revision=self._model_revision)
        self._fallback_reason = "Аварийный fallback: MLX не загрузился, использован PyTorch backend"
        self._factory_error = None
        return self._backend.load()

    def _load_with_fallback(self, logger=None) -> bool:
        if self._backend is None or self._backend.is_loaded():
            return True

        if self._backend.load(logger=logger):
            self._sync_from_backend()
            return True

        if (
            self._requested_backend == "auto"
            and self._backend.name == "mlx"
            and self._allow_fallback
        ):
            self._factory_error = "MLX не загрузился"
            if self._backend is not None:
                try:
                    self._backend.unload()
                except Exception:
                    pass
            try:
                if self._switch_to_pytorch_fallback():
                    self._sync_from_backend()
                    return True
            except Exception as exc:
                self._factory_error = f"PyTorch fallback failed: {exc}"

        return False

    def load_model(self, logger=None):
        """Загружает выбранный backend ASR."""
        if self._backend is not None and self._backend.is_loaded():
            self._sync_from_backend()
            return True

        if logger:
            logger(f"Запрошен ASR backend: {self._requested_backend}")

        try:
            self._ensure_backend()
        except Exception as exc:
            if logger:
                logger(f"Ошибка инициализации backend: {exc}")
            self._factory_error = f"Backend create failed: {exc}"
            return False

        if self._backend is None:
            if logger:
                logger("Не удалось создать backend для загрузки модели")
            self._fallback_reason = None
            return False

        if self._load_with_fallback(logger=logger):
            self._factory_error = None
            return True

        if logger:
            logger(f"Не удалось загрузить backend {self._backend.name}")
        self._factory_error = (
            f"Не удалось загрузить backend {self._backend.name if self._backend else 'неизвестный'}"
        )
        return False

    @property
    def _bundle_download_root(self) -> str | None:
        if self._backend is None:
            return None
        return getattr(self._backend, "_bundled_download_root", lambda: None)()

    def _empty_cache(self):
        """Освобождает кэш ускорителя."""
        if self._backend is None:
            if self.device in {"cuda", "mps"}:
                try:
                    import torch

                    if self.device == "cuda" and torch.cuda.is_available():
                        torch.cuda.empty_cache()
                    elif self.device == "mps" and hasattr(torch, "mps"):
                        torch.mps.empty_cache()
                except Exception:
                    pass
            return

        if self._backend and hasattr(self._backend, "_empty_cache"):
            try:
                self._backend._empty_cache()  # noqa: SLF001
            except Exception:
                pass

    def transcribe_longform(
        self,
        audio_path: str,
        progress_callback: ProgressCallback | None = None,
    ):
        """Транскрибирует длинное аудио через выбранную стратегию сегментации."""
        if self._backend is None:
            raise RuntimeError("Модель не загружена")

        if not self._backend.is_loaded():
            raise RuntimeError("Модель не загружена")

        return self._backend.transcribe_longform(audio_path, progress_callback=progress_callback)

    def unload(self):
        """Выгружает модель и освобождает память."""
        if self._backend is not None:
            try:
                self._backend.unload()
            except Exception:
                pass
        self.model = None
        self.device = None
        self._backend = None
        self._fallback_reason = None
        self._factory_error = None

    def is_loaded(self) -> bool:
        """Проверяет, загружена ли модель."""
        if self._backend is not None:
            return self._backend.is_loaded()
        return self.model is not None

    def diagnostics(self) -> dict[str, object]:
        model = self._model_name
        device = "N/A"
        active = self.backend_name
        segmentation_mode = None
        segmentation_fallback_reason = None

        if self._backend is not None:
            try:
                capabilities = self._backend.capabilities()
                model = capabilities.model
                device = capabilities.device
                segmentation_mode = getattr(capabilities, "segmentation_mode", None)
                segmentation_fallback_reason = getattr(
                    capabilities,
                    "segmentation_fallback_reason",
                    None,
                )
            except Exception:
                pass

        return {
            "requested_backend": self._requested_backend,
            "active_backend": active,
            "fallback_reason": self._fallback_reason,
            "model": model,
            "device": device or "N/A",
            "segmentation_mode": segmentation_mode,
            "segmentation_fallback_reason": segmentation_fallback_reason,
            "cache_root": self._bundle_download_root,
            "repo": self._mlx_model_repo,
            "loader_loaded": self.is_loaded(),
            "error": self._factory_error,
        }

    @property
    def requested_model(self) -> str:
        return self._model_revision

    def configure_model(self, model_revision: str) -> None:
        """Select an ASR model for the next load."""
        selected = validate_asr_model(model_revision)
        if selected != self._model_revision:
            self._model_revision = selected
            self.unload()

    def configure_backend(self, requested_backend: str | None = None) -> None:
        """Переконфигурировать backend перед следующей загрузкой."""
        requested = (requested_backend or ASR_BACKEND).strip().lower() or ASR_BACKEND
        self._apply_requested_backend(requested)
