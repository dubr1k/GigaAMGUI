"""
Главное окно приложения GigaAM v3 Transcriber
"""

import os
import threading
import time
import customtkinter as ctk
from tkinter import filedialog, messagebox

from ..config import APP_TITLE, APP_GEOMETRY, SUPPORTED_FORMATS, STATS_FILE, OUTPUT_FORMATS
from ..core import ModelLoader, TranscriptionProcessor
from ..utils import ProcessingStats, TimeFormatter, AudioConverter, AppLogger, LoggerAdapter, UserSettings


class GigaTranscriberApp(ctk.CTk):
    """Главное окно приложения для транскрибации"""

    def __init__(self):
        super().__init__()

        self.title(APP_TITLE)
        self.geometry(APP_GEOMETRY)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(7, weight=1)  # Лог растягивается при изменении размера окна

        # Переменные
        self.files_to_process = []
        self.output_dir = ""
        self.input_dir = ""  # Папка для выбора файлов
        self.is_processing = False
        self.start_time = None
        self.files_processed = 0
        self.total_files = 0
        self.file_estimates = {}
        self.total_estimated_time = 0
        self.time_spent = 0
        self.current_file_start_time = 0
        self.progress_update_timer = None  # Таймер для периодического обновления прогресса
        self.current_stage = None  # Текущий этап обработки ('conversion' или 'transcription')
        self.current_stage_progress = 0.0  # Прогресс текущего этапа
        
        # Настройки диаризации
        self.enable_diarization = False
        self.num_speakers = None
        
        # Настройки выходных форматов (по умолчанию txt)
        self.output_formats = {'txt': True, 'md': False, 'srt': False, 'vtt': False}

        # Инициализация системы логирования
        self.app_logger = AppLogger()
        self.app_logger.log_session_start()

        # Инициализация модулей
        self.model_loader = ModelLoader()
        self.stats = ProcessingStats(STATS_FILE)
        self.time_formatter = TimeFormatter()
        self.user_settings = UserSettings()

        # Загружаем сохраненные пути для входной и выходной папок
        saved_output_dir = self.user_settings.get_last_output_dir()
        saved_input_dir = self.user_settings.get_last_files_dir()
        
        if saved_output_dir:
            self.output_dir = saved_output_dir
        
        if saved_input_dir:
            self.input_dir = saved_input_dir

        # Элементы интерфейса
        self._create_widgets()

        # Обновляем метки папок, если пути были загружены
        if saved_output_dir:
            display_output_path = saved_output_dir if len(saved_output_dir) < 50 else f"...{saved_output_dir[-50:]}"
            self.lbl_folder_path.configure(
                text=display_output_path,
                text_color=("black", "white")
            )
        
        if saved_input_dir:
            display_input_path = saved_input_dir if len(saved_input_dir) < 50 else f"...{saved_input_dir[-50:]}"
            self.lbl_input_folder_path.configure(
                text=display_input_path,
                text_color=("black", "white")
            )

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

        # Блок настроек - выбор файлов
        self.frame_files = ctk.CTkFrame(self)
        self.frame_files.grid(row=1, column=0, padx=20, pady=(10, 5), sticky="ew")
        self.frame_files.grid_columnconfigure(1, weight=1)
        self.frame_files.grid_columnconfigure(3, weight=1)

        # Кнопка выбора файлов
        self.btn_files = ctk.CTkButton(
            self.frame_files,
            text="1.1. Выбрать файлы",
            command=self.select_files,
            width=180
        )
        self.btn_files.grid(row=0, column=0, padx=(10, 5), pady=10)

        self.lbl_files_count = ctk.CTkLabel(
            self.frame_files,
            text="Файлы не выбраны",
            text_color="gray"
        )
        self.lbl_files_count.grid(row=0, column=1, padx=5, pady=10, sticky="w")

        # Кнопка выбора папки с файлами
        self.btn_folder_input = ctk.CTkButton(
            self.frame_files,
            text="1.2. Выбрать папку с файлами",
            command=self.select_files_folder,
            width=180
        )
        self.btn_folder_input.grid(row=0, column=2, padx=(10, 5), pady=10)

        self.lbl_input_folder_path = ctk.CTkLabel(
            self.frame_files,
            text="Папка не выбрана",
            text_color="gray"
        )
        self.lbl_input_folder_path.grid(row=0, column=3, padx=5, pady=10, sticky="w")

        # Блок настроек - папка сохранения
        self.frame_output = ctk.CTkFrame(self)
        self.frame_output.grid(row=2, column=0, padx=20, pady=(5, 10), sticky="ew")
        self.frame_output.grid_columnconfigure(1, weight=1)

        self.btn_folder = ctk.CTkButton(
            self.frame_output,
            text="2. Папка сохранения",
            command=self.select_folder,
            width=180
        )
        self.btn_folder.grid(row=0, column=0, padx=(10, 5), pady=10)

        self.lbl_folder_path = ctk.CTkLabel(
            self.frame_output,
            text="Папка не выбрана (по умолчанию - рядом с файлом)",
            text_color="gray"
        )
        self.lbl_folder_path.grid(row=0, column=1, padx=5, pady=10, sticky="w")
        
        # Блок настроек - диаризация
        self.frame_diarization = ctk.CTkFrame(self)
        self.frame_diarization.grid(row=3, column=0, padx=20, pady=(5, 10), sticky="ew")
        self.frame_diarization.grid_columnconfigure(1, weight=1)
        
        self.checkbox_diarization = ctk.CTkCheckBox(
            self.frame_diarization,
            text="3. Включить диаризацию спикеров",
            command=self.toggle_diarization,
            width=180
        )
        self.checkbox_diarization.grid(row=0, column=0, padx=(10, 5), pady=10)
        
        self.lbl_diarization_info = ctk.CTkLabel(
            self.frame_diarization,
            text="Автоматическое определение спикеров (требуется HF_TOKEN)",
            text_color="gray"
        )
        self.lbl_diarization_info.grid(row=0, column=1, padx=5, pady=10, sticky="w")
        
        # Поле для ввода количества спикеров
        self.lbl_num_speakers = ctk.CTkLabel(
            self.frame_diarization,
            text="Количество спикеров (опционально):",
            text_color="gray"
        )
        self.lbl_num_speakers.grid(row=1, column=0, padx=(10, 5), pady=(0, 10), sticky="w")
        
        self.entry_num_speakers = ctk.CTkEntry(
            self.frame_diarization,
            placeholder_text="Оставьте пустым для автоопределения",
            width=250,
            state="disabled"
        )
        self.entry_num_speakers.grid(row=1, column=1, padx=5, pady=(0, 10), sticky="w")
        
        # Блок настроек - выходные форматы
        self.frame_formats = ctk.CTkFrame(self)
        self.frame_formats.grid(row=4, column=0, padx=20, pady=(5, 10), sticky="ew")
        
        self.lbl_formats = ctk.CTkLabel(
            self.frame_formats,
            text="4. Форматы вывода:",
            font=("Roboto", 12)
        )
        self.lbl_formats.grid(row=0, column=0, padx=(10, 10), pady=10, sticky="w")
        
        # Чекбоксы для форматов
        self.format_checkboxes = {}
        col = 1
        for fmt, label in OUTPUT_FORMATS.items():
            var = ctk.BooleanVar(value=(fmt == 'txt'))  # txt по умолчанию включен
            cb = ctk.CTkCheckBox(
                self.frame_formats,
                text=label,
                variable=var,
                command=lambda f=fmt: self.toggle_format(f),
                width=140
            )
            cb.grid(row=0, column=col, padx=5, pady=10)
            self.format_checkboxes[fmt] = {'checkbox': cb, 'var': var}
            col += 1

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
        self.btn_start.grid(row=5, column=0, padx=20, pady=20, sticky="ew")

        # Прогресс-бар и информация
        self.frame_progress = ctk.CTkFrame(self)
        self.frame_progress.grid(row=6, column=0, padx=20, pady=(0, 10), sticky="ew")
        self.frame_progress.grid_columnconfigure(0, weight=1)
        
        # Заголовок блока прогресса
        self.lbl_progress_title = ctk.CTkLabel(
            self.frame_progress,
            text="Прогресс обработки",
            font=("Roboto", 14, "bold")
        )
        self.lbl_progress_title.grid(row=0, column=0, padx=10, pady=(10, 5), sticky="w")
        
        # Общий прогресс (все файлы)
        self.frame_total_progress = ctk.CTkFrame(self.frame_progress, fg_color="transparent")
        self.frame_total_progress.grid(row=1, column=0, padx=10, pady=(5, 2), sticky="ew")
        self.frame_total_progress.grid_columnconfigure(1, weight=1)
        
        self.lbl_total_progress = ctk.CTkLabel(
            self.frame_total_progress,
            text="Всего:",
            font=("Roboto", 12),
            width=80,
            anchor="w"
        )
        self.lbl_total_progress.grid(row=0, column=0, padx=(0, 5), sticky="w")
        
        self.progress_bar = ctk.CTkProgressBar(self.frame_total_progress, height=20)
        self.progress_bar.grid(row=0, column=1, padx=(0, 10), sticky="ew")
        self.progress_bar.set(0)
        
        self.lbl_total_percent = ctk.CTkLabel(
            self.frame_total_progress,
            text="0%",
            font=("Roboto", 12, "bold"),
            width=50
        )
        self.lbl_total_percent.grid(row=0, column=2, padx=(5, 0), sticky="e")
        
        # Прогресс текущего файла
        self.frame_file_progress = ctk.CTkFrame(self.frame_progress, fg_color="transparent")
        self.frame_file_progress.grid(row=2, column=0, padx=10, pady=(2, 5), sticky="ew")
        self.frame_file_progress.grid_columnconfigure(1, weight=1)
        
        self.lbl_file_progress = ctk.CTkLabel(
            self.frame_file_progress,
            text="Файл:",
            font=("Roboto", 12),
            width=80,
            anchor="w"
        )
        self.lbl_file_progress.grid(row=0, column=0, padx=(0, 5), sticky="w")
        
        self.progress_bar_file = ctk.CTkProgressBar(self.frame_file_progress, height=14)
        self.progress_bar_file.grid(row=0, column=1, padx=(0, 10), sticky="ew")
        self.progress_bar_file.set(0)
        self.progress_bar_file.configure(progress_color="#5DADE2")  # Другой цвет для текущего файла
        
        self.lbl_file_percent = ctk.CTkLabel(
            self.frame_file_progress,
            text="0%",
            font=("Roboto", 11),
            width=50
        )
        self.lbl_file_percent.grid(row=0, column=2, padx=(5, 0), sticky="e")
        
        # Информация о текущем файле
        self.lbl_current_file = ctk.CTkLabel(
            self.frame_progress,
            text="",
            font=("Roboto", 11),
            text_color="gray"
        )
        self.lbl_current_file.grid(row=3, column=0, padx=10, pady=(0, 2), sticky="w")
        
        # Статус и время
        self.lbl_progress = ctk.CTkLabel(
            self.frame_progress,
            text="Готов к работе",
            font=("Roboto", 12)
        )
        self.lbl_progress.grid(row=4, column=0, padx=10, pady=(2, 10))

        # Лог
        self.textbox_log = ctk.CTkTextbox(
            self,
            width=800,
            height=250,
            font=("Consolas", 12)
        )
        self.textbox_log.grid(row=7, column=0, padx=20, pady=(0, 10), sticky="nsew")
        self.textbox_log.configure(state="disabled")

        self.frame_file_buttons = ctk.CTkFrame(self)
        self.frame_file_buttons.grid(row=8, column=0, padx=20, pady=(0, 20), sticky="ew")
        self.frame_file_buttons.grid_columnconfigure((0, 1), weight=1)

        # Кнопка очистки выбора
        self.btn_clear_all = ctk.CTkButton(
            self.frame_file_buttons,
            text="Очистить все",
            command=self.clear_all,
            fg_color="#f44336",
            hover_color="#cc0000",
            width=1000,
            height=50,
            font=("Roboto", 16, "bold")
        )
        self.btn_clear_all.grid(row=0, column=0, padx=20, pady=20, sticky="ew")

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
        # Сохраняем текущие пути для входной и выходной папок перед закрытием
        if self.output_dir:
            self.user_settings.set_last_output_dir(self.output_dir)
        if self.input_dir:
            self.user_settings.set_last_files_dir(self.input_dir)
        self.app_logger.log_session_end()
        super().destroy()

    def select_files(self):
        """Обработчик выбора файлов"""
        # Получаем последний использованный путь для выбора файлов
        initialdir = self.user_settings.get_last_files_dir()
        if not initialdir and self.input_dir:
            initialdir = self.input_dir

        files = filedialog.askopenfilenames(
            parent=self,
            title="Выберите аудио или видео файлы",
            filetypes=(SUPPORTED_FORMATS, ('All files', '*.*')),
            initialdir=initialdir
        )
        if files:
            self.files_to_process = list(files)
            count = len(self.files_to_process)
            # Сохраняем путь первого выбранного файла для следующего раза
            if files:
                file_dir = os.path.dirname(files[0])
                self.input_dir = file_dir
                self.user_settings.set_last_files_dir(files[0])
            self.lbl_files_count.configure(
                text=f"Выбрано файлов: {count}",
                text_color=("black", "white")
            )
            self.log(f"Добавлено в очередь: {count} файлов")
            for f in files:
                self.log(f" + {os.path.basename(f)}")

    def select_files_folder(self):
        """Обработчик выбора папки с файлами"""
        # Получаем последний использованный путь для выбора файлов
        initial_dir = self.user_settings.get_last_files_dir()
        if not initial_dir and self.input_dir:
            initial_dir = self.input_dir
        if not initial_dir:
            initial_dir = os.path.expanduser("~")

        folder = filedialog.askdirectory(
            parent=self,
            title="Выберите папку с аудио/видео файлами",
            initialdir=initial_dir,
            mustexist=True
        )
        if folder:
            self.input_dir = folder
            # Сохраняем выбранный путь для следующего запуска
            self.user_settings.set_last_files_dir(folder)

            display_path = folder if len(folder) < 50 else f"...{folder[-50:]}"
            self.lbl_input_folder_path.configure(
                text=display_path,
                text_color=("black", "white")
            )

            ext_str = SUPPORTED_FORMATS[1]
            extensions = [ext.replace('*', '').lower() for ext in ext_str.split()]

            files = [
                os.path.join(folder, f)
                for f in os.listdir(folder)
                if os.path.isfile(os.path.join(folder, f)) and f.lower().endswith(tuple(extensions))
            ]

            if files:
                self.files_to_process = files
                count = len(files)
                self.lbl_files_count.configure(
                    text=f"Выбрано файлов: {count}",
                    text_color=("black", "white")
                )
                self.log(f"Добавлено из папки: {count} файлов")
                for f in files:
                    self.log(f" + {os.path.basename(f)}")
            else:
                messagebox.showinfo("Информация", "В выбранной папке нет поддерживаемых файлов")

    def select_folder(self):
        """Обработчик выбора папки сохранения"""
        # Получаем последний использованный путь для сохранения
        initialdir = self.user_settings.get_last_output_dir()
        if not initialdir and self.output_dir:
            initialdir = self.output_dir

        # Явно указываем родительское окно для правильной работы на macOS
        folder = filedialog.askdirectory(
            parent=self,
            title="Выберите папку для сохранения результатов",
            mustexist=True,
            initialdir=initialdir
        )
        if folder:
            self.output_dir = folder
            # Сохраняем выбранный путь для следующего запуска
            self.user_settings.set_last_output_dir(folder)
            display_path = folder if len(folder) < 50 else f"...{folder[-50:]}"
            self.lbl_folder_path.configure(
                text=display_path,
                text_color=("black", "white")
            )
            self.log(f"Папка для сохранения: {folder} (установлена как папка по умолчанию)")

    def toggle_diarization(self):
        """Обработчик изменения состояния чекбокса диаризации"""
        self.enable_diarization = self.checkbox_diarization.get()
        
        # Активируем/деактивируем поле ввода количества спикеров
        if self.enable_diarization:
            self.entry_num_speakers.configure(state="normal")
            self.log("Диаризация спикеров: ВКЛЮЧЕНА")
        else:
            self.entry_num_speakers.configure(state="disabled")
            self.entry_num_speakers.delete(0, "end")
            self.log("Диаризация спикеров: ВЫКЛЮЧЕНА")
    
    def toggle_format(self, fmt: str):
        """Обработчик изменения состояния чекбокса формата"""
        if fmt in self.format_checkboxes:
            self.output_formats[fmt] = self.format_checkboxes[fmt]['var'].get()
            
            # Проверяем, что хотя бы один формат выбран
            if not any(self.output_formats.values()):
                # Возвращаем txt по умолчанию
                self.output_formats['txt'] = True
                self.format_checkboxes['txt']['var'].set(True)
                self.log("ПРЕДУПРЕЖДЕНИЕ: Выбран хотя бы один формат по умолчанию (txt)")
    
    def get_selected_formats(self) -> list:
        """Возвращает список выбранных форматов"""
        return [fmt for fmt, enabled in self.output_formats.items() if enabled]
    
    def clear_all(self):
        """Сбрасывает все выбранные файлы, папки и состояние интерфейса"""
        # Предупреждение, если идет обработка
        if self.is_processing:
            if not messagebox.askyesno(
                "Внимание",
                "Идет обработка файлов. Вы уверены, что хотите сбросить все настройки?"
            ):
                return
        
        # Останавливаем обработку, если она идет
        if self.is_processing:
            self.is_processing = False
            self._stop_progress_updates()
            self.btn_start.configure(
                state="normal",
                text="ЗАПУСТИТЬ ОБРАБОТКУ",
                fg_color="#2CC985"
            )
        
        # Очищаем список файлов
        self.files_to_process = []
        
        # Сбрасываем папки (но сохраняем последние использованные в настройках)
        self.output_dir = ""
        self.input_dir = ""
        
        # Сбрасываем состояние обработки
        self.files_processed = 0
        self.total_files = 0
        self.time_spent = 0
        self.current_file_start_time = 0
        self.start_time = None
        self.file_estimates = {}
        self.total_estimated_time = 0
        self.current_stage = None
        self.current_stage_progress = 0.0
        
        # Очищаем лог
        self.textbox_log.configure(state="normal")
        self.textbox_log.delete("0.0", "end")
        self.textbox_log.configure(state="disabled")
        self.textbox_log.clipboard_clear()
        
        # Сбрасываем прогресс-бары
        self.progress_bar.set(0)
        self.progress_bar_file.set(0)
        self.lbl_total_percent.configure(text="0%")
        self.lbl_file_percent.configure(text="0%")
        self.lbl_current_file.configure(text="")
        self.lbl_progress.configure(text="Готов к работе")
        
        # Обновляем метки интерфейса
        self.lbl_files_count.configure(
            text="Файлы не выбраны",
            text_color="gray"
        )
        self.lbl_input_folder_path.configure(
            text="Папка не выбрана",
            text_color="gray"
        )
        self.lbl_folder_path.configure(
            text="Папка не выбрана (по умолчанию - рядом с файлом)",
            text_color="gray"
        )
        
        # Логируем сброс
        self.log("Все настройки сброшены")

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
        Callback для обновления прогресса внутри файла (используется только для информации)

        Args:
            stage: 'conversion' или 'transcription'
            stage_progress: прогресс этапа от 0.0 до 1.0
        """
        # Сохраняем информацию о текущем этапе для отображения
        self.current_stage = stage
        self.current_stage_progress = stage_progress

    def update_progress_display(self):
        """Обновляет отображение прогресса на основе количества файлов и реального времени"""
        if not self.is_processing or self.total_files == 0:
            return

        # Реальное время, прошедшее с начала обработки
        total_elapsed = time.time() - self.start_time

        # Базовый прогресс на основе количества обработанных файлов
        files_progress = self.files_processed / self.total_files

        # Прогресс текущего файла на основе времени
        current_file_progress = 0.0
        current_filepath = ""
        if self.files_processed < len(self.files_to_process) and self.current_file_start_time > 0:
            current_filepath = self.files_to_process[self.files_processed]
            current_elapsed = time.time() - self.current_file_start_time

            # Получаем ожидаемое время обработки текущего файла
            estimated_time = self.file_estimates.get(current_filepath, 30)

            if estimated_time > 0:
                # Прогресс = прошедшее время / ожидаемое время (но не больше 1.0)
                # Используем более консервативный подход: считаем, что файл обрабатывается быстрее
                # чем ожидалось, только если прошло больше 80% от ожидаемого времени
                if current_elapsed >= estimated_time * 0.8:
                    # Если прошло больше 80% от ожидаемого времени, считаем прогресс близким к завершению
                    current_file_progress = min(0.95, current_elapsed / estimated_time)
                else:
                    # Используем более консервативную оценку: прогресс растет медленнее
                    # Это предотвращает завышение прогресса в начале обработки
                    current_file_progress = min(0.9, (current_elapsed / estimated_time) * 0.8)
            else:
                # Если нет оценки, используем простую линейную зависимость
                # Но ограничиваем максимальный прогресс до 90%, пока файл не завершен
                current_file_progress = min(0.9, current_elapsed / 60.0)  # Предполагаем 60 секунд по умолчанию

        # Общий прогресс = прогресс файлов + прогресс текущего файла / общее количество файлов
        overall_progress = files_progress + (current_file_progress / self.total_files)

        # Ограничиваем прогресс до 99%, пока обработка не завершена
        overall_progress = min(0.99, overall_progress)

        # Обновляем общий прогресс-бар
        self.progress_bar.set(overall_progress)
        percent = int(overall_progress * 100)
        self.lbl_total_percent.configure(text=f"{percent}%")
        
        # Обновляем прогресс текущего файла
        file_percent = int(current_file_progress * 100)
        self.progress_bar_file.set(current_file_progress)
        self.lbl_file_percent.configure(text=f"{file_percent}%")
        
        # Обновляем информацию о текущем файле
        if current_filepath:
            filename = os.path.basename(current_filepath)
            # Определяем текущий этап
            stage_text = ""
            if self.current_stage == 'conversion':
                stage_text = " (конвертация)"
            elif self.current_stage == 'transcription':
                stage_text = " (транскрибация)"
            
            # Показываем имя файла и номер
            file_info = f"Файл {self.files_processed + 1}/{self.total_files}: {filename}{stage_text}"
            # Обрезаем, если слишком длинное
            if len(file_info) > 80:
                file_info = f"Файл {self.files_processed + 1}/{self.total_files}: ...{filename[-40:]}{stage_text}"
            self.lbl_current_file.configure(text=file_info)

        # Рассчитываем оставшееся время на основе реальной скорости
        remaining_time = 0
        if self.files_processed < len(self.files_to_process):
            # Осталось файлов (включая текущий)
            remaining_files_count = len(self.files_to_process) - self.files_processed

            # Оставшееся время для текущего файла
            if self.current_file_start_time > 0:
                current_elapsed = time.time() - self.current_file_start_time
                estimated_current = self.file_estimates.get(
                    self.files_to_process[self.files_processed],
                    max(current_elapsed * 1.5, 30)  # Минимум 30 секунд или 1.5x от текущего времени
                )

                # Если реальное время уже превысило оценку, экстраполируем
                if current_elapsed >= estimated_current:
                    # Файл обрабатывается дольше - используем текущую скорость
                    if current_file_progress > 0.1:  # Если есть хотя бы 10% прогресса
                        # Экстраполируем: оставшееся время = текущее время * (1 - прогресс) / прогресс
                        remaining_current = current_elapsed * ((1 - current_file_progress) / max(current_file_progress, 0.1))
                    else:
                        # Если прогресс мал, используем оценку
                        remaining_current = max(0, estimated_current - current_elapsed)
                else:
                    # Используем оценку, скорректированную на реальное время
                    remaining_current = max(0, estimated_current - current_elapsed)
            else:
                # Файл еще не начат
                estimated_current = self.file_estimates.get(
                    self.files_to_process[self.files_processed],
                    30
                )
                remaining_current = estimated_current

            # Средняя скорость обработки на основе реального времени
            if self.files_processed > 0:
                # Используем среднее время на уже обработанные файлы
                avg_time_per_file = self.time_spent / self.files_processed
            elif self.current_file_start_time > 0:
                # Если обрабатывается первый файл, используем его текущую скорость
                current_elapsed = time.time() - self.current_file_start_time
                avg_time_per_file = current_elapsed
            else:
                # Если нет данных, используем оценку
                avg_time_per_file = estimated_current

            # Оставшееся время = оставшееся время текущего файла + среднее время * оставшиеся файлы
            remaining_time = remaining_current + (avg_time_per_file * (remaining_files_count - 1))

        # Оставшееся время
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
        self.current_file_start_time = time.time()
        self.current_stage = None
        self.current_stage_progress = 0.0
        self.update_progress_display()

        # Запускаем периодическое обновление прогресса на основе реального времени
        self._start_progress_updates()

    def _start_progress_updates(self):
        """Запускает периодическое обновление прогресса на основе реального времени"""
        def update_periodically():
            if self.is_processing:
                self.update_progress_display()
                # Обновляем каждую секунду
                self.progress_update_timer = self.after(1000, update_periodically)

        # Останавливаем предыдущий таймер, если есть
        if self.progress_update_timer:
            self.after_cancel(self.progress_update_timer)

        # Запускаем обновления
        update_periodically()

    def _stop_progress_updates(self):
        """Останавливает периодическое обновление прогресса"""
        if self.progress_update_timer:
            self.after_cancel(self.progress_update_timer)
            self.progress_update_timer = None

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
                # Сохраняем автоматически выбранный путь для следующего раза
                self.user_settings.set_last_output_dir(self.output_dir)
                self.log(f"Папка не выбрана. Использую директорию первого файла: {self.output_dir}")

        self.is_processing = True
        self.start_time = time.time()
        self.files_processed = 0
        self.total_files = len(self.files_to_process)
        self.time_spent = 0
        self.current_file_start_time = 0
        self.current_stage = None
        self.current_stage_progress = 0.0

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
        # Инициализируем прогресс-бары
        self.progress_bar.set(0)
        self.progress_bar_file.set(0)
        self.lbl_total_percent.configure(text="0%")
        self.lbl_file_percent.configure(text="0%")
        self.lbl_current_file.configure(text="Подготовка...")
        self.lbl_progress.configure(text=f"Оценка: ~{estimate_str}")

        thread = threading.Thread(target=self.process_files)
        thread.start()

    def process_files(self):
        """Основной процесс обработки файлов"""
        try:
            # Загружаем модель
            if not self.model_loader.load_model(self.log):
                self.is_processing = False
                self._stop_progress_updates()
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
            
            # Получаем параметры диаризации
            num_speakers = None
            if self.enable_diarization:
                try:
                    speakers_text = self.entry_num_speakers.get().strip()
                    if speakers_text:
                        num_speakers = int(speakers_text)
                        self.log(f"Количество спикеров: {num_speakers}")
                    else:
                        self.log("Количество спикеров: автоопределение")
                except ValueError:
                    self.log("ПРЕДУПРЕЖДЕНИЕ: Некорректное значение количества спикеров. Используется автоопределение.")

            # Обрабатываем каждый файл
            for i, filepath in enumerate(self.files_to_process):
                try:
                    self.update_progress(i)

                    # Получаем веса этапов для точной передачи процессору
                    conv_weight, trans_weight = self.get_file_stage_weights(filepath)

                    result = processor.process_file(
                        filepath,
                        self.output_dir,
                        i,
                        self.total_files,
                        estimated_conversion_ratio=conv_weight,
                        estimated_transcription_ratio=trans_weight,
                        enable_diarization=self.enable_diarization,
                        num_speakers=num_speakers,
                        output_formats=self.get_selected_formats()
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

                    # Используем реальное время из результата
                    self.time_spent += result['total_time']

                    # Обновляем оценку времени для следующего файла на основе реального времени
                    # Это поможет улучшить прогнозы для следующих файлов
                    if result['total_time'] > 0:
                        # Обновляем оценку для этого типа файла
                        current_filepath = self.files_to_process[i]
                        # Если реальное время сильно отличается от оценки, корректируем
                        estimated = self.file_estimates.get(current_filepath, result['total_time'])
                        if abs(result['total_time'] - estimated) > estimated * 0.3:  # Разница больше 30%
                            # Обновляем оценку на основе реального времени
                            self.file_estimates[current_filepath] = result['total_time']

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
            self._stop_progress_updates()
            self.btn_start.configure(
                state="normal",
                text="ЗАПУСТИТЬ ОБРАБОТКУ",
                fg_color="#2CC985"
            )
            return

        # Останавливаем периодическое обновление прогресса
        self._stop_progress_updates()

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