"""
Модуль обработки транскрибации
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable

from ..config import DIARIZATION_BACKEND
from ..utils.audio_converter import AudioConverter
from ..utils.audio_preprocessing import AudioPreprocessor, FFmpegAudioPreprocessingBackend
from ..utils.cancellation import CancelCheck, ProcessingCancelled, raise_if_cancelled
from ..utils.deepfilter_backend import DeepFilterNetBinaryBackend
from ..utils.output_naming import output_filename
from ..utils.time_formatter import TimeFormatter
from . import export, formatters
from .diarization_stage import DiarizationStageMixin
from .preprocessing_messages import (
    PREPROCESSING_ACTIONS_RU as _PREPROCESSING_ACTIONS_RU,  # noqa: F401 — старый импорт
)
from .preprocessing_messages import log_preprocessing_report
from .preprocessing_messages import preprocessing_reason_ru as _preprocessing_reason_ru  # noqa: F401
from .processing_support import (  # DiarizationSetupError — для старых импортов
    DiarizationOutcome,
    DiarizationSetupError,  # noqa: F401
    _accepts_event_argument,
    _accepts_keyword,
    _TempFiles,
)
from .progress import ProgressEvent, ProgressPlan
from .subtitles import SubtitleOptions

_module_logger = logging.getLogger(__name__)


class TranscriptionProcessor(DiarizationStageMixin):
    """Класс для обработки файлов транскрибации"""

    def __init__(
        self,
        model_loader,
        stats_manager,
        logger: Callable = None,
        progress_callback: Callable = None,
        *,
        diarization_manager=None,
        diarization_backend: str | None = None,
    ):
        """
        Args:
            model_loader: экземпляр ModelLoader
            stats_manager: экземпляр ProcessingStats
            logger: функция для логирования
            progress_callback: функция для обновления прогресса (опционально)
        """
        self.model_loader = model_loader
        self.stats = stats_manager
        self.logger = logger or print
        self.progress_callback = progress_callback
        self.audio_converter = AudioConverter(self.logger)
        self.audio_preprocessor = AudioPreprocessor(
            dsp_backend=FFmpegAudioPreprocessingBackend(self.logger),
            neural_backend=DeepFilterNetBinaryBackend(self.logger),
        )
        self.time_formatter = TimeFormatter()
        self._diarization_manager = diarization_manager
        self._active_diarization_backend = diarization_backend or DIARIZATION_BACKEND
        # Готовый менеджер (GUI подготавливает его заранее) создан под свой
        # provider. Без этого None != provider сбрасывал его при первом же
        # обращении, и ONNX-модели диаризации грузились второй раз.
        self._diarization_provider = getattr(diarization_manager, "provider", None)
        # Исключение последней попытки создать backend: без него сбой фабрики
        # (например, WinError 1114 при загрузке torch) превращался в «Проверьте HF_TOKEN».
        self._diarization_factory_error: Exception | None = None
        self._progress_plan = None
        # (колбэк, принимает ли он ProgressEvent) — progress_callback публичен и
        # может быть заменён после создания процессора.
        self._progress_style: tuple[Callable, bool] | None = None

    def _emit_progress(self, event: ProgressEvent) -> None:
        callback = self.progress_callback
        if not callback:
            return

        if self._progress_plan is not None:
            event = self._progress_plan.normalize_event(event)

        if self._progress_style is None or self._progress_style[0] is not callback:
            self._progress_style = (callback, _accepts_event_argument(callback))
        if self._progress_style[1]:
            callback(event)
        else:
            callback(event.stage, event.file_progress)

    def _update_progress(
        self,
        stage: str,
        stage_progress: float | None,
        *,
        processed_seconds: float | None = None,
        total_seconds: float | None = None,
    ):
        event = ProgressEvent(
            stage=stage,
            stage_progress=stage_progress,
            file_progress=0.0,
            processed_seconds=processed_seconds,
            total_seconds=total_seconds,
            message=None,
        )
        self._emit_progress(event)

    def _stage_reporter(self, stage: str) -> Callable[[float | None, float | None, float | None], None]:
        """Колбэк (доля, обработано, всего) для ASR и диаризации."""

        def report(stage_progress, processed, total):
            self._update_progress(
                stage,
                stage_progress,
                processed_seconds=processed,
                total_seconds=total,
            )

        return report

    def process_file(self,
                     filepath: str,
                     output_dir: str,
                     file_index: int,
                     total_files: int,
                     original_filename: str | None = None,
                     estimated_conversion_ratio: float = 0.05,
                     estimated_transcription_ratio: float = 0.95,
                     enable_diarization: bool = False,
                     num_speakers: int | None = None,
                     output_formats: list | None = None,
                     diarization_backend: str = DIARIZATION_BACKEND,
                     audio_preprocessing_mode: str = "off",
                     subtitle_options: SubtitleOptions | None = None,
                     cancel_check: CancelCheck | None = None) -> dict:
        """
        Обрабатывает один файл

        Args:
            filepath: путь к файлу
            output_dir: папка для сохранения результатов
            file_index: индекс файла (для логирования)
            total_files: общее количество файлов
            output_formats: список форматов вывода ('txt', 'md', 'srt', 'vtt');
                None — TXT, пустой список — ничего не сохранять (только utterances)
            original_filename: оригинальное имя файла (если отличается от filepath)
            estimated_conversion_ratio: не используется, оставлен для совместимости
            estimated_transcription_ratio: не используется, оставлен для совместимости
            enable_diarization: включить диаризацию спикеров
            num_speakers: количество спикеров (если известно)
            diarization_backend: backend диаризации (`onnx`, `pyannote` или `sortformer`)
            audio_preprocessing_mode: подготовка аудио (`off`, `auto`, `light` или `denoise`)
            subtitle_options: правила пофразной разбивки SRT/VTT
            cancel_check: вызывается между стадиями, на строках прогресса ffmpeg
                и между окнами ASR; True — прервать файл (result['cancelled'])

        Returns:
            dict: результаты обработки с ключами:
                - success: bool
                - error: str | None — причина неуспеха для клиента
                - file_path: str
                - file_size: int
                - media_duration: float
                - total_time / conversion_time / preprocessing_time / transcription_time: float
                - audio_preprocessing: dict | None
                - diarization: {requested, applied, backend, error}
                - saved_files: list[str]
                - export_errors: dict[str, str] — несохранённые форматы
                - utterances: list (после распознавания)
                - cancelled: bool — файл прерван через cancel_check
        """
        with _TempFiles(protected=filepath) as temp_files:
            return self._process_file(
                filepath,
                output_dir,
                file_index,
                total_files,
                temp_files,
                original_filename=original_filename,
                enable_diarization=enable_diarization,
                num_speakers=num_speakers,
                output_formats=output_formats,
                diarization_backend=diarization_backend,
                audio_preprocessing_mode=audio_preprocessing_mode,
                subtitle_options=subtitle_options,
                cancel_check=cancel_check,
            )

    def _process_file(
        self,
        filepath: str,
        output_dir: str,
        file_index: int,
        total_files: int,
        temp_files: _TempFiles,
        *,
        original_filename: str | None,
        enable_diarization: bool,
        num_speakers: int | None,
        output_formats: list | None,
        diarization_backend: str,
        audio_preprocessing_mode: str,
        subtitle_options: SubtitleOptions | None,
        cancel_check: CancelCheck | None = None,
    ) -> dict:
        file_start_time = time.time()
        # Используем оригинальное имя если передано, иначе берем из пути
        filename = original_filename if original_filename else os.path.basename(filepath)
        name_without_ext = os.path.splitext(filename)[0]
        file_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0

        # Получаем длительность медиа файла
        media_duration = AudioConverter.get_media_duration(filepath)

        result = {
            'success': False,
            'file_path': filepath,
            'file_size': file_size,
            'media_duration': media_duration,
            'total_time': 0,
            'conversion_time': 0,
            'preprocessing_time': 0,
            'transcription_time': 0,
            'audio_preprocessing': None,
            'diarization': {
                'requested': bool(enable_diarization),
                'applied': False,
                'backend': diarization_backend,
                'error': None,
            },
            'saved_files': [],
            # Формат → причина, если отдельный формат не удалось сохранить.
            'export_errors': {},
            # Причина неуспеха одной строкой для клиентов, которые не видят журнал
            # (web, MCP, OpenAI-совместимый API). None при success.
            'error': None,
            'cancelled': False,
        }

        def fail(message: str) -> dict:
            result['error'] = message
            result['total_time'] = time.time() - file_start_time
            return result

        try:
            self.logger(f"Файл {file_index+1} из {total_files}: {filename}")
            self.logger(f"Длительность записи: {self.time_formatter.format_clock(media_duration)}")

            # Папку результатов создаём до долгой обработки: иначе (новая папка в
            # CLI) распознавание шло впустую и падало только на сохранении.
            try:
                os.makedirs(output_dir, exist_ok=True)
            except OSError as exc:
                message = f"Не удалось создать папку для результатов {output_dir}: {exc}"
                self.logger(message)
                return fail(message)

            from .diarization.names import normalize_diarization_backend

            self._active_diarization_backend = normalize_diarization_backend(diarization_backend)
            self._progress_plan = ProgressPlan(has_diarization=enable_diarization)
            self._update_progress("preparing", 0.0, total_seconds=media_duration, processed_seconds=0.0)

            raise_if_cancelled(cancel_check)
            temp_audio = self._convert(filepath, temp_files, media_duration, result, cancel_check)
            if not temp_audio:
                self.logger(f"Файл пропущен: {filename}")
                return fail(
                    getattr(self.audio_converter, "last_error", None)
                    or "Не удалось подготовить звук: FFmpeg не создал WAV, подробности в журнале обработки"
                )

            raise_if_cancelled(cancel_check)
            asr_audio, diarization_audio = self._preprocess(
                temp_audio,
                temp_files,
                audio_preprocessing_mode,
                media_duration,
                result,
            )

            transcription_start = time.time()
            try:
                try:
                    utterances = self._transcribe(asr_audio, cancel_check)
                except ProcessingCancelled:
                    raise
                except Exception as e:
                    # Сбой VAD backend-ы обрабатывают сами (резервное разбиение),
                    # поэтому ValueError здесь — не «ошибка детектора речи», а
                    # настоящая ошибка распознавания. Подсказка про токен — только
                    # когда исключение действительно о нём.
                    error_msg = f"Ошибка при распознавании речи: {e}"
                    self.logger(error_msg)
                    if "HF_TOKEN" in str(e):
                        self.logger("Проверьте токен HF_TOKEN в .env файле и убедитесь, что приняли условия доступа:")
                        self.logger("https://huggingface.co/pyannote/segmentation-3.0")
                    _module_logger.error("ASR failed for %s", filepath, exc_info=True)
                    result['transcription_time'] = time.time() - transcription_start
                    return fail(error_msg)

                result['transcription_time'] = time.time() - transcription_start
                self._update_progress('transcription', 1.0)
                self.logger(f"Распознавание завершено: фрагментов текста — {len(utterances) if utterances else 0}")

                raise_if_cancelled(cancel_check)
                diarization = self._run_diarization(
                    diarization_audio,
                    utterances or [],
                    requested=enable_diarization,
                    num_speakers=num_speakers,
                )
                result['diarization']['applied'] = diarization.applied
                result['diarization']['error'] = diarization.error
                utterances = diarization.utterances

                raise_if_cancelled(cancel_check)
                full_text = self._summarize_transcript(utterances, filename)
                # Реплики нужны API, который собирает ответ в памяти, не читая файлы,
                # в том числе когда какой-то формат не удалось сохранить.
                result['utterances'] = utterances

                outcome = self._export(
                    utterances,
                    output_dir=output_dir,
                    stem=name_without_ext,
                    filename=filename,
                    full_text=full_text,
                    output_formats=output_formats,
                    diarization=diarization,
                    subtitle_options=subtitle_options,
                )
                result['saved_files'] = outcome.saved_files
                result['export_errors'] = outcome.errors
                if outcome.errors and not outcome.saved_files:
                    return fail("Не удалось сохранить результаты: " + "; ".join(
                        f"{fmt}: {reason}" for fmt, reason in outcome.errors.items()
                    ))

                # Проверка сохраненных данных
                if not full_text.strip():
                    self.logger("Внимание: распознанный текст пуст")
                else:
                    self.logger(f"Объём текста: {len(full_text)} символов")

                # Успех (отдельные несохранённые форматы — в export_errors)
                result['success'] = True
                result['total_time'] = time.time() - file_start_time

                for saved_file in outcome.saved_files:
                    self.logger(f"Сохранён файл: {os.path.basename(saved_file)}")
                self.logger(f"Готово за {self.time_formatter.format_duration(result['total_time'])} " +
                           f"(подготовка звука {round(result['conversion_time'], 1)} с, " +
                           f"распознавание {round(result['transcription_time'], 1)} с)")
                self._update_progress("finalizing", 1.0)

            except ProcessingCancelled:
                raise
            except Exception as e:
                result['success'] = False
                if not result['transcription_time']:
                    result['transcription_time'] = time.time() - transcription_start
                message = f"Не удалось обработать {filename}: {e}"
                self.logger(message)
                _module_logger.error("Processing failed for %s", filepath, exc_info=True)
                fail(message)

        except ProcessingCancelled as exc:
            # Частичные результаты не сохраняются; временные WAV удалит _TempFiles.
            result['success'] = False
            result['cancelled'] = True
            self.logger(str(exc))
            return fail(str(exc))

        return result

    def _convert(
        self,
        filepath: str,
        temp_files: _TempFiles,
        media_duration: float,
        result: dict,
        cancel_check: CancelCheck | None = None,
    ) -> str | None:
        """Конвертация в WAV 16 кГц mono в личный temp-каталог обработки."""

        def report(value: float | None) -> None:
            self._update_progress(
                "conversion",
                value,
                total_seconds=media_duration,
                processed_seconds=(
                    value * media_duration if value is not None and media_duration > 0 else None
                ),
            )

        conversion_start = time.time()
        convert = self.audio_converter.convert_to_wav
        extra = {}
        if cancel_check is not None and _accepts_keyword(convert, "cancel_check"):
            extra["cancel_check"] = cancel_check
        temp_audio = convert(
            filepath,
            temp_files.directory,
            media_duration=media_duration,
            progress_callback=report,
            **extra,
        )
        temp_files.add(temp_audio)
        result['conversion_time'] = time.time() - conversion_start
        # Если конвертер вернул путь, считаем стадию завершенной даже при indeterminate-сценарии.
        # Для известных длительностей FFmpeg уже присылает 1.0 в своем колбэке.
        if media_duration and media_duration > 0:
            self._update_progress('conversion', 1.0, total_seconds=media_duration, processed_seconds=media_duration)
        return temp_audio

    def _preprocess(
        self,
        temp_audio: str,
        temp_files: _TempFiles,
        mode: str,
        media_duration: float,
        result: dict,
    ) -> tuple[str, str]:
        """Очистка звука: отдельная дорожка только для ASR.

        Диаризация получает canonical WAV, чтобы не терять тембр и границы
        реплик. Возвращает (дорожка ASR, дорожка диаризации).
        """
        asr_audio = temp_audio
        diarization_audio = temp_audio
        preprocessing_start = time.time()
        self._update_progress("preprocessing", 0.0, total_seconds=media_duration, processed_seconds=0.0)
        try:
            prepared_audio = self.audio_preprocessor.prepare(
                temp_audio,
                temp_files.directory,
                mode=mode,
            )
            temp_files.add(*prepared_audio.temporary_paths)
            asr_audio = prepared_audio.asr_path
            diarization_audio = prepared_audio.diarization_path
            result['audio_preprocessing'] = prepared_audio.report.to_dict()
            log_preprocessing_report(self.logger, prepared_audio.report)
        except Exception as exc:
            # Quality enhancement никогда не должен ломать базовую транскрибацию.
            self.logger(f"Очистка звука недоступна, используем запись как есть: {exc}")
        finally:
            result['preprocessing_time'] = time.time() - preprocessing_start
            self._update_progress(
                "preprocessing",
                1.0,
                total_seconds=media_duration,
                processed_seconds=media_duration if media_duration > 0 else None,
            )
        return asr_audio, diarization_audio

    def _transcribe(self, asr_audio: str, cancel_check: CancelCheck | None = None) -> list:
        self.logger("Распознаём речь…")
        transcribe = self.model_loader.transcribe_longform
        kwargs = {}
        if _accepts_keyword(transcribe, "logger"):
            # Предупреждения распознавания — в журнал этого файла, а не той
            # задачи, что когда-то загрузила модель.
            kwargs["logger"] = self.logger
        if cancel_check is not None and _accepts_keyword(transcribe, "cancel_check"):
            kwargs["cancel_check"] = cancel_check
        return transcribe(
            asr_audio,
            progress_callback=self._stage_reporter("transcription"),
            **kwargs,
        )

    def _summarize_transcript(self, utterances: list, filename: str) -> str:
        """Журнал по итогам распознавания; возвращает обычный TXT."""
        if not utterances:
            self.logger(
                f"Внимание: в файле {filename} не найдено речи.\n"
                f"Возможные причины:\n"
                f"  1. В записи нет речи или она очень тихая\n"
                f"  2. Не работает токен HuggingFace для pyannote/segmentation-3.0\n"
                f"  3. Не удалось разбить запись на участки речи\n"
                f"Проверьте токен HF_TOKEN и убедитесь, что приняли условия доступа:\n"
                f"https://huggingface.co/pyannote/segmentation-3.0"
            )
        else:
            self.logger(f"Реплик в тексте: {len(utterances)}")
            for utt in utterances:
                text = utt.get('transcription', '')
                if not text or not text.strip():
                    self.logger(f"Внимание: фрагмент {utt.get('boundaries', (0.0, 0.0))} распознан без текста")

        # Декодерные/VAD-границы не являются абзацами: они режут фразы
        # посередине каждые 10–20 секунд. Абзацы TXT закрываются только на
        # конце предложения (пауза или длина), см. formatters._paragraphs.
        full_text = formatters.generate_plain_text(utterances)
        if utterances and not full_text.strip():
            self.logger("Внимание: речь не распознана — все фрагменты пустые")
        return full_text

    def _export(
        self,
        utterances: list,
        *,
        output_dir: str,
        stem: str,
        filename: str,
        full_text: str,
        output_formats: list | None,
        diarization: DiarizationOutcome,
        subtitle_options: SubtitleOptions | None,
    ) -> export.ExportResult:
        formats = export.resolve_output_formats(
            output_formats,
            diarization_applied=diarization.applied,
        )
        self._update_progress("export", 0.0)
        outcome = export.write_outputs(
            output_dir,
            stem,
            formats,
            self._output_renderers(
                utterances,
                filename=filename,
                full_text=full_text,
                diarization_applied=diarization.applied,
                diarization_requested=diarization.attempted,
                subtitle_options=subtitle_options,
            ),
            on_format_done=lambda done, total: self._update_progress("export", done / total),
        )
        for fmt, reason in outcome.errors.items():
            self.logger(f"Не удалось сохранить {output_filename(stem, fmt)}: {reason}")
        return outcome

    def _output_renderers(
        self,
        utterances: list,
        *,
        filename: str,
        full_text: str,
        diarization_applied: bool,
        diarization_requested: bool,
        subtitle_options: SubtitleOptions | None,
    ) -> dict[str, export.Renderer]:
        """Рендер каждого формата; None — файл не создаётся."""

        def diarized(render: Callable[[], str], file_label: str) -> export.Renderer:
            # Файлы с метками спикеров — только после реально успешной
            # диаризации: иначе обычный текст под именем _diarize.txt.
            def run() -> str | None:
                if diarization_applied:
                    content = render()
                    if content.strip():
                        return content
                if diarization_requested:
                    self.logger(f"Внимание: говорящих определить не удалось, файл {file_label} не создан")
                return None

            return run

        return {
            'txt': lambda: full_text,
            'txt_timecodes': lambda: formatters.generate_timecoded_text(utterances, self.time_formatter),
            'txt_diarize': diarized(lambda: formatters.generate_diarized_text(utterances), "_diarize.txt"),
            'txt_diarize_timecodes': diarized(
                lambda: formatters.generate_timecoded_text(
                    utterances, self.time_formatter, with_speakers=True
                ),
                "_diarize_timecodes.txt",
            ),
            'md': lambda: self._generate_markdown(utterances, filename),
            'srt': lambda: self._generate_srt(utterances, subtitle_options),
            'vtt': lambda: self._generate_vtt(utterances, subtitle_options),
        }

    def _generate_srt(
        self,
        utterances: list,
        options: SubtitleOptions | None = None,
    ) -> str:
        """Генерирует контент в формате SRT субтитров (делегирует в formatters)."""
        return formatters.generate_srt(utterances, options)

    def _generate_vtt(
        self,
        utterances: list,
        options: SubtitleOptions | None = None,
    ) -> str:
        """Генерирует контент в формате VTT субтитров (делегирует в formatters)."""
        return formatters.generate_vtt(utterances, options)

    def _generate_markdown(self, utterances: list, filename: str) -> str:
        """Генерирует контент в формате Markdown (делегирует в formatters)."""
        return formatters.generate_markdown(utterances, filename, self.time_formatter)
