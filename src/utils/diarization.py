"""
Модуль диаризации спикеров для GigaAM v3 Transcriber.

Поддерживает:
- pyannote: Полная диаризация через pyannote/speaker-diarization-3.1
"""

import logging
import os
import warnings
from pathlib import Path
from typing import List, Optional, Tuple

# Применяем патч для совместимости с NumPy 2.0
from .pyannote_patch import apply_pyannote_patch
apply_pyannote_patch()

logger = logging.getLogger(__name__)


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
        hf_token: Optional[str] = None,
        device: str = "auto",
        min_speakers: Optional[int] = None,
        max_speakers: Optional[int] = None,
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
            from pyannote.audio import Pipeline
            import torch
        except ImportError:
            raise ImportError(
                "pyannote.audio не установлен. "
                "Установите: pip install pyannote.audio"
            )
        
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
                    logger.info(f"Модель {model_id} загружена успешно")
                    break
                except TypeError:
                    # Fallback для старых версий
                    pipeline = Pipeline.from_pretrained(
                        model_id,
                        use_auth_token=self.hf_token
                    )
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
            # Если все попытки не удались
            if last_error:
                error_str = str(last_error)
                if "403" in error_str or "gated" in error_str.lower():
                    raise ValueError(
                        f"Нет доступа к моделям диаризации. "
                        f"Примите условия использования на HuggingFace:\n"
                        f"- https://huggingface.co/pyannote/speaker-diarization-3.1\n"
                        f"- https://huggingface.co/pyannote/segmentation-3.0\n"
                        f"- https://huggingface.co/pyannote/speaker-diarization\n"
                        f"\nПосле принятия условий повторите попытку."
                    )
                else:
                    raise ValueError(
                        f"Не удалось загрузить модель диаризации: {last_error}"
                    )
            else:
                raise ValueError("Не удалось загрузить модель диаризации")
        
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
        num_speakers: Optional[int] = None,
        min_speakers: Optional[int] = None,
        max_speakers: Optional[int] = None,
    ) -> List[SpeakerSegment]:
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
                diarization = self.pipeline(str(audio_path), **kwargs)
            
            # Преобразование результатов
            segments = []
            
            # Проверяем, какой API использует pyannote
            if hasattr(diarization, 'itertracks'):
                # Старый API (pyannote.audio < 3.0)
                for turn, _, speaker in diarization.itertracks(yield_label=True):
                    segments.append(SpeakerSegment(
                        start=turn.start,
                        end=turn.end,
                        speaker=speaker
                    ))
            else:
                # Новый API (pyannote.audio >= 3.0)
                for turn, speaker in diarization.speaker_diarization:
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
            raise ValueError(f"Ошибка при диаризации: {e}")
    
    def _rename_speakers(
        self, 
        segments: List[SpeakerSegment]
    ) -> List[SpeakerSegment]:
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
        speaker_segments: List[SpeakerSegment],
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
        speaker_segments: List[SpeakerSegment],
    ) -> Optional[str]:
        """Найти спикера, говорившего в указанный момент времени."""
        for seg in speaker_segments:
            if seg.start <= time <= seg.end:
                return seg.speaker
        return None
    
    def _find_speaker_by_overlap(
        self,
        start: float,
        end: float,
        speaker_segments: List[SpeakerSegment],
    ) -> Optional[str]:
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
    hf_token: Optional[str] = None,
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
