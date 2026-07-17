"""
Модуль обработки транскрибации
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from ..utils.audio_converter import AudioConverter
from ..utils.audio_preprocessing import AudioPreprocessor, FFmpegAudioPreprocessingBackend
from ..utils.deepfilter_backend import DeepFilterNetBinaryBackend
from ..utils.output_naming import output_path
from ..utils.time_formatter import TimeFormatter
from . import formatters
from .progress import ProgressEvent, ProgressPlan

if TYPE_CHECKING:
    from ..utils.diarization import DiarizationManager


class TranscriptionProcessor:
    """Класс для обработки файлов транскрибации"""

    def __init__(self, model_loader, stats_manager, logger: Callable = None, progress_callback: Callable = None):
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
        self._diarization_manager = None
        self._active_diarization_backend = "pyannote"
        self._progress_plan = None

    def _emit_progress(self, event: ProgressEvent) -> None:
        if not self.progress_callback:
            return

        if self._progress_plan is not None:
            event = self._progress_plan.normalize_event(event)

        try:
            self.progress_callback(event)
            return
        except TypeError:
            self.progress_callback(event.stage, event.file_progress)
        except Exception:
            raise

    def _emit_legacy_stage(self, stage: str, progress: float):
        if not self.progress_callback:
            return
        try:
            self.progress_callback(stage, progress)
        except TypeError:
            return

    @property
    def diarization_manager(self) -> DiarizationManager | None:
        """Ленивая загрузка выбранного backend с актуальным HF-токеном."""
        from ..utils.diarization import get_diarization_manager, normalize_diarization_backend

        backend = normalize_diarization_backend(self._active_diarization_backend)
        hf_token = os.getenv("HF_TOKEN", "").strip()

        # Токен можно заменить в GUI уже после создания processor. Не держим
        # менеджер (и загруженный им pipeline) со старым токеном.
        if (
            self._diarization_manager is not None
            and (
                getattr(self._diarization_manager, "backend", "pyannote") != backend
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
                self._diarization_manager = get_diarization_manager(
                    backend=backend,
                    hf_token=hf_token or None,
                    device="auto"
                )
            except Exception as e:
                self.logger(f"Не удалось инициализировать менеджер диаризации: {e}")
        return self._diarization_manager

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
                     diarization_backend: str = "pyannote",
                     audio_preprocessing_mode: str = "off") -> dict:
        """
        Обрабатывает один файл

        Args:
            filepath: путь к файлу
            output_dir: папка для сохранения результатов
            file_index: индекс файла (для логирования)
            total_files: общее количество файлов
            output_formats: список форматов вывода ('txt', 'md', 'srt', 'vtt')
            original_filename: оригинальное имя файла (если отличается от filepath)
            estimated_conversion_ratio: доля времени на конвертацию (0-1)
            estimated_transcription_ratio: доля времени на транскрибацию (0-1)
            enable_diarization: включить диаризацию спикеров
            num_speakers: количество спикеров (если известно)
            diarization_backend: backend диаризации (`pyannote` или `sortformer`)
            audio_preprocessing_mode: подготовка аудио (`off`, `auto`, `light` или `denoise`)

        Returns:
            dict: результаты обработки с ключами:
                - success: bool
                - file_path: str
                - file_size: int
                - total_time: float
                - conversion_time: float
                - transcription_time: float
        """
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
            'saved_files': []
        }

        # Логирование начала
        duration_str = f"{int(media_duration//60)}:{int(media_duration%60):02d}" if media_duration > 0 else "неизвестна"
        self.logger(f"--- Обработка файла {file_index+1}/{total_files}: {filename} ---")
        self.logger(f"Длительность: {duration_str}")

        from ..utils.diarization import normalize_diarization_backend

        self._active_diarization_backend = normalize_diarization_backend(diarization_backend)
        self._progress_plan = ProgressPlan(has_diarization=enable_diarization)
        self._update_progress("preparing", 0.0, total_seconds=media_duration, processed_seconds=0.0)

        # Конвертация
        conversion_start = time.time()
        temp_audio = self.audio_converter.convert_to_wav(
            filepath,
            output_dir,
            media_duration=media_duration,
            progress_callback=lambda value: self._update_progress(
                "conversion",
                value,
                total_seconds=media_duration,
                processed_seconds=value * media_duration if value is not None and media_duration > 0 else None,
            ) if value is not None else self._update_progress("conversion", None, total_seconds=media_duration, processed_seconds=None),
        )
        result['conversion_time'] = time.time() - conversion_start
        # Если конвертер вернул путь, считаем стадию завершенной даже при indeterminate-сценарии.
        # Для известных длительностей FFmpeg уже присылает 1.0 в своем колбэке.
        if media_duration and media_duration > 0 and result['conversion_time'] >= 0:
            self._update_progress('conversion', 1.0, total_seconds=media_duration, processed_seconds=media_duration)

        if not temp_audio:
            self.logger(f"Пропуск файла {filename}")
            result['total_time'] = time.time() - file_start_time
            return result

        # Подготовка создаёт отдельную дорожку только для ASR. Диаризация
        # получает canonical WAV, чтобы не терять тембр и границы реплик.
        asr_audio = temp_audio
        diarization_audio = temp_audio
        preprocessing_temp_paths: tuple[str, ...] = ()
        preprocessing_start = time.time()
        self._update_progress("preprocessing", 0.0, total_seconds=media_duration, processed_seconds=0.0)
        try:
            prepared_audio = self.audio_preprocessor.prepare(
                temp_audio,
                output_dir,
                mode=audio_preprocessing_mode,
            )
            asr_audio = prepared_audio.asr_path
            diarization_audio = prepared_audio.diarization_path
            preprocessing_temp_paths = prepared_audio.temporary_paths
            result['audio_preprocessing'] = prepared_audio.report.to_dict()
            decision = prepared_audio.report.decision
            self.logger(
                "Предобработка аудио: "
                f"режим={prepared_audio.report.mode}, действие={decision.action}, "
                f"применено={'да' if prepared_audio.report.applied else 'нет'}"
            )
            for reason in decision.reasons:
                self.logger(f"  Причина: {reason}")
            if prepared_audio.report.runtime_fallback:
                detail = prepared_audio.report.fallback_reason or "безопасный fallback к исходной дорожке"
                self.logger(f"  Очистка не применена: {detail}")
        except Exception as exc:
            # Quality enhancement никогда не должен ломать базовую транскрибацию.
            self.logger(f"Предобработка аудио недоступна, используется исходная дорожка: {exc}")
        finally:
            result['preprocessing_time'] = time.time() - preprocessing_start
            self._update_progress(
                "preprocessing",
                1.0,
                total_seconds=media_duration,
                processed_seconds=media_duration if media_duration > 0 else None,
            )

        # Транскрибация
        transcription_start = time.time()
        try:
            self.logger("Распознавание речи (GigaAM-v3)...")
            # Транскрибация (обновляем прогресс постепенно)
            try:
                utterances = self.model_loader.transcribe_longform(
                    asr_audio,
                    progress_callback=lambda stage_progress, processed, total: self._update_progress(
                        "transcription",
                        stage_progress,
                        processed_seconds=processed,
                        total_seconds=total,
                    ),
                )
            except ValueError as e:
                # Ошибка VAD (обычно связана с токеном)
                error_msg = str(e)
                if "HF_TOKEN" in error_msg:
                    self.logger(f"ОШИБКА VAD: {error_msg}")
                    self.logger("Проверьте токен HF_TOKEN в .env файле и убедитесь, что приняли условия доступа:")
                    self.logger("https://huggingface.co/pyannote/segmentation-3.0")
                else:
                    self.logger(f"ОШИБКА VAD: {error_msg}")
                result['transcription_time'] = time.time() - transcription_start
                result['total_time'] = time.time() - file_start_time
                return result
            except Exception as e:
                # Другие ошибки транскрибации
                self.logger(f"ОШИБКА при транскрибации: {str(e)}")
                import traceback
                traceback.print_exc()
                result['transcription_time'] = time.time() - transcription_start
                result['total_time'] = time.time() - file_start_time
                return result

            result['transcription_time'] = time.time() - transcription_start
            self._update_progress('transcription', 1.0)

            # Логирование результатов транскрибации для отладки
            self.logger(f"Транскрибация завершена. Получено сегментов: {len(utterances) if utterances else 0}")
            if utterances and len(utterances) > 0:
                # Показываем структуру первого сегмента для отладки
                first_utt = utterances[0]
                self.logger(f"Пример структуры сегмента: keys={list(first_utt.keys())}, "
                           f"has_transcription={'transcription' in first_utt}, "
                           f"has_boundaries={'boundaries' in first_utt}")

            # Применение диаризации, если включена
            if (
                enable_diarization
                and self._active_diarization_backend == "pyannote"
                and not os.getenv("HF_TOKEN", "").startswith("hf_")
            ):
                self.logger("ОШИБКА: Диаризация требует токен HuggingFace.")
                self.logger("Установите токен через чекбокс 'Диаризация' в интерфейсе.")
                enable_diarization = False

            diarization_applied = False
            if enable_diarization and utterances and len(utterances) > 0:
                self.logger(f"Применение диаризации спикеров ({self._active_diarization_backend})...")
                try:
                    self._update_progress("diarization", None)
                    utterances = self._apply_diarization(
                        diarization_audio,
                        utterances,
                        num_speakers=num_speakers,
                        progress_callback=lambda stage_progress, processed, total: self._update_progress(
                            "diarization",
                            stage_progress,
                            processed_seconds=processed,
                            total_seconds=total,
                        ),
                    )
                    diarization_applied = True
                    result['diarization']['applied'] = True
                    self._update_progress("diarization", 1.0)
                    self.logger(f"Диаризация завершена. Найдено спикеров: {len(set(u.get('speaker', 'Неизвестный спикер') for u in utterances))}")
                except Exception as e:
                    result['diarization']['error'] = str(e)
                    # Диаризация не удалась — сохраняем транскрипт БЕЗ фиктивной
                    # разметки «Спикер №1» и даём пользователю реальную причину.
                    self.logger(f"ОШИБКА: диаризация не выполнена — спикеры НЕ размечены: {e}")
                    self.logger("Частая причина: на huggingface.co не приняты условия ВСЕХ моделей —")
                    self.logger("  pyannote/segmentation-3.0, pyannote/speaker-diarization-3.1")
                    self.logger("  и модели эмбеддингов (wespeaker-voxceleb-resnet34-LM),")
                    self.logger("либо у токена нет доступа read. Транскрипт сохранён без разметки спикеров.")

            # Проверка результатов транскрибации
            if not utterances or len(utterances) == 0:
                error_msg = (
                    f"ПРЕДУПРЕЖДЕНИЕ: VAD (Voice Activity Detection) не нашел сегментов речи в файле {filename}.\n"
                    f"Возможные причины:\n"
                    f"  1. В файле нет речи или очень тихая речь\n"
                    f"  2. Проблема с токеном HuggingFace для pyannote/segmentation-3.0\n"
                    f"  3. Проблема с сегментацией аудио\n"
                    f"Проверьте токен HF_TOKEN в src/config.py и убедитесь, что приняли условия доступа:\n"
                    f"https://huggingface.co/pyannote/segmentation-3.0"
                )
                self.logger(error_msg)
                # Сохраняем пустые файлы, но логируем предупреждение
                full_text = ""
                timecoded_lines = []
                full_text_diarized = ""
                timecoded_lines_diarized = []
            else:
                self.logger(f"Найдено сегментов речи: {len(utterances)}")

                # Формирование результатов: обычный текст и диаризованный (если включена диаризация)
                # Обычный текст — всегда без меток спикеров
                full_text_lines_plain = []
                timecoded_lines_plain = []
                # Диаризованный текст — с метками спикеров (только при enable_diarization)
                full_text_lines_diarized = []
                timecoded_lines_diarized = []
                current_speaker = None

                for utt in utterances:
                    text = utt.get('transcription', '')
                    boundaries = utt.get('boundaries', (0.0, 0.0))
                    speaker = utt.get('speaker', None)

                    # Проверка на пустой текст
                    if not text or not text.strip():
                        self.logger(f"ПРЕДУПРЕЖДЕНИЕ: Пустой текст в сегменте {boundaries}")
                        continue

                    start, end = boundaries

                    # Обычный текст — всегда без спикеров
                    full_text_lines_plain.append(text)
                    ts_str_plain = (f"[{self.time_formatter.format_timestamp(start)} - "
                                    f"{self.time_formatter.format_timestamp(end)}] {text}")
                    timecoded_lines_plain.append(ts_str_plain)

                    # Диаризованный текст — с метками спикеров только после
                    # реально успешного запуска модели и маппинга.
                    if diarization_applied and speaker:
                        if speaker != current_speaker:
                            if current_speaker is not None:
                                full_text_lines_diarized.append("")
                            full_text_lines_diarized.append(f"[{speaker}]")
                            current_speaker = speaker
                        full_text_lines_diarized.append(text)
                        ts_str_diarized = (f"[{self.time_formatter.format_timestamp(start)} - "
                                           f"{self.time_formatter.format_timestamp(end)}] {speaker}: {text}")
                        timecoded_lines_diarized.append(ts_str_diarized)
                    else:
                        full_text_lines_diarized.append(text)
                        ts_str_diarized = (f"[{self.time_formatter.format_timestamp(start)} - "
                                           f"{self.time_formatter.format_timestamp(end)}] {text}")
                        timecoded_lines_diarized.append(ts_str_diarized)

                # Декодерные/VAD-границы не являются абзацами. В обычном TXT
                # склеиваем их пробелом, чтобы не создавать ложные «обрывы» каждые
                # 10–20 секунд. Таймкодированные форматы сохраняют сегментацию.
                full_text = " ".join(full_text_lines_plain)
                timecoded_lines = timecoded_lines_plain

                # Диаризованный текст — для _diarize.txt и _diarize_timecodes.txt (при enable_diarization)
                full_text_diarized = "\n".join(full_text_lines_diarized)

                if not full_text.strip():
                    self.logger("ПРЕДУПРЕЖДЕНИЕ: Все сегменты имеют пустой текст транскрипции")

            # Определяем форматы вывода (по умолчанию txt). Если пользователь
            # запросил диаризацию и она действительно сработала, всегда создаём
            # хотя бы один явно диаризованный файл — обычный TXT намеренно остаётся
            # без меток спикеров и раньше создавал впечатление, что функция не работает.
            if output_formats is None:
                output_formats = ['txt']
            else:
                output_formats = list(output_formats)
            if diarization_applied and 'txt_diarize' not in output_formats:
                output_formats.append('txt_diarize')
            if (
                diarization_applied
                and 'txt_timecodes' in output_formats
                and 'txt_diarize_timecodes' not in output_formats
            ):
                output_formats.append('txt_diarize_timecodes')
            self._update_progress("export", 0.0)

            # Сохранение в выбранных форматах
            saved_files = []
            total_formats = max(len(output_formats), 1)

            for fmt_index, fmt in enumerate(output_formats, start=1):
                if fmt == 'txt':
                    # Чистый текст без таймкодов и меток спикеров
                    path_txt = output_path(output_dir, name_without_ext, 'txt')
                    with open(path_txt, "w", encoding="utf-8") as f:
                        f.write(full_text)
                    saved_files.append(path_txt)

                elif fmt == 'txt_timecodes':
                    # Текст с таймкодами, без меток спикеров
                    path_ts = output_path(output_dir, name_without_ext, 'txt_timecodes')
                    with open(path_ts, "w", encoding="utf-8") as f:
                        f.write("\n".join(timecoded_lines))
                    saved_files.append(path_ts)

                elif fmt == 'txt_diarize':
                    # Текст с метками спикеров (только при включённой диаризации)
                    if diarization_applied and full_text_diarized.strip():
                        path_diarize = output_path(output_dir, name_without_ext, 'txt_diarize')
                        with open(path_diarize, "w", encoding="utf-8") as f:
                            f.write(full_text_diarized)
                        saved_files.append(path_diarize)
                    elif enable_diarization:
                        self.logger("ПРЕДУПРЕЖДЕНИЕ: Диаризация не применена, _diarize.txt не создан")

                elif fmt == 'txt_diarize_timecodes':
                    # Текст с метками спикеров (только после успешной диаризации)
                    if diarization_applied and timecoded_lines_diarized:
                        path_diarize_ts = output_path(output_dir, name_without_ext, 'txt_diarize_timecodes')
                        with open(path_diarize_ts, "w", encoding="utf-8") as f:
                            f.write("\n".join(timecoded_lines_diarized))
                        saved_files.append(path_diarize_ts)
                    elif enable_diarization:
                        self.logger("ПРЕДУПРЕЖДЕНИЕ: Диаризация не применена, _diarize_timecodes.txt не создан")

                elif fmt == 'md':
                    # Markdown формат
                    path_md = output_path(output_dir, name_without_ext, 'md')
                    md_content = self._generate_markdown(utterances, filename)
                    with open(path_md, "w", encoding="utf-8") as f:
                        f.write(md_content)
                    saved_files.append(path_md)

                elif fmt == 'srt':
                    # SRT субтитры
                    path_srt = output_path(output_dir, name_without_ext, 'srt')
                    srt_content = self._generate_srt(utterances)
                    with open(path_srt, "w", encoding="utf-8") as f:
                        f.write(srt_content)
                    saved_files.append(path_srt)

                elif fmt == 'vtt':
                    # VTT субтитры
                    path_vtt = output_path(output_dir, name_without_ext, 'vtt')
                    vtt_content = self._generate_vtt(utterances)
                    with open(path_vtt, "w", encoding="utf-8") as f:
                        f.write(vtt_content)
                    saved_files.append(path_vtt)

                self._update_progress("export", fmt_index / total_formats)

            # Проверка сохраненных данных
            if not full_text.strip():
                self.logger("ПРЕДУПРЕЖДЕНИЕ: Текст транскрипции пустой")
            else:
                self.logger(f"Сохранено символов: {len(full_text)}")

            # Успех
            result['success'] = True
            result['saved_files'] = saved_files
            result['total_time'] = time.time() - file_start_time

            # Логируем сохраненные файлы
            for saved_file in saved_files:
                self.logger(f"Сохранено: {os.path.basename(saved_file)}")
            self.logger(f"Время обработки: {self.time_formatter.format_duration(result['total_time'])} " +
                       f"(Конверсия: {round(result['conversion_time'], 1)}с, " +
                       f"Транскрибация: {round(result['transcription_time'], 1)}с)")
            self._update_progress("finalizing", 1.0)

        except Exception as e:
            result['transcription_time'] = time.time() - transcription_start
            result['total_time'] = time.time() - file_start_time

            self.logger(f"Ошибка при обработке {filename}: {str(e)}")
            import traceback
            traceback.print_exc()

        finally:
            # Удаляем только временные файлы текущего запуска; исходный файл
            # не трогаем даже если внешний converter вернул тот же путь.
            owned_temp_paths = {temp_audio, *preprocessing_temp_paths}
            for temp_path in owned_temp_paths:
                if not temp_path or os.path.abspath(temp_path) == os.path.abspath(filepath):
                    continue
                if not os.path.exists(temp_path):
                    continue
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

        return result

    def _apply_diarization(
        self,
        audio_path: str,
        utterances: list,
        num_speakers: int | None = None,
        progress_callback=None,
    ) -> list:
        """
        Применяет диаризацию к сегментам транскрипции.

        Args:
            audio_path: путь к аудио файлу
            utterances: список сегментов транскрипции
            num_speakers: количество спикеров (если известно)

        Returns:
            list: utterances с добавленной информацией о спикерах
        """
        # Проверяем, доступен ли менеджер диаризации
        if not self.diarization_manager:
            raise RuntimeError(
                "Менеджер диаризации недоступен. Проверьте HF_TOKEN (нужен доступ read)."
            )

        try:
            # Выполняем диаризацию
            kwargs = {}
            if num_speakers is not None:
                kwargs['num_speakers'] = num_speakers

            speaker_segments = self.diarization_manager.diarize(
                audio_path,
                **kwargs,
                progress_callback=progress_callback,
            )

            # Сопоставляем спикеров с сегментами транскрипции
            utterances = self.diarization_manager.map_speakers_to_transcription(
                utterances,
                speaker_segments
            )

            return utterances

        except Exception as e:
            # НЕ маскируем сбой фиктивным «Спикер №1» — пробрасываем наверх,
            # чтобы process_file показал настоящую причину (иначе пользователь
            # видит «найден 1 спикер» и думает, что диаризация сработала).
            import traceback
            self.logger(f"ОШИБКА диаризации: {e}")
            self.logger(traceback.format_exc().strip())
            raise

    def _generate_srt(self, utterances: list) -> str:
        """Генерирует контент в формате SRT субтитров (делегирует в formatters)."""
        return formatters.generate_srt(utterances)

    def _generate_vtt(self, utterances: list) -> str:
        """Генерирует контент в формате VTT субтитров (делегирует в formatters)."""
        return formatters.generate_vtt(utterances)

    def _generate_markdown(self, utterances: list, filename: str) -> str:
        """Генерирует контент в формате Markdown (делегирует в formatters)."""
        return formatters.generate_markdown(utterances, filename, self.time_formatter)
