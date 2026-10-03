"""
Модуль диаризации спикеров для GigaAM v3 Transcriber.

Поддерживает:
- pyannote: полная диаризация через pyannote/speaker-diarization-3.1;
- sortformer: NVIDIA Streaming Sortformer 4spk v2.1 через NeMo.
"""

import inspect
import logging
import os
import sys
import threading
import warnings
from contextlib import nullcontext
from pathlib import Path

from ..core.diarization.base import SpeakerSegment
from ..core.diarization.factory import should_use_sortformer_onnx
from ..core.diarization.mapping import (  # noqa: F401 — реэкспорт для старых импортов
    MAX_SPEAKER_SNAP_DISTANCE_SEC,
    MIN_SPEAKER_TURN_SEC,
    UNKNOWN_SPEAKER,
    SpeakerMappingMixin,
)
from ..core.model_preparation import PreparationCancelled, PreparationState
from .model_cache import hf_repo_is_cached

# Патч pyannote применяется лениво в _load_pipeline (а не при импорте модуля),
# чтобы простой импорт src.utils не переписывал pyannote глобально, когда
# диаризация не используется. apply_pyannote_patch идемпотентен.

logger = logging.getLogger(__name__)

# Репозитории, нужные пайплайну pyannote/speaker-diarization-3.1.
_DIARIZATION_REQUIRED_REPOS = [
    "pyannote/speaker-diarization-3.1",
    "pyannote/segmentation-3.0",
    "pyannote/wespeaker-voxceleb-resnet34-LM",
]

_DIARIZATION_MODEL_ID = "pyannote/speaker-diarization-3.1"
_SORTFORMER_MODEL_ID = "nvidia/diar_streaming_sortformer_4spk-v2.1"

DIARIZATION_BACKENDS = ("pyannote", "sortformer", "onnx")
_DIARIZATION_BACKEND_ALIASES = {
    "pyannote": "pyannote",
    "sortformer": "sortformer",
    "nvidia": "sortformer",
    "onnx": "onnx",
}


def normalize_diarization_backend(backend: str | None) -> str:
    """Возвращает каноническое имя backend диаризации."""
    normalized = str(backend or "pyannote").strip().lower()
    try:
        return _DIARIZATION_BACKEND_ALIASES[normalized]
    except KeyError as exc:
        supported = ", ".join(DIARIZATION_BACKENDS)
        raise ValueError(
            f"Неизвестный backend диаризации: {backend!r}. Доступно: {supported}"
        ) from exc


def _annotation_from_pipeline_output(output):
    """pyannote.core.Annotation из результата pipeline любой версии.

    pyannote.audio 2.x–3.x возвращает Annotation. В 4.x ``apply`` отдаёт
    ``DiarizeOutput`` (если pipeline не собран с ``legacy=True``, а конфиг 3.1
    этого не задаёт): ``speaker_diarization`` с наложениями речи и
    ``exclusive_speaker_diarization`` — не больше одного говорящего в каждый
    момент. Берём exclusive: маппинг назначает каждому слову одного говорящего,
    а на наложениях обычной разметки выбор зависел бы от порядка сегментов
    (``_find_speaker_at_time`` вернул бы того, кто начал раньше, — как правило,
    перебитого). Ровно для сведения со словами STT pyannote её и строит.
    """
    if hasattr(output, "itertracks"):
        return output
    for attribute in ("exclusive_speaker_diarization", "speaker_diarization"):
        annotation = getattr(output, attribute, None)
        if annotation is not None and hasattr(annotation, "itertracks"):
            return annotation
    raise ValueError(
        f"Неожиданный тип результата диаризации: {type(output).__name__} "
        "(ожидался pyannote.core.Annotation с методом itertracks "
        "или DiarizeOutput pyannote.audio 4.x)"
    )


