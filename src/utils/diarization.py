"""
Модуль диаризации спикеров для GigaAM v3 Transcriber.

Поддерживает:
- pyannote: Полная диаризация через pyannote/speaker-diarization-3.1
"""

import inspect
import logging
import os
import warnings
from pathlib import Path

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


def diagnose_hf_access(token: str | None) -> str:
    """Точно определяет, почему pyannote from_pretrained вернул None.

    Пробует каждый нужный репозиторий через HfApi и возвращает человекочитаемый
    отчёт: валиден ли токен и к какому именно репозиторию нет доступа и почему
    (не приняты условия / fine-grained токен без доступа к gated-репам / 401 / сеть).
    Никогда не бросает исключение — только возвращает строку.
    """
    try:
        from huggingface_hub import HfApi
        from huggingface_hub.utils import (
            GatedRepoError,
            HfHubHTTPError,
            RepositoryNotFoundError,
        )
    except Exception as e:  # noqa: BLE001
        return f"Не удалось выполнить диагностику доступа (huggingface_hub): {e}"

    if not token:
        return "HF-токен не задан. Укажите read-токен: https://huggingface.co/settings/tokens"

    api = HfApi()
    lines: list[str] = []

    # 1) Валиден ли сам токен.
    try:
        who = api.whoami(token=token)
        name = who.get("name") if isinstance(who, dict) else None
        lines.append(f"Токен валиден (пользователь: {name or '?'}).")
    except Exception as e:  # noqa: BLE001
        return (
            "Токен НЕвалиден или не даёт доступа (whoami: "
            f"{type(e).__name__}). Создайте новый read-токен: "
            "https://huggingface.co/settings/tokens"
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


class SpeakerSegment:
    """Сегмент с информацией о спикере"""

    def __init__(self, start: float, end: float, speaker: str):
        self.start = start
        self.end = end
        self.speaker = speaker

    @property
    def duration(self) -> float:
        return self.end - self.start


class DiarizationManager:
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

        # Устанавливаем токен для huggingface_hub
        try:
            from huggingface_hub import login
            # Пробуем логин, если токен не установлен в окружении
            if not os.getenv("HF_TOKEN"):
                login(token=self.hf_token, add_to_git_credential=False)
        except Exception as e:
            logger.debug(f"Не удалось установить токен через huggingface_hub: {e}")

        # Список моделей для попытки загрузки
        models_to_try = [
            "pyannote/speaker-diarization-3.1",
            "pyannote/speaker-diarization",
        ]

        last_error = None
        pipeline = None

        for model_id in models_to_try:
            try:
                logger.info(f"Попытка загрузки модели диаризации: {model_id}")
                # В новых версиях pyannote.audio используется 'token' вместо 'use_auth_token'
                try:
                    pipeline = Pipeline.from_pretrained(
                        model_id,
                        token=self.hf_token
                    )
                except TypeError:
                    # Fallback для старых версий
                    pipeline = Pipeline.from_pretrained(
                        model_id,
                        use_auth_token=self.hf_token
                    )
                # ВАЖНО: from_pretrained НЕ бросает исключение, а возвращает None,
                # если не приняты условия ЗАВИСИМЫХ моделей (segmentation-3.0 /
                # модель эмбеддингов) или у токена нет прав read. Раньше это
                # логировалось как «успех» и молча приводило к «1 спикеру».
                if pipeline is None:
                    last_error = RuntimeError(
                        f"{model_id}: from_pretrained вернул None — не приняты условия "
                        f"зависимых моделей (segmentation-3.0 / эмбеддинги) либо у токена нет прав read"
                    )
                    logger.warning(str(last_error))
                    continue
                logger.info(f"Модель {model_id} загружена успешно")
                break
            except Exception as e:
                last_error = e
                error_str = str(e)

                # Проверяем на ошибку доступа
                if "403" in error_str or "gated" in error_str.lower() or "authorized" in error_str.lower():
                    logger.warning(
                        f"Нет доступа к модели {model_id}. "
                        f"Необходимо принять условия использования на HuggingFace:\n"
                        f"1. Перейдите на https://huggingface.co/{model_id}\n"
                        f"2. Нажмите 'Agree and access repository'\n"
                        f"3. Также примите условия для pyannote/segmentation-3.0\n"
                        f"4. Убедитесь, что токен имеет права 'read'"
                    )
                    continue
                else:
                    logger.warning(f"Ошибка при загрузке {model_id}: {e}")
                    continue

        if pipeline is None:
            # from_pretrained вернул None или упал — выясняем ТОЧНУЮ причину пробой
            # каждого нужного репозитория, а не гадаем «условия/токен».
            diagnosis = diagnose_hf_access(self.hf_token)
            base = str(last_error) if last_error else "from_pretrained вернул None"
            raise ValueError(
                "Не удалось загрузить модель диаризации.\n"
                f"Причина: {base}\n\n"
                "Диагностика доступа HuggingFace:\n"
                f"{diagnosis}\n\n"
                "Подсказка: чаще всего это fine-grained токен без права "
                "'Read access to public gated repos' — включите его в настройках токена "
                "или создайте classic read-токен."
            )

        # Перемещение на устройство
        try:
            import torch
            device = torch.device(self.device)
            pipeline = pipeline.to(device)
        except Exception as e:
            logger.warning(f"Не удалось переместить pipeline на {self.device}: {e}")

        return pipeline

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

            # Pipeline возвращает pyannote.core.Annotation — итерируем через itertracks.
            # Поддерживается всеми версиями pyannote.audio 2.x–3.x.
            if not hasattr(diarization, 'itertracks'):
                raise ValueError(
                    f"Неожиданный тип результата диаризации: {type(diarization).__name__} "
                    "(ожидался pyannote.core.Annotation с методом itertracks)"
                )
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

            # `completed/total` applies to the current internal pyannote step,
            # not the whole diarization pipeline.  Expose the real work units
            # but keep the overall stage indeterminate rather than inventing a
            # misleading whole-pipeline percentage.
            progress_callback(
                None,
                float(completed),
                float(total) if isinstance(total, (int, float)) else None,
            )

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

    def _rename_speakers(
        self,
        segments: list[SpeakerSegment]
    ) -> list[SpeakerSegment]:
        """
        Переименование спикеров в человекочитаемый формат.

        SPEAKER_00 -> Спикер №1
        SPEAKER_01 -> Спикер №2
        """
        # Получаем уникальных спикеров в порядке первого появления
        seen = set()
        speaker_order = []
        for seg in segments:
            if seg.speaker not in seen:
                seen.add(seg.speaker)
                speaker_order.append(seg.speaker)

        # Создаём маппинг
        speaker_map = {
            old_name: f"Спикер №{i+1}"
            for i, old_name in enumerate(speaker_order)
        }

        # Применяем переименование
        for seg in segments:
            seg.speaker = speaker_map.get(seg.speaker, seg.speaker)

        return segments

    def map_speakers_to_transcription(
        self,
        transcription_segments: list,
        speaker_segments: list[SpeakerSegment],
    ) -> list:
        """
        Сопоставление транскрипции с диаризацией по временным меткам.

        Для каждого сегмента транскрипции определяется спикер
        на основе временного пересечения с сегментами диаризации.

        Args:
            transcription_segments: Сегменты транскрипции (словари с 'transcription', 'boundaries')
            speaker_segments: Сегменты диаризации

        Returns:
            Сегменты транскрипции с добавленным полем 'speaker'
        """
        for trans_seg in transcription_segments:
            # Получаем границы сегмента
            start, end = trans_seg.get('boundaries', (0.0, 0.0))

            # Находим midpoint сегмента транскрипции
            midpoint = (start + end) / 2

            # Ищем спикера, говорившего в этот момент
            speaker = self._find_speaker_at_time(midpoint, speaker_segments)

            # Если не нашли по midpoint, ищем по максимальному пересечению
            if speaker is None:
                speaker = self._find_speaker_by_overlap(start, end, speaker_segments)

            # Добавляем информацию о спикере в сегмент
            trans_seg['speaker'] = speaker if speaker else "Неизвестный спикер"

        return transcription_segments

    def _find_speaker_at_time(
        self,
        time: float,
        speaker_segments: list[SpeakerSegment],
    ) -> str | None:
        """Найти спикера, говорившего в указанный момент времени."""
        for seg in speaker_segments:
            if seg.start <= time <= seg.end:
                return seg.speaker
        return None

    def _find_speaker_by_overlap(
        self,
        start: float,
        end: float,
        speaker_segments: list[SpeakerSegment],
    ) -> str | None:
        """
        Найти спикера с максимальным пересечением по времени.
        """
        max_overlap = 0
        best_speaker = None

        for sp_seg in speaker_segments:
            # Вычисляем пересечение
            overlap_start = max(start, sp_seg.start)
            overlap_end = min(end, sp_seg.end)
            overlap = max(0, overlap_end - overlap_start)

            if overlap > max_overlap:
                max_overlap = overlap
                best_speaker = sp_seg.speaker

        return best_speaker


def get_diarization_manager(
    hf_token: str | None = None,
    device: str = "auto",
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
    return DiarizationManager(
        hf_token=hf_token,
        device=device,
        **kwargs
    )
