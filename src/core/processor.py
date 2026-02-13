"""
Модуль обработки транскрибации
"""

import os
import time
import warnings
from typing import Dict, Callable, Optional
from ..utils.audio_converter import AudioConverter
from ..utils.time_formatter import TimeFormatter
from ..utils.diarization import DiarizationManager
from ..config import HF_TOKEN


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
        self.time_formatter = TimeFormatter()
        self._diarization_manager = None
        
    @property
    def diarization_manager(self) -> Optional[DiarizationManager]:
        """Ленивая загрузка менеджера диаризации"""
        if self._diarization_manager is None and HF_TOKEN:
            try:
                self._diarization_manager = DiarizationManager(
                    hf_token=HF_TOKEN,
                    device="auto"
                )
            except Exception as e:
                self.logger(f"Не удалось инициализировать менеджер диаризации: {e}")
        return self._diarization_manager
    
    def _update_progress(self, stage: str, progress: float):
        """
        Обновляет прогресс через callback
        
        Args:
            stage: этап обработки ('conversion' или 'transcription')
            progress: прогресс этапа от 0.0 до 1.0
        """
        if self.progress_callback:
            self.progress_callback(stage, progress)
    
    def process_file(self,
                     filepath: str,
                     output_dir: str,
                     file_index: int,
                     total_files: int,
                     original_filename: Optional[str] = None,
                     estimated_conversion_ratio: float = 0.05,
                     estimated_transcription_ratio: float = 0.95,
                     enable_diarization: bool = False,
                     num_speakers: Optional[int] = None,
                     output_formats: Optional[list] = None) -> Dict:
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
            'transcription_time': 0
        }
        
        # Логирование начала
        estimated_time = self.stats.estimate_processing_time(filepath, media_duration)
        duration_str = f"{int(media_duration//60)}:{int(media_duration%60):02d}" if media_duration > 0 else "неизвестна"
        self.logger(f"--- Обработка файла {file_index+1}/{total_files}: {filename} ---")
        self.logger(f"Длительность: {duration_str} | "
                   f"Оценка времени обработки: ~{self.time_formatter.format_duration(estimated_time)}")
        
        # Конвертация
        self._update_progress('conversion', 0.0)
        conversion_start = time.time()
        temp_audio = self.audio_converter.convert_to_wav(filepath, output_dir)
        result['conversion_time'] = time.time() - conversion_start
        self._update_progress('conversion', 1.0)
        
        if not temp_audio:
            self.logger(f"Пропуск файла {filename}")
            result['total_time'] = time.time() - file_start_time
            return result
        
        # Транскрибация
        transcription_start = time.time()
        try:
            # Проверяем токен
            if not HF_TOKEN or not HF_TOKEN.startswith("hf_"):
                error_msg = (
                    "ОШИБКА: Требуется валидный токен HuggingFace для longform транскрибации!\n"
                    "Установите ваш токен в src/config.py\n"
                    "Получить токен: https://huggingface.co/settings/tokens\n"
                    "Принять условия доступа: https://huggingface.co/pyannote/segmentation-3.0"
                )
                self.logger(error_msg)
                return result
            
            self.logger("Распознавание речи (GigaAM-v3)...")
            self._update_progress('transcription', 0.0)
            
            # Транскрибация (обновляем прогресс постепенно)
            try:
                utterances = self.model_loader.transcribe_longform(temp_audio)
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
            if enable_diarization and utterances and len(utterances) > 0:
                self.logger("Применение диаризации спикеров...")
                try:
                    utterances = self._apply_diarization(
                        temp_audio,
                        utterances,
                        num_speakers=num_speakers
                    )
                    self.logger(f"Диаризация завершена. Найдено спикеров: {len(set(u.get('speaker', 'Неизвестный спикер') for u in utterances))}")
                except Exception as e:
                    self.logger(f"ПРЕДУПРЕЖДЕНИЕ: Ошибка при диаризации: {e}")
                    self.logger("Продолжаем без диаризации...")
                    # Добавляем дефолтное имя спикера
                    for utt in utterances:
                        utt['speaker'] = "Спикер №1"
            
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
                    
                    # Диаризованный текст — с метками спикеров (если диаризация включена)
                    if enable_diarization and speaker:
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
                
                # Обычный текст — всегда основной для .txt и _timecodes.txt
                full_text = "\n".join(full_text_lines_plain)
                timecoded_lines = timecoded_lines_plain
                
                # Диаризованный текст — для _diarize.txt и _diarize_timecodes.txt (при enable_diarization)
                full_text_diarized = "\n".join(full_text_lines_diarized)
                
                if not full_text.strip():
                    self.logger(f"ПРЕДУПРЕЖДЕНИЕ: Все сегменты имеют пустой текст транскрипции")
            
            # Определяем форматы вывода (по умолчанию txt)
            if output_formats is None:
                output_formats = ['txt']
            
            # Сохранение в выбранных форматах
            saved_files = []
            
            for fmt in output_formats:
                if fmt == 'txt':
                    # Обычный текстовый файл (всегда без диаризации)
                    path_txt = os.path.join(output_dir, f"{name_without_ext}.txt")
                    with open(path_txt, "w", encoding="utf-8") as f:
                        f.write(full_text)
                    saved_files.append(path_txt)
                    
                    # Файл с таймкодами (всегда вместе с txt, без меток спикеров)
                    path_ts = os.path.join(output_dir, f"{name_without_ext}_timecodes.txt")
                    with open(path_ts, "w", encoding="utf-8") as f:
                        f.write("\n".join(timecoded_lines))
                    saved_files.append(path_ts)
                    
                    # Диаризованные файлы (только при включённой диаризации)
                    if enable_diarization and full_text_diarized.strip():
                        path_diarize = os.path.join(output_dir, f"{name_without_ext}_diarize.txt")
                        with open(path_diarize, "w", encoding="utf-8") as f:
                            f.write(full_text_diarized)
                        saved_files.append(path_diarize)
                        
                        path_diarize_ts = os.path.join(output_dir, f"{name_without_ext}_diarize_timecodes.txt")
                        with open(path_diarize_ts, "w", encoding="utf-8") as f:
                            f.write("\n".join(timecoded_lines_diarized))
                        saved_files.append(path_diarize_ts)
                    
                elif fmt == 'md':
                    # Markdown формат
                    path_md = os.path.join(output_dir, f"{name_without_ext}.md")
                    md_content = self._generate_markdown(utterances, filename)
                    with open(path_md, "w", encoding="utf-8") as f:
                        f.write(md_content)
                    saved_files.append(path_md)
                    
                elif fmt == 'srt':
                    # SRT субтитры
                    path_srt = os.path.join(output_dir, f"{name_without_ext}.srt")
                    srt_content = self._generate_srt(utterances)
                    with open(path_srt, "w", encoding="utf-8") as f:
                        f.write(srt_content)
                    saved_files.append(path_srt)
                    
                elif fmt == 'vtt':
                    # VTT субтитры
                    path_vtt = os.path.join(output_dir, f"{name_without_ext}.vtt")
                    vtt_content = self._generate_vtt(utterances)
                    with open(path_vtt, "w", encoding="utf-8") as f:
                        f.write(vtt_content)
                    saved_files.append(path_vtt)
            
            # Проверка сохраненных данных
            if not full_text.strip():
                self.logger(f"ПРЕДУПРЕЖДЕНИЕ: Текст транскрипции пустой")
            else:
                self.logger(f"Сохранено символов: {len(full_text)}")
            
            # Успех
            result['success'] = True
            result['total_time'] = time.time() - file_start_time
            
            # Логируем сохраненные файлы
            for saved_file in saved_files:
                self.logger(f"Сохранено: {os.path.basename(saved_file)}")
            self.logger(f"Время обработки: {self.time_formatter.format_duration(result['total_time'])} " +
                       f"(Конверсия: {round(result['conversion_time'], 1)}с, " +
                       f"Транскрибация: {round(result['transcription_time'], 1)}с)")
            
        except Exception as e:
            result['transcription_time'] = time.time() - transcription_start
            result['total_time'] = time.time() - file_start_time
            
            self.logger(f"Ошибка при обработке {filename}: {str(e)}")
            import traceback
            traceback.print_exc()
            
        finally:
            # Чистка временного файла
            if temp_audio and os.path.exists(temp_audio):
                try:
                    os.remove(temp_audio)
                except:
                    pass
        
        return result
    
    def _apply_diarization(
        self,
        audio_path: str,
        utterances: list,
        num_speakers: Optional[int] = None
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
            self.logger("ПРЕДУПРЕЖДЕНИЕ: Менеджер диаризации недоступен. Проверьте HF_TOKEN.")
            # Добавляем дефолтное имя спикера
            for utt in utterances:
                utt['speaker'] = "Спикер №1"
            return utterances
        
        try:
            # Выполняем диаризацию
            kwargs = {}
            if num_speakers is not None:
                kwargs['num_speakers'] = num_speakers
            
            speaker_segments = self.diarization_manager.diarize(
                audio_path,
                **kwargs
            )
            
            # Сопоставляем спикеров с сегментами транскрипции
            utterances = self.diarization_manager.map_speakers_to_transcription(
                utterances,
                speaker_segments
            )
            
            return utterances
            
        except Exception as e:
            self.logger(f"Ошибка при применении диаризации: {e}")
            # В случае ошибки добавляем дефолтное имя спикера
            for utt in utterances:
                if 'speaker' not in utt:
                    utt['speaker'] = "Спикер №1"
            return utterances
    
    def _format_srt_timestamp(self, seconds: float) -> str:
        """
        Форматирует время в формат SRT (HH:MM:SS,mmm)
        
        Args:
            seconds: время в секундах
            
        Returns:
            str: отформатированное время
        """
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds - int(seconds)) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
    
    def _format_vtt_timestamp(self, seconds: float) -> str:
        """
        Форматирует время в формат VTT (HH:MM:SS.mmm)
        
        Args:
            seconds: время в секундах
            
        Returns:
            str: отформатированное время
        """
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds - int(seconds)) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"
    
    def _generate_srt(self, utterances: list) -> str:
        """
        Генерирует контент в формате SRT субтитров
        
        Args:
            utterances: список сегментов транскрипции
            
        Returns:
            str: контент в формате SRT
        """
        lines = []
        index = 1
        
        for utt in utterances:
            text = utt.get('transcription', '')
            if not text or not text.strip():
                continue
                
            boundaries = utt.get('boundaries', (0.0, 0.0))
            start, end = boundaries
            speaker = utt.get('speaker', None)
            
            lines.append(str(index))
            lines.append(f"{self._format_srt_timestamp(start)} --> {self._format_srt_timestamp(end)}")
            
            # Добавляем имя спикера, если есть
            if speaker:
                lines.append(f"<{speaker}> {text}")
            else:
                lines.append(text)
            
            lines.append("")  # Пустая строка между субтитрами
            index += 1
        
        return "\n".join(lines)
    
    def _generate_vtt(self, utterances: list) -> str:
        """
        Генерирует контент в формате VTT субтитров
        
        Args:
            utterances: список сегментов транскрипции
            
        Returns:
            str: контент в формате VTT
        """
        lines = ["WEBVTT", ""]  # Заголовок VTT
        
        for utt in utterances:
            text = utt.get('transcription', '')
            if not text or not text.strip():
                continue
                
            boundaries = utt.get('boundaries', (0.0, 0.0))
            start, end = boundaries
            speaker = utt.get('speaker', None)
            
            lines.append(f"{self._format_vtt_timestamp(start)} --> {self._format_vtt_timestamp(end)}")
            
            # Добавляем имя спикера, если есть
            if speaker:
                lines.append(f"<v {speaker}>{text}")
            else:
                lines.append(text)
            
            lines.append("")  # Пустая строка между субтитрами
        
        return "\n".join(lines)
    
    def _generate_markdown(self, utterances: list, filename: str) -> str:
        """
        Генерирует контент в формате Markdown
        
        Args:
            utterances: список сегментов транскрипции
            filename: имя исходного файла
            
        Returns:
            str: контент в формате Markdown
        """
        lines = [
            f"# Транскрипция: {filename}",
            "",
            f"*Создано с помощью GigaAM v3 Transcriber*",
            "",
            "---",
            ""
        ]
        
        current_speaker = None
        
        for utt in utterances:
            text = utt.get('transcription', '')
            if not text or not text.strip():
                continue
                
            boundaries = utt.get('boundaries', (0.0, 0.0))
            start, end = boundaries
            speaker = utt.get('speaker', None)
            
            # Форматируем время
            time_str = f"`{self.time_formatter.format_timestamp(start)} - {self.time_formatter.format_timestamp(end)}`"
            
            if speaker:
                # Если спикер изменился, добавляем заголовок
                if speaker != current_speaker:
                    lines.append("")
                    lines.append(f"### {speaker}")
                    lines.append("")
                    current_speaker = speaker
                
                lines.append(f"- {time_str} {text}")
            else:
                lines.append(f"- {time_str} {text}")
        
        return "\n".join(lines)