def diagnose_hf_access(token: str | None) -> str:
    """Дополняет ошибку pyannote проверкой доступа к нужным репозиториям.

    Пробует каждый нужный репозиторий через HfApi и возвращает человекочитаемый
    отчёт: валиден ли токен и к какому именно репозиторию нет доступа и почему
    (не приняты условия / fine-grained токен без доступа к gated-репам / 401 / сеть).
    Никогда не бросает исключение — только возвращает строку.
    """
    if not token:
        return "HF-токен не задан. Укажите read-токен: https://huggingface.co/settings/tokens"

    try:
        from huggingface_hub import HfApi
        from huggingface_hub.utils import (
            GatedRepoError,
            HfHubHTTPError,
            RepositoryNotFoundError,
        )
    except Exception as e:  # noqa: BLE001
        return f"Не удалось выполнить диагностику доступа (huggingface_hub): {e}"

    api = HfApi()
    lines: list[str] = []

    # 1) Валиден ли сам токен.
    try:
        who = api.whoami(token=token)
        name = who.get("name") if isinstance(who, dict) else None
        lines.append(f"Токен валиден (пользователь: {name or '?'}).")
    except Exception as e:  # noqa: BLE001
        code = getattr(getattr(e, "response", None), "status_code", None)
        details = f"{type(e).__name__}: {e}"
        if code is not None:
            details = f"HTTP {code}; {details}"
        return (
            f"Токен НЕвалиден или не даёт доступа (whoami: {details}). "
            "Создайте новый read-токен: https://huggingface.co/settings/tokens"
        )

    # 2) Доступ к каждому нужному репозиторию.
    for repo in _DIARIZATION_REQUIRED_REPOS:
        try:
            api.model_info(repo, token=token)
            lines.append(f"  OK  {repo}")
        except GatedRepoError:
            lines.append(
                f"  НЕТ {repo}: не приняты условия — откройте "
                f"https://huggingface.co/{repo} и нажмите 'Agree and access repository'."
            )
        except RepositoryNotFoundError:
            lines.append(
                f"  НЕТ {repo}: репозиторий не виден токену. Если токен fine-grained, "
                "включите 'Read access to contents of all public gated repos you can access'."
            )
        except HfHubHTTPError as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code == 403:
                lines.append(
                    f"  НЕТ {repo}: 403 — примите условия и/или дайте токену доступ к "
                    "gated-репозиториям (fine-grained токен: 'Read access to public gated repos')."
                )
            elif code == 401:
                lines.append(f"  НЕТ {repo}: 401 — токен недействителен.")
            else:
                lines.append(f"  НЕТ {repo}: HTTP {code}: {e}")
        except Exception as e:  # noqa: BLE001
            lines.append(f"  НЕТ {repo}: {type(e).__name__}: {e}")

    return "\n".join(lines)


