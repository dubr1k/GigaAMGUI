"""
Модуль обработки транскрибации
"""

import os
import time
from typing import Dict, Callable, Optional
from ..utils.audio_converter import AudioConverter
from ..utils.time_formatter import TimeFormatter
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
                     estimated_conversion_ratio: float = 0.05,
                     estimated_transcription_ratio: float = 0.95) -> Dict:
        """
        Обрабатывает один файл
        
        Args:
            filepath: путь к файлу
            output_dir: папка для сохранения результатов
            file_index: индекс файла (для логирования)
            total_files: общее количество файлов
            estimated_conversion_ratio: доля времени на конвертацию (0-1)
            estimated_transcription_ratio: доля времени на транскрибацию (0-1)
            
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
        filename = os.path.basename(filepath)
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
            utterances = self.model_loader.transcribe_longform(temp_audio)
            
            result['transcription_time'] = time.time() - transcription_start
            self._update_progress('transcription', 1.0)
            
            # Формирование результатов
            full_text_lines = []
            timecoded_lines = []
            
            for utt in utterances:
                text = utt['transcription']
                start, end = utt['boundaries']
                
                full_text_lines.append(text)
                
                # Таймкоды
                ts_str = (f"[{self.time_formatter.format_timestamp(start)} - "
                         f"{self.time_formatter.format_timestamp(end)}] {text}")
                timecoded_lines.append(ts_str)
            
            # Собираем полный текст (каждая фраза с новой строки)
            full_text = "\n".join(full_text_lines)
            
            # Сохранение
            path_txt = os.path.join(output_dir, f"{name_without_ext}.txt")
            path_ts = os.path.join(output_dir, f"{name_without_ext}_timecodes.txt")
            
            with open(path_txt, "w", encoding="utf-8") as f:
                f.write(full_text)
            
            with open(path_ts, "w", encoding="utf-8") as f:
                f.write("\n".join(timecoded_lines))
            
            # Успех
            result['success'] = True
            result['total_time'] = time.time() - file_start_time
            
            self.logger(f"Сохранено: {os.path.basename(path_txt)}")
            self.logger(f"Сохранено: {os.path.basename(path_ts)}")
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