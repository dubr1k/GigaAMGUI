"""Стадия диаризации TranscriptionProcessor.

Вынесена из processor.py как mixin: выбор и кэш backend-а (с учётом смены
токена, provider-а и backend-а), запуск диаризации и сопоставление говорящих
со словами. Сбой диаризации никогда не теряет транскрипт и не подменяется
фиктивной разметкой «Спикер №1».
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from .processing_support import DiarizationOutcome, DiarizationSetupError

if TYPE_CHECKING:
    from .diarization.base import DiarizationBackend

_module_logger = logging.getLogger("src.core.processor")


class DiarizationStageMixin:
    """Требует от класса: logger, model_loader, _diarization_manager,
    _diarization_provider, _diarization_factory_error, _active_diarization_backend,
    _update_progress и _stage_reporter."""

    @property
    def diarization_manager(self) -> DiarizationBackend | None:
        """Ленивая загрузка выбранного backend с актуальным HF-токеном."""
        from ..config import ONNX_MODEL_DIR, ONNX_PROVIDER
        from .diarization.factory import create_diarization_backend
        from .diarization.names import normalize_diarization_backend

        backend = normalize_diarization_backend(self._active_diarization_backend)
        hf_token = os.getenv("HF_TOKEN", "").strip()
        # Provider переключается в настройках уже после создания processor,
        # поэтому берём актуальное значение loader-а, а не снимок из .env.
        provider = getattr(self.model_loader, "requested_provider", None) or ONNX_PROVIDER

        # Токен можно заменить в GUI уже после создания processor. Не держим
        # менеджер (и загруженный им pipeline) со старым токеном.
        if (
            self._diarization_manager is not None
            and (
                getattr(self._diarization_manager, "backend", "pyannote") != backend
                or (backend == "onnx" and self._diarization_provider != provider)
                or (
                    backend == "pyannote"
                    and getattr(self._diarization_manager, "hf_token", hf_token) != hf_token
                )
            )
        ):
            self._diarization_manager = None

        if backend == "pyannote" and not hf_token:
            self._diarization_manager = None
            return None

        if self._diarization_manager is None:
            try:
                self._diarization_manager = create_diarization_backend(
                    backend,
                    hf_token=hf_token or None,
                    device="auto",
                    provider=provider,
                    model_dir=ONNX_MODEL_DIR,
                )
                self._diarization_provider = provider
                self._diarization_factory_error = None
            except Exception as e:
                self._diarization_factory_error = e
                self.logger(f"Не удалось подготовить определение говорящих: {e}")
        return self._diarization_manager

    def _run_diarization(
        self,
        audio_path: str,
        utterances: list,
        *,
        requested: bool,
        num_speakers: int | None,
    ) -> DiarizationOutcome:
        """Диаризация с понятной причиной отказа; транскрипт не теряется никогда."""
        if not requested:
            return DiarizationOutcome(utterances)

        if (
            self._active_diarization_backend == "pyannote"
            and not os.getenv("HF_TOKEN", "").startswith("hf_")
        ):
            token_error = (
                "Диаризация pyannote требует HuggingFace read-токен "
                "с префиксом hf_."
            )
            self.logger(f"Ошибка: {token_error}")
            self.logger("Укажите токен в настройках диаризации (определения говорящих).")
            return DiarizationOutcome(utterances, error=token_error)

        if not utterances:
            return DiarizationOutcome(utterances, attempted=True)

        self.logger(f"Определяем, кто говорит ({self._active_diarization_backend})…")
        try:
            manager = self.diarization_manager
            runtime_details = []
            if manager is not None:
                device = getattr(manager, "device", None)
                provider = getattr(manager, "provider", None)
                if device:
                    runtime_details.append(f"устройство {device}")
                if provider:
                    runtime_details.append(f"провайдер {provider}")
            if runtime_details:
                self.logger(
                    "Определение говорящих выполняется на: "
                    + ", ".join(runtime_details)
                )
            self._update_progress("diarization", None)
            mapped = self._apply_diarization(
                audio_path,
                utterances,
                manager=manager,
                num_speakers=num_speakers,
                progress_callback=self._stage_reporter("diarization"),
            )
            self._update_progress("diarization", 1.0)
            fallback_reason = getattr(manager, "last_fallback_reason", None)
            if fallback_reason:
                self.logger(
                    "Внимание: определение говорящих переключилось на запасной режим — "
                    + fallback_reason
                )
                manager.last_fallback_reason = None
            self.logger(f"Говорящих найдено: {len(set(u.get('speaker', 'Неизвестный спикер') for u in mapped))}")
            return DiarizationOutcome(mapped, applied=True, attempted=True)
        except Exception as e:
            # Диаризация не удалась — сохраняем транскрипт БЕЗ фиктивной
            # разметки «Спикер №1» и даём пользователю реальную причину.
            self.logger(f"Не удалось определить говорящих, текст сохранён без разметки по говорящим: {e}")
            if (
                self._active_diarization_backend == "pyannote"
                and not isinstance(e, DiarizationSetupError)
            ):
                self.logger("Частая причина: на huggingface.co не приняты условия ВСЕХ моделей —")
                self.logger("  pyannote/segmentation-3.0, pyannote/speaker-diarization-3.1")
                self.logger("  и модели эмбеддингов (wespeaker-voxceleb-resnet34-LM),")
                self.logger("либо у токена нет права read.")
            else:
                self.logger("Причина указана выше.")
            return DiarizationOutcome(utterances, error=str(e), attempted=True)

    def _apply_diarization(
        self,
        audio_path: str,
        utterances: list,
        num_speakers: int | None = None,
        progress_callback=None,
        manager=None,
    ) -> list:
        """
        Применяет диаризацию к сегментам транскрипции.

        Args:
            audio_path: путь к аудио файлу
            utterances: список сегментов транскрипции
            num_speakers: количество спикеров (если известно)
            manager: уже полученный backend (иначе берётся diarization_manager)

        Returns:
            list: utterances с добавленной информацией о спикерах
        """
        # Свойство diarization_manager при каждом обращении заново пробует
        # фабрику, поэтому backend берём один раз.
        if manager is None:
            manager = self.diarization_manager
        if not manager:
            cause = self._diarization_factory_error
            if cause is not None:
                raise DiarizationSetupError(
                    f"Не удалось подготовить определение говорящих: {type(cause).__name__}: {cause}"
                ) from cause
            raise RuntimeError(
                "Менеджер диаризации недоступен. Проверьте HF_TOKEN (нужен доступ read)."
            )

        try:
            kwargs = {}
            if num_speakers is not None:
                kwargs['num_speakers'] = num_speakers

            speaker_segments = manager.diarize(
                audio_path,
                **kwargs,
                progress_callback=progress_callback,
            )
            if not speaker_segments:
                raise RuntimeError(
                    "Диаризатор не вернул ни одного speaker-сегмента."
                )

            # Сопоставляем спикеров с сегментами транскрипции
            return manager.map_speakers_to_transcription(
                utterances,
                speaker_segments
            )

        except Exception as e:
            # НЕ маскируем сбой фиктивным «Спикер №1» — пробрасываем наверх,
            # чтобы process_file показал настоящую причину (иначе пользователь
            # видит «найден 1 спикер» и думает, что диаризация сработала).
            # Traceback — в лог приложения, а не в журнал пользователя.
            self.logger(f"Ошибка при определении говорящих: {e}")
            _module_logger.error("Diarization failed for %s", audio_path, exc_info=True)
            raise