class DiarizationManager(SpeakerMappingMixin):
    """Менеджер диаризации спикеров."""

    def __init__(
        self,
        hf_token: str | None = None,
        device: str = "auto",
        min_speakers: int | None = None,
        max_speakers: int | None = None,
    ):
        """
        Инициализация менеджера диаризации.

        Args:
            hf_token: HuggingFace токен для доступа к pyannote моделям
            device: Устройство ("auto", "cuda", "cpu")
            min_speakers: Минимальное количество спикеров
            max_speakers: Максимальное количество спикеров
        """
        self.backend = "pyannote"
        self.hf_token = hf_token or os.getenv("HF_TOKEN")
        self.device = self._resolve_device(device)
        self.min_speakers = min_speakers
        self.max_speakers = max_speakers

        self._pipeline = None

    def _resolve_device(self, device: str) -> str:
        """Определение устройства: CUDA > MPS (Apple Silicon) > CPU."""
        if device == "auto":
            try:
                import torch
                if torch.cuda.is_available():
                    return "cuda"
                elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                    return "mps"
                else:
                    return "cpu"
            except ImportError:
                return "cpu"
        return device

    @property
    def pipeline(self):
        """Ленивая загрузка pipeline диаризации."""
        if self._pipeline is None:
            self._pipeline = self._load_pipeline()
        return self._pipeline

    def _load_pipeline(self):
        """Загрузка pyannote pipeline."""
        if not self.hf_token:
            raise ValueError(
                "HF_TOKEN не установлен. "
                "Установите токен в .env файле для использования диаризации."
            )

        # Применяем патч еще раз перед импортом pyannote
        from .pyannote_patch import apply_pyannote_patch
        apply_pyannote_patch()

        try:
            import torch
            from pyannote.audio import Pipeline
        except ImportError as e:
            raise ImportError(
                "pyannote.audio не установлен. "
                "Установите: pip install pyannote.audio"
            ) from e

        # pyannote.audio 3.1 не передаёт use_auth_token в загрузчик ONNX-модели
        # WeSpeaker. huggingface_hub при этом берёт токен из HF_TOKEN, поэтому
        # синхронизируем окружение с токеном менеджера перед любыми загрузками.
        os.environ["HF_TOKEN"] = self.hf_token

        pipeline = None
        load_error = None
        try:
            logger.info("Попытка загрузки модели диаризации: %s", _DIARIZATION_MODEL_ID)
            parameters = inspect.signature(Pipeline.from_pretrained).parameters
            token_parameter = "token" if "token" in parameters else "use_auth_token"
            pipeline = Pipeline.from_pretrained(
                _DIARIZATION_MODEL_ID,
                **{token_parameter: self.hf_token},
            )
        except Exception as e:  # noqa: BLE001
            # Не трактуем внутренний TypeError как несовместимость API: сигнатура
            # уже определена выше, поэтому это настоящая ошибка загрузки.
            load_error = e
            logger.warning(
                "Ошибка при загрузке %s: %s: %s",
                _DIARIZATION_MODEL_ID,
                type(e).__name__,
                e,
                exc_info=True,
            )

        if pipeline is None:
            diagnosis = diagnose_hf_access(self.hf_token)
            if load_error is None:
                base = f"{_DIARIZATION_MODEL_ID}: from_pretrained вернул None"
            else:
                base = f"{type(load_error).__name__}: {load_error}"
            raise ValueError(
                "Не удалось загрузить модель диаризации.\n"
                f"Причина: {base}\n\n"
                "Диагностика доступа HuggingFace:\n"
                f"{diagnosis}\n\n"
                "Если все репозитории отмечены OK, причина не в правах токена — "
                "ориентируйтесь на исходную ошибку загрузки выше."
            )

        logger.info("Модель %s загружена успешно", _DIARIZATION_MODEL_ID)

        # Перемещение на устройство
        try:
            import torch
            device = torch.device(self.device)
            pipeline = pipeline.to(device)
        except Exception as e:
            logger.warning(f"Не удалось переместить pipeline на {self.device}: {e}")

        return pipeline

    def prepare(self, report=None, cancel_check=None):
        """Скачать недостающие веса и загрузить pyannote до первого файла."""
        emit = report or (lambda _state, **_kwargs: None)
        cancelled = cancel_check or (lambda: False)
        if cancelled():
            raise PreparationCancelled("Подготовка Pyannote отменена")
        missing = [repo for repo in _DIARIZATION_REQUIRED_REPOS if not hf_repo_is_cached(repo)]
        if missing:
            emit(
                PreparationState.DOWNLOADING,
                message="Недостающие модели Pyannote: " + ", ".join(missing),
            )
        emit(PreparationState.LOADING, message=_DIARIZATION_MODEL_ID)
        _ = self.pipeline
        if cancelled():
            raise PreparationCancelled("Подготовка Pyannote отменена")
        return self

    def diarize(
        self,
        audio_path: Path | str,
        num_speakers: int | None = None,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
        progress_callback=None,
    ) -> list[SpeakerSegment]:
        """
        Выполнить диаризацию аудио файла.

        Args:
            audio_path: Путь к аудио файлу (должен быть WAV, 16kHz, mono)
            num_speakers: Точное количество спикеров (если известно)
            min_speakers: Минимальное количество спикеров
            max_speakers: Максимальное количество спикеров

        Returns:
            Список сегментов с информацией о спикерах
        """
        audio_path = Path(audio_path)

        # Использование параметров по умолчанию
        min_speakers = min_speakers or self.min_speakers
        max_speakers = max_speakers or self.max_speakers

        try:
            # Подготовка параметров
            kwargs = {}
            if num_speakers is not None:
                kwargs["num_speakers"] = num_speakers
            else:
                if min_speakers is not None:
                    kwargs["min_speakers"] = min_speakers
                if max_speakers is not None:
                    kwargs["max_speakers"] = max_speakers

            logger.info(f"Запуск диаризации для {audio_path.name} с параметрами: {kwargs}")

            # Запуск диаризации
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                diarization = self._run_pipeline(str(audio_path), kwargs, progress_callback=progress_callback)

            # Преобразование результатов
            segments = []
            diarization = _annotation_from_pipeline_output(diarization)
            for turn, _, speaker in diarization.itertracks(yield_label=True):
                segments.append(SpeakerSegment(
                    start=turn.start,
                    end=turn.end,
                    speaker=speaker
                ))

            # Сортировка по времени
            segments.sort(key=lambda s: s.start)

            # Переименование спикеров в человекочитаемый формат
            segments = self._rename_speakers(segments)

            logger.info(f"Диаризация завершена. Найдено спикеров: {len(set(s.speaker for s in segments))}")

            return segments

        except Exception as e:
            logger.error(f"Ошибка при диаризации: {e}")
            raise ValueError(f"Ошибка при диаризации: {e}") from e

    def _run_pipeline(
        self,
        file_path: str,
        kwargs: dict,
        progress_callback=None,
    ):
        """Запускает pyannote pipeline с hook-поддержкой если доступна."""
        pipeline = self.pipeline
        if not self._supports_hook(pipeline):
            return pipeline(file_path, **kwargs)

        def _hook(
            _step_name,
            _step_artifact,
            file=None,
            total=None,
            completed=None,
        ):
            if progress_callback is None:
                return

            if completed is None or not isinstance(completed, (int, float)):
                return

            # `completed/total` считает элементы текущего внутреннего шага
            # pyannote (окна сегментации, батчи эмбеддингов), а не секунды и не
            # весь pipeline. Процессор трактует 2-й и 3-й аргументы как секунды
            # записи, поэтому единицы шага не передаём: стадия неопределённая,
            # а вызов служит признаком того, что работа идёт.
            progress_callback(None, None, None)

        try:
            return pipeline(file_path, hook=_hook, **kwargs)
        except TypeError as exc:
            if "hook" not in str(exc):
                raise
            return pipeline(file_path, **kwargs)

    @staticmethod
    def _supports_hook(pipeline) -> bool:
        for callable_obj in (pipeline, getattr(pipeline, "apply", None)):
            if callable_obj is None:
                continue
            try:
                signature = inspect.signature(callable_obj)
            except (TypeError, ValueError):
                continue
            if "hook" in signature.parameters:
                return True
        return False

    def unload(self) -> None:
        """Освободить ссылку экземпляра на legacy pipeline."""
        self._pipeline = None


