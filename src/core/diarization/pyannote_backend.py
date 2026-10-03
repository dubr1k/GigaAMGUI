"""Диаризация через pyannote/speaker-diarization-3.1 (torch + HF-токен)."""

from __future__ import annotations

import inspect
import logging
import os
import warnings
from pathlib import Path

from ...utils.model_cache import hf_repo_is_cached
from ..model_preparation import PreparationCancelled, PreparationState
from .base import SpeakerSegment
from .hf_access import _DIARIZATION_REQUIRED_REPOS, diagnose_hf_access
from .mapping import SpeakerMappingMixin

# Патч pyannote применяется лениво в _load_pipeline (а не при импорте модуля),
# чтобы простой импорт не переписывал pyannote глобально, когда диаризация не
# используется. apply_pyannote_patch идемпотентен.

logger = logging.getLogger(__name__)

_DIARIZATION_MODEL_ID = "pyannote/speaker-diarization-3.1"


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
        from ...utils.pyannote_patch import apply_pyannote_patch
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
