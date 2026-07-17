"""
Модуль диаризации спикеров для GigaAM v3 Transcriber.

Поддерживает:
- pyannote: полная диаризация через pyannote/speaker-diarization-3.1;
- sortformer: NVIDIA Streaming Sortformer 4spk v2.1 через NeMo.
"""

import inspect
import logging
import os
import threading
import warnings
from contextlib import nullcontext
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

_DIARIZATION_MODEL_ID = "pyannote/speaker-diarization-3.1"
_SORTFORMER_MODEL_ID = "nvidia/diar_streaming_sortformer_4spk-v2.1"

DIARIZATION_BACKENDS = ("pyannote", "sortformer")
_DIARIZATION_BACKEND_ALIASES = {
    "pyannote": "pyannote",
    "sortformer": "sortformer",
    "nvidia": "sortformer",
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
        """Сопоставить ASR с диаризацией, сохраняя смены спикеров.

        PyTorch GigaAM передаёт word-level timestamps. В этом случае один
        длинный ASR-сегмент разбивается на последовательные реплики по словам,
        а не схлопывается до спикера в midpoint. Backend-ы без word timestamps
        сохраняют прежний безопасный fallback с одним спикером на ASR-сегмент.
        """
        mapped: list = []
        for trans_seg in transcription_segments:
            words = trans_seg.get("words") or []
            if not words:
                start, end = trans_seg.get('boundaries', (0.0, 0.0))
                midpoint = (start + end) / 2
                speaker = self._find_speaker_at_time(midpoint, speaker_segments)
                if speaker is None:
                    speaker = self._find_speaker_by_overlap(start, end, speaker_segments)
                trans_seg['speaker'] = speaker if speaker else "Неизвестный спикер"
                mapped.append(trans_seg)
                continue

            current = None
            for word in words:
                text = str(word.get("text", "")).strip()
                start = float(word.get("start", 0.0))
                end = max(start, float(word.get("end", start)))
                if not text:
                    continue
                speaker = self._find_speaker_by_overlap(start, end, speaker_segments)
                if speaker is None:
                    speaker = self._find_speaker_at_time((start + end) / 2, speaker_segments)
                speaker = speaker or "Неизвестный спикер"

                if current is not None and current["speaker"] == speaker:
                    current["transcription"] = f'{current["transcription"]} {text}'
                    current["boundaries"] = (current["boundaries"][0], end)
                    continue

                current = {
                    "transcription": text,
                    "boundaries": (start, end),
                    "speaker": speaker,
                }
                mapped.append(current)

            if current is None:
                fallback = dict(trans_seg)
                fallback.pop("words", None)
                start, end = fallback.get('boundaries', (0.0, 0.0))
                speaker = self._find_speaker_by_overlap(start, end, speaker_segments)
                fallback["speaker"] = speaker or "Неизвестный спикер"
                mapped.append(fallback)

        return mapped

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
        """NeMo поддерживает CUDA/CPU; MPS пока оставляем на безопасном CPU."""
        if device not in {"auto", "cuda", "cpu", "mps"}:
            raise ValueError(f"Неподдерживаемое устройство Sortformer: {device}")
        if device in {"cpu", "mps"}:
            return "cpu"
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda"
        except ImportError:
            pass
        return "cpu"

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
            with self._inference_lock:
                pipeline = self.pipeline
                with self._inference_context():
                    predicted = pipeline.diarize(
                        audio=[str(audio_path)],
                        batch_size=1,
                        num_workers=0,
                        verbose=False,
                    )
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
    if backend == "sortformer":
        return SortformerDiarizationManager(device=device)
    return DiarizationManager(
        hf_token=hf_token,
        device=device,
        **kwargs
    )