class SortformerDiarizationManager(DiarizationManager):
    """Диаризация через NVIDIA Streaming Sortformer 4spk v2.1.

    NeMo импортируется только при первом запуске backend: базовая установка и
    PyInstaller-сборки с pyannote от него не зависят. Конфигурация повторяет
    официальный high-latency preset model card v2.1: длинный chunk даёт более ровные
    границы ценой задержки, которая для офлайн-транскрибации несущественна.
    """

    max_supported_speakers = 4
    _shared_pipelines = {}
    _shared_load_lock = threading.Lock()
    _shared_inference_lock = threading.Lock()
    _shared_inference_contexts = {}

    def __init__(self, device: str = "auto"):
        self.backend = "sortformer"
        self.hf_token = None
        self.device = self._resolve_sortformer_device(device)
        self.min_speakers = None
        self.max_speakers = self.max_supported_speakers
        self._pipeline = None
        self._inference_lock = self._shared_inference_lock
        self._inference_context = nullcontext
        self.last_fallback_reason: str | None = None

    @property
    def pipeline(self):
        """Переиспользует одну тяжёлую модель NeMo между processor-задачами."""
        if self._pipeline is not None:
            return self._pipeline
        with self._shared_load_lock:
            cls = type(self)
            if self.device not in cls._shared_pipelines:
                cls._shared_pipelines[self.device] = self._load_pipeline()
            self._pipeline = cls._shared_pipelines[self.device]
            self._inference_context = cls._shared_inference_contexts[self.device]
        return self._pipeline

    @staticmethod
    def _resolve_sortformer_device(device: str) -> str:
        """Выбрать CUDA/MPS/CPU без неявного переноса MPS на CPU."""
        if device not in {"auto", "cuda", "cpu", "mps"}:
            raise ValueError(f"Неподдерживаемое устройство Sortformer: {device}")
        if device == "cpu":
            return "cpu"
        try:
            import torch

            if device in {"auto", "cuda"} and torch.cuda.is_available():
                return "cuda"
            mps = getattr(getattr(torch, "backends", None), "mps", None)
            if device in {"auto", "mps"} and mps is not None and mps.is_available():
                return "mps"
        except ImportError:
            pass
        return "cpu"

    def _fallback_to_cpu(self) -> None:
        """Сбросить нерабочий MPS pipeline и лениво загрузить отдельный CPU."""
        cls = type(self)
        failed_pipeline = self._pipeline
        with self._shared_load_lock:
            if cls._shared_pipelines.get("mps") is failed_pipeline:
                cls._shared_pipelines.pop("mps", None)
                cls._shared_inference_contexts.pop("mps", None)
            self._pipeline = None
            self._inference_context = nullcontext
            self.device = "cpu"
        _ = self.pipeline

    def unload(self) -> None:
        """Выгрузить модель NeMo, включая общий для класса кэш.

        Базовый ``unload`` обнуляет только ``self._pipeline``, а модель живёт в
        ``cls._shared_pipelines`` и переживала выгрузку до конца процесса. На
        CUDA это удержанная VRAM на карте фиксированного объёма, поэтому кэш
        нужно чистить именно по ключу устройства.
        """
        cls = type(self)
        with self._shared_load_lock:
            cls._shared_pipelines.pop(self.device, None)
            cls._shared_inference_contexts.pop(self.device, None)
            self._pipeline = None
            self._inference_context = nullcontext
        self._empty_accelerator_cache()

    def _empty_accelerator_cache(self) -> None:
        """Вернуть драйверу VRAM/unified memory, освободившуюся после выгрузки."""
        try:
            import torch

            if self.device == "cuda" and torch.cuda.is_available():
                torch.cuda.empty_cache()
            elif self.device == "mps" and hasattr(torch, "mps"):
                torch.mps.empty_cache()
        except Exception:
            pass

    def _run_sortformer(self, audio_path: Path):
        with self._inference_lock:
            pipeline = self.pipeline
            with self._inference_context():
                return pipeline.diarize(
                    audio=[str(audio_path)],
                    override_config=self._diarize_config(),
                )

    def _load_pipeline(self):
        try:
            import torch
            from nemo.collections.asr.models import SortformerEncLabelModel
        except ImportError as exc:
            raise ImportError(
                "NVIDIA Sortformer не установлен. Установите опциональные "
                "зависимости: pip install -r requirements-sortformer.txt"
            ) from exc

        logger.info("Загрузка модели диаризации: %s", _SORTFORMER_MODEL_ID)
        model = SortformerEncLabelModel.from_pretrained(_SORTFORMER_MODEL_ID)
        model.eval()
        model.to(torch.device(self.device))
        self._inference_context = getattr(torch, "inference_mode", nullcontext)
        type(self)._shared_inference_contexts[self.device] = self._inference_context

        modules = model.sortformer_modules
        modules.chunk_len = 340
        modules.chunk_right_context = 40
        modules.fifo_len = 40
        modules.spkcache_update_period = 300
        modules.spkcache_len = 188
        modules._check_streaming_parameters()
        logger.info("Модель %s загружена на %s", _SORTFORMER_MODEL_ID, self.device)
        return model

    def prepare(self, report=None, cancel_check=None):
        """Скачать и загрузить NeMo Sortformer до обработки первого файла."""
        emit = report or (lambda _state, **_kwargs: None)
        cancelled = cancel_check or (lambda: False)
        if cancelled():
            raise PreparationCancelled("Подготовка Sortformer отменена")
        cached = hf_repo_is_cached(_SORTFORMER_MODEL_ID)
        if not cached:
            emit(
                PreparationState.DOWNLOADING,
                message=f"Sortformer NeMo ({_SORTFORMER_MODEL_ID})",
            )
        emit(
            PreparationState.LOADING,
            message=f"Sortformer NeMo (device={self.device})",
            cached=cached,
        )
        _ = self.pipeline
        if cancelled():
            raise PreparationCancelled("Подготовка Sortformer отменена")
        return self

    @staticmethod
    def _diarize_config():
        """NeMo-recommended post-processing for streaming Sortformer v2.1."""

        from nemo.collections.asr.parts.mixins.diarization import DiarizeConfig
        from nemo.collections.asr.parts.utils.vad_utils import PostProcessingParams
        from omegaconf import OmegaConf

        params = OmegaConf.structured(PostProcessingParams())
        params.onset = 0.64
        params.offset = 0.74
        params.pad_onset = 0.06
        params.pad_offset = 0.0
        params.min_duration_on = 0.1
        params.min_duration_off = 0.15
        return DiarizeConfig(
            batch_size=1,
            num_workers=0,
            verbose=False,
            postprocessing_params=params,
        )

    def diarize(
        self,
        audio_path: Path | str,
        num_speakers: int | None = None,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
        progress_callback=None,
    ) -> list[SpeakerSegment]:
        """Запускает Sortformer и приводит вывод NeMo к SpeakerSegment."""
        del min_speakers, max_speakers
        if num_speakers is not None and not 1 <= num_speakers <= self.max_supported_speakers:
            raise ValueError("Sortformer поддерживает не более 4 спикеров")
        if num_speakers is not None:
            logger.warning(
                "Sortformer сам определяет активных спикеров; num_speakers=%s игнорируется",
                num_speakers,
            )

        audio_path = Path(audio_path)
        try:
            try:
                predicted = self._run_sortformer(audio_path)
            except Exception as exc:
                if self.device != "mps":
                    raise
                self.last_fallback_reason = f"MPS: {exc}; использован CPU"
                logger.warning(
                    "Sortformer не выполнился на MPS (%s); повторяем на CPU",
                    exc,
                )
                self._fallback_to_cpu()
                predicted = self._run_sortformer(audio_path)
            if not predicted or not isinstance(predicted, (list, tuple)):
                raise ValueError("Sortformer не вернул результаты")
            raw_segments = predicted[0]
            if isinstance(raw_segments, str):
                raw_segments = [raw_segments]

            segments = [self._parse_sortformer_segment(item) for item in raw_segments]
            segments.sort(key=lambda segment: segment.start)
            segments = self._rename_speakers(segments)
            if progress_callback is not None:
                progress_callback(1.0, None, None)
            logger.info(
                "Диаризация Sortformer завершена. Найдено спикеров: %s",
                len({segment.speaker for segment in segments}),
            )
            return segments
        except Exception as exc:
            logger.error("Ошибка Sortformer: %s", exc)
            raise ValueError(f"Ошибка при диаризации Sortformer: {exc}") from exc

    @staticmethod
    def _parse_sortformer_segment(item) -> SpeakerSegment:
        if isinstance(item, str):
            parts = item.strip().split()
            if len(parts) != 3:
                raise ValueError(f"Неожиданный сегмент Sortformer: {item!r}")
            start, end, speaker = parts
        elif isinstance(item, dict):
            start = item.get("start")
            end = item.get("end")
            speaker = item.get("speaker", item.get("speaker_id"))
        elif isinstance(item, (list, tuple)) and len(item) == 3:
            start, end, speaker = item
        else:
            raise ValueError(f"Неожиданный сегмент Sortformer: {item!r}")

        try:
            start_value = float(start)
            end_value = float(end)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Неожиданный сегмент Sortformer: {item!r}") from exc
        if not speaker or start_value < 0 or end_value <= start_value:
            raise ValueError(f"Неожиданный сегмент Sortformer: {item!r}")
        return SpeakerSegment(start_value, end_value, str(speaker))


def get_diarization_manager(
    hf_token: str | None = None,
    device: str = "auto",
    backend: str = "pyannote",
    **kwargs
) -> DiarizationManager:
    """
    Получить менеджер диаризации.

    Args:
        hf_token: HuggingFace токен
        device: Устройство
        **kwargs: Дополнительные параметры

    Returns:
        Экземпляр DiarizationManager
    """
    backend = normalize_diarization_backend(backend)
    if backend == "onnx":
        from ..core.diarization.onnx_backend import OnnxDiarizationBackend

        return OnnxDiarizationBackend(
            provider=kwargs.pop("provider", "auto"),
            model_dir=kwargs.pop("model_dir", None),
        )
    if backend == "sortformer":
        if should_use_sortformer_onnx(
            platform_name=sys.platform,
            nemo_available=kwargs.pop("nemo_available", None),
        ):
            from ..core.diarization.sortformer_onnx import SortformerOnnxDiarizationManager

            return SortformerOnnxDiarizationManager(
                device=device,
                provider=kwargs.pop("provider", "auto"),
                model_dir=kwargs.pop("model_dir", None),
            )
        return SortformerDiarizationManager(device=device)
    return DiarizationManager(
        hf_token=hf_token,
        device=device,
        **kwargs
    )
