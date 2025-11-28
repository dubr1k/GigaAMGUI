"""
Главное окно приложения GigaAM v3 Transcriber
"""

import os
import threading
import time
import customtkinter as ctk
from tkinter import filedialog, messagebox

from ..config import APP_TITLE, APP_GEOMETRY, SUPPORTED_FORMATS, STATS_FILE
from ..core import ModelLoader, TranscriptionProcessor
from ..utils import ProcessingStats, TimeFormatter, AudioConverter, AppLogger, LoggerAdapter


class GigaTranscriberApp(ctk.CTk):
    """Главное окно приложения для транскрибации"""
    
    def __init__(self):
        super().__init__()

        self.title(APP_TITLE)
        self.geometry(APP_GEOMETRY)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        # Переменные
        self.files_to_process = []
        self.output_dir = ""
        self.is_processing = False
        self.start_time = None
        self.files_processed = 0
        self.total_files = 0
        self.file_estimates = {}
        self.total_estimated_time = 0
        self.time_spent = 0
        self.current_file_progress = 0.0  # Прогресс текущего файла (0-1)
        self.current_file_start_time = 0
        
        # Инициализация системы логирования
        self.app_logger = AppLogger()
        self.app_logger.log_session_start()
        
        # Инициализация модулей
        self.model_loader = ModelLoader()
        self.stats = ProcessingStats(STATS_FILE)
        self.time_formatter = TimeFormatter()

        # Элементы интерфейса
        self._create_widgets()
        
        # Очистка старых логов (старше 30 дней)
        self.app_logger.cleanup_old_logs()

    def _create_widgets(self):
        """Создание элементов интерфейса"""
        # Заголовок
        self.label_title = ctk.CTkLabel(
            self, 
            text="GigaAM v3: Транскрибация (Аудио/Видео -> Текст)", 
            font=("Roboto", 22, "bold")
        )
        self.label_title.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="ew")

        # Блок настроек
        self.frame_controls = ctk.CTkFrame(self)
        self.frame_controls.grid(row=1, column=0, padx=20, pady=10, sticky="ew")
        self.frame_controls.grid_columnconfigure(1, weight=1)

        # Кнопка выбора файлов
        self.btn_files = ctk.CTkButton(
            self.frame_controls, 
            text="1. Выбрать файлы", 
            command=self.select_files, 
            width=200
        )
        self.btn_files.grid(row=0, column=0, padx=10, pady=10)

        self.lbl_files_count = ctk.CTkLabel(
            self.frame_controls, 
            text="Файлы не выбраны", 
            text_color="gray"
        )
        self.lbl_files_count.grid(row=0, column=1, padx=10, pady=10, sticky="w")

        # Кнопка выбора папки
        self.btn_folder = ctk.CTkButton(
            self.frame_controls, 
            text="2. Папка сохранения", 
            command=self.select_folder, 
            width=200
        )
        self.btn_folder.grid(row=1, column=0, padx=10, pady=10)

        self.lbl_folder_path = ctk.CTkLabel(
            self.frame_controls, 
            text="Папка не выбрана (по умолчанию - рядом с файлом)", 
            text_color="gray"
        )
        self.lbl_folder_path.grid(row=1, column=1, padx=10, pady=10, sticky="w")

        # Кнопка старта
        self.btn_start = ctk.CTkButton(
            self, 
            text="ЗАПУСТИТЬ ОБРАБОТКУ", 
            command=self.start_processing_thread,
            fg_color="#2CC985", 
            hover_color="#26AB72",
            height=50, 
            font=("Roboto", 16, "bold")
        )
        self.btn_start.grid(row=2, column=0, padx=20, pady=20, sticky="ew")

        # Прогресс-бар и информация
        self.frame_progress = ctk.CTkFrame(self)
        self.frame_progress.grid(row=3, column=0, padx=20, pady=(0, 10), sticky="ew")
        self.frame_progress.grid_columnconfigure(0, weight=1)
        
        self.progress_bar = ctk.CTkProgressBar(self.frame_progress, height=20)
        self.progress_bar.grid(row=0, column=0, padx=10, pady=(10, 5), sticky="ew")
        self.progress_bar.set(0)
        
        self.lbl_progress = ctk.CTkLabel(
            self.frame_progress, 
            text="Готов к работе", 
            font=("Roboto", 12)
        )
        self.lbl_progress.grid(row=1, column=0, padx=10, pady=(0, 10))

        # Лог
        self.textbox_log = ctk.CTkTextbox(
            self, 
            width=800, 
            height=250, 
            font=("Consolas", 12)
        )
        self.textbox_log.grid(row=4, column=0, padx=20, pady=(0, 20), sticky="nsew")
        self.textbox_log.configure(state="disabled")

    def log(self, message):
        """Добавляет сообщение в лог (GUI и файл)"""
        # Вывод в GUI
        self.textbox_log.configure(state="normal")
        self.textbox_log.insert("end", f">> {message}\n")
        self.textbox_log.see("end")
        self.textbox_log.configure(state="disabled")
        
        # Логирование в файл
        self.app_logger.get_logger().info(message)
    
    def destroy(self):
        """Переопределяем метод закрытия для логирования завершения сессии"""
        self.app_logger.log_session_end()
        super().destroy()

    def select_files(self):
        """Обработчик выбора файлов"""
        files = filedialog.askopenfilenames(
            parent=self,
            title="Выберите аудио или видео файлы",
            filetypes=(SUPPORTED_FORMATS, ('All files', '*.*'))
        )
        if files:
            self.files_to_process = list(files)
            count = len(self.files_to_process)
            self.lbl_files_count.configure(
                text=f"Выбрано файлов: {count}", 
                text_color=("black", "white")
            )
            self.log(f"Добавлено в очередь: {count} файлов")
            for f in files:
                self.log(f" + {os.path.basename(f)}")

    def select_folder(self):
        """Обработчик выбора папки сохранения"""
        # Явно указываем родительское окно для правильной работы на macOS
        folder = filedialog.askdirectory(
            parent=self,
            title="Выберите папку для сохранения результатов",
            mustexist=True
        )
        if folder:
            self.output_dir = folder
            display_path = folder if len(folder) < 50 else f"...{folder[-50:]}"
            self.lbl_folder_path.configure(
                text=display_path, 
                text_color=("black", "white")
            )
            self.log(f"Папка для сохранения: {folder}")

    def update_progress_label(self, percent, extra_info=""):
        """Обновляет текст прогресса"""
        text = f"Прогресс: {percent}%"
        if extra_info:
            text += f" | {extra_info}"
        self.lbl_progress.configure(text=text)

    def get_file_stage_weights(self, filepath: str) -> tuple:
        """
        Определяет веса этапов обработки на основе статистики
        
        Returns:
            tuple: (conversion_weight, transcription_weight)
        """
        file_ext = os.path.splitext(filepath)[1].lower()
        
        # Получаем статистику для этого типа файла
        summary = self.stats.stats.get("summary", {})
        if file_ext in summary:
            ext_stats = summary[file_ext]
            conv_time = ext_stats.get("avg_conversion_sec", 3)
            trans_time = ext_stats.get("avg_transcription_sec", 90)
            total = conv_time + trans_time
            
            return (conv_time / total, trans_time / total)
        
        # Дефолтные веса: конвертация ~5%, транскрибация ~95%
        return (0.05, 0.95)
    
    def on_file_progress(self, stage: str, stage_progress: float):
        """
        Callback для обновления прогресса внутри файла
        
        Args:
            stage: 'conversion' или 'transcription'
            stage_progress: прогресс этапа от 0.0 до 1.0
        """
        # Получаем веса этапов для текущего файла
        if not self.files_to_process:
            return
        
        current_filepath = self.files_to_process[self.files_processed] if self.files_processed < len(self.files_to_process) else None
        if not current_filepath:
            return
        
        conv_weight, trans_weight = self.get_file_stage_weights(current_filepath)
        
        # Вычисляем прогресс текущего файла
        if stage == 'conversion':
            self.current_file_progress = stage_progress * conv_weight
        elif stage == 'transcription':
            self.current_file_progress = conv_weight + (stage_progress * trans_weight)
        
        self.update_progress_display()
    
    def update_progress_display(self):
        """Обновляет отображение прогресса на основе текущего состояния"""
        if self.total_estimated_time <= 0:
            return
        
        # Время, уже потраченное на завершенные файлы
        completed_time = self.time_spent
        
        # Добавляем прогресс текущего файла
        if self.files_processed < len(self.files_to_process):
            current_filepath = self.files_to_process[self.files_processed]
            estimated_current = self.file_estimates.get(current_filepath, 30)
            current_progress_time = estimated_current * self.current_file_progress
            total_progress_time = completed_time + current_progress_time
        else:
            total_progress_time = completed_time
        
        # Общий прогресс
        progress = min(total_progress_time / self.total_estimated_time, 0.99)
        self.progress_bar.set(progress)
        percent = int(progress * 100)
        
        # Оставшееся время
        remaining_time = max(0, self.total_estimated_time - total_progress_time)
        
        if remaining_time > 0:
            time_info = f"Осталось: ~{self.time_formatter.format_duration(remaining_time)}"
            self.update_progress_label(percent, time_info)
        else:
            self.update_progress_label(percent, "Завершение...")
    
    def update_progress(self, current_file_index):
        """
        Обновляет прогресс-бар (вызывается при начале обработки файла)
        
        Args:
            current_file_index: индекс текущего файла
        """
        self.current_file_progress = 0.0
        self.update_progress_display()

    def start_processing_thread(self):
        """Запускает обработку в отдельном потоке"""
        if self.is_processing:
            return
        
        if not self.files_to_process:
            self.app_logger.get_logger().warning("Попытка запустить обработку без выбранных файлов")
            messagebox.showwarning("Внимание", "Выберите хотя бы один файл для обработки!")
            return
        
        # Если папка не выбрана, используем директорию первого файла
        if not self.output_dir:
            if self.files_to_process:
                self.output_dir = os.path.dirname(self.files_to_process[0])
                self.log(f"Папка не выбрана. Использую директорию первого файла: {self.output_dir}")
        
        self.is_processing = True
        self.start_time = time.time()
        self.files_processed = 0
        self.total_files = len(self.files_to_process)
        self.time_spent = 0
        
        # Получаем длительность файлов для точной оценки времени
        self.log("Анализ файлов и оценка времени обработки...")
        files_with_durations = []
        for filepath in self.files_to_process:
            try:
                # Получаем длительность через ffprobe
                duration = AudioConverter.get_media_duration(filepath)
                if duration > 0:
                    self.log(f"  {os.path.basename(filepath)}: {int(duration//60)}:{int(duration%60):02d}")
                    files_with_durations.append((filepath, duration))
                else:
                    # Если не удалось получить длительность, используем дефолтное значение
                    self.log(f"  {os.path.basename(filepath)}: длительность неизвестна (используется оценка)")
                    files_with_durations.append((filepath, 60))  # Дефолт: 1 минута
            except Exception as e:
                self.log(f"  {os.path.basename(filepath)}: ошибка определения длительности")
                files_with_durations.append((filepath, 60))
        
        # Получаем оценки времени обработки
        batch_estimate = self.stats.estimate_batch_time(files_with_durations)
        self.file_estimates = batch_estimate["per_file"]
        self.total_estimated_time = batch_estimate["total_seconds"]
        
        estimate_str = self.time_formatter.format_duration(self.total_estimated_time)
        self.log(f"Ожидаемое время обработки: ~{estimate_str}")
        
        self.btn_start.configure(
            state="disabled", 
            text="ИДЕТ ОБРАБОТКА...", 
            fg_color="gray"
        )
        self.progress_bar.set(0)
        self.update_progress_label(0, f"Оценка: ~{estimate_str}")
        
        thread = threading.Thread(target=self.process_files)
        thread.start()

    def process_files(self):
        """Основной процесс обработки файлов"""
        try:
            # Загружаем модель
            if not self.model_loader.load_model(self.log):
                self.is_processing = False
                self.btn_start.configure(
                    state="normal", 
                    text="ЗАПУСТИТЬ ОБРАБОТКУ", 
                    fg_color="#2CC985"
                )
                return
            
            # Создаем процессор с callback для прогресса
            processor = TranscriptionProcessor(
                self.model_loader,
                self.stats,
                self.log,
                progress_callback=self.on_file_progress
            )
            
            # Обрабатываем каждый файл
            for i, filepath in enumerate(self.files_to_process):
                try:
                    self.current_file_start_time = time.time()
                    self.update_progress(i)
                    
                    # Получаем веса этапов для точной передачи процессору
                    conv_weight, trans_weight = self.get_file_stage_weights(filepath)
                    
                    result = processor.process_file(
                        filepath,
                        self.output_dir,
                        i,
                        self.total_files,
                        estimated_conversion_ratio=conv_weight,
                        estimated_transcription_ratio=trans_weight
                    )
                    
                    # Сохраняем статистику обработки
                    self.stats.add_processing_record(
                        file_path=result['file_path'],
                        file_size=result['file_size'],
                        duration=result.get('media_duration', 0),
                        conversion_time=result['conversion_time'],
                        transcription_time=result['transcription_time'],
                        success=result['success']
                    )
                    
                    # Обновляем счетчики
                    if result['success']:
                        self.files_processed += 1
                    
                    self.time_spent += result['total_time']
                    
                except Exception as e:
                    error_msg = f"Ошибка при обработке файла {os.path.basename(filepath)}: {str(e)}"
                    self.log(error_msg)
                    self.app_logger.get_logger().error(error_msg, exc_info=True)
                    continue
                
        except Exception as e:
            error_msg = f"Критическая ошибка в процессе обработки: {str(e)}"
            self.log(error_msg)
            self.app_logger.get_logger().error(error_msg, exc_info=True)
            self.is_processing = False
            self.btn_start.configure(
                state="normal", 
                text="ЗАПУСТИТЬ ОБРАБОТКУ", 
                fg_color="#2CC985"
            )
            return
        
        # Финальное обновление прогресса
        self.progress_bar.set(1.0)
        total_elapsed = time.time() - self.start_time
        
        self.log("=== ВСЕ ФАЙЛЫ ОБРАБОТАНЫ ===")
        self.log(f"Общее время обработки: {self.time_formatter.format_duration(total_elapsed)}")
        self.log(f"Ожидалось: ~{self.time_formatter.format_duration(self.total_estimated_time)}")
        self.log(f"Обработано файлов: {self.files_processed}/{self.total_files}")
        self.log("")
        self.log("Статистика сохранена. Следующие обработки будут иметь более точные прогнозы.")
        
        self.update_progress_label(
            100, 
            f"Завершено за {self.time_formatter.format_duration(total_elapsed)}"
        )
        
        self.is_processing = False
        self.btn_start.configure(
            state="normal", 
            text="ЗАПУСТИТЬ ОБРАБОТКУ", 
            fg_color="#2CC985"
        )
        
        # Логируем завершение
        self.app_logger.get_logger().info(
            f"Обработка завершена. Обработано файлов: {self.files_processed}/{self.total_files}. "
            f"Время: {self.time_formatter.format_duration(total_elapsed)}"
        )
        
        messagebox.showinfo(
            "Готово", 
            f"Обработка завершена!\nВремя: {self.time_formatter.format_duration(total_elapsed)}"
        )