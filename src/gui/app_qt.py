"""
Главное окно приложения GigaAM v3 Transcriber на PyQt6
Строгий профессиональный дизайн без ярких цветов
"""

import os
import sys

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QFontDatabase,
    QIcon,
)
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
)

from ..config import STATS_FILE
from ..core import ModelLoader
from ..utils import (
    AppLogger,
    MediaDownloader,
    ProcessingStats,
    TimeFormatter,
    UserSettings,
)
from .application import GigaApplication
from .asr_backend_dialog import ASRBackendDialog, is_mlx_supported
from .download_mixin import DownloadMixin
from .files_mixin import FilesMixin
from .i18n_mixin import I18nMixin
from .journal_mixin import JournalMixin
from .lifecycle_mixin import LifecycleMixin, install_exception_hook
from .live_mixin import LiveMixin
from .live_ui_mixin import LiveUiMixin
from .llm_mixin import LlmMixin
from .llm_ui_mixin import LlmUiMixin
from .processing_mixin import ProcessingMixin
from .processing_options_ui_mixin import ProcessingOptionsUiMixin
from .result_view_mixin import ResultViewMixin
from .settings_mixin import SettingsMixin
from .single_instance import (
    argv_open_paths,
    install_open_request_poller,
    qt_argv,
    queue_open_request,
    try_acquire_instance_lock,
)
from .style_mixin import StyleMixin
from .support_surfaces_mixin import SupportSurfacesMixin
from .theme_mixin import ThemeMixin
from .ui_build_mixin import UiBuildMixin


class WorkerSignals(QObject):
    """Сигналы для потока обработки"""
    log_message = pyqtSignal(str)
    current_file_info = pyqtSignal(str)
    processing_finished = pyqtSignal(bool, str, object)  # success, message, cancel-токен запуска
    stage_update = pyqtSignal(object)
    download_progress = pyqtSignal(int)
    download_finished = pyqtSignal(list)
    download_failed = pyqtSignal(str)
    llm_finished = pyqtSignal(bool, str, str)
    llm_progress_update = pyqtSignal(int, int)
    llm_progress_started = pyqtSignal(int, int)
    llm_response_ready = pyqtSignal()
    llm_tools_scanned = pyqtSignal(object)  # list[ToolStatus] из cli_tools.scan
    llm_tool_checked = pyqtSignal(object)  # ToolStatus одного инструмента
    live_status = pyqtSignal(object)
    live_event = pyqtSignal(object)
    live_finished = pyqtSignal(object)
    live_stop_failed = pyqtSignal(str)
    live_backend_prepared = pyqtSignal(object, object)  # запрос старта, текст ошибки или None
    live_answer = pyqtSignal(str, str)
    api_status_checked = pyqtSignal(str, bool)  # адрес API, отвечает ли /health
    live_devices_probed = pyqtSignal(int, object)  # поколение запроса, {CaptureSource: [CaptureDevice]}


class GigaTranscriberQtApp(
    LlmMixin, LlmUiMixin, DownloadMixin, ProcessingMixin, ResultViewMixin, FilesMixin,
    I18nMixin, SettingsMixin, JournalMixin, SupportSurfacesMixin, StyleMixin, ThemeMixin, ProcessingOptionsUiMixin,
    LiveMixin, LiveUiMixin, LifecycleMixin,
    UiBuildMixin, QMainWindow,
):
    """Главное окно приложения для транскрибации на PyQt6"""

    def __init__(self):
        super().__init__()

        self.files_to_process = []
        self.output_dir = ""
        self.input_dir = ""
        self.is_processing = False
        self._last_generated_transcript_files = []
        self._cancel_requested = False
        self._processing_cancel_event = None
        self._processing_thread = None
        # Выход ждёт экспорта live-сессии: см. LifecycleMixin.closeEvent.
        self._close_confirmed = False
        self._close_pending = False
        self._close_forced = False
        self.start_time = None
        self.files_processed = 0
        self.total_files = 0
        self.time_spent = 0
        self.current_file_start_time = 0
        self.current_stage = None
        self.current_stage_progress = 0.0
        self.current_stage_file_progress = 0.0
        self.current_stage_is_indeterminate = False
        self._stage_start_time = 0.0
        self._current_filename = ""
        self.is_downloading = False
        self.start_processing_after_download = False
        self._last_result_dir = ""
        self._last_processing_results = []
        # Строки таблицы «Журнал»: заполняются из сообщений лога ещё до того,
        # как построена сама вкладка, поэтому список живёт с самого начала.
        self._journal_entries = []

        self.transcript_files_for_llm = []
        self.llm_output_dir = ""
        # Пусто, пока пользователь не выбрал транскрипты: из этой папки при
        # старте пересобирается список LLM (домашняя — только для диалога).
        self.llm_transcript_dir = ""
        self.is_llm_processing = False
        self.llm_last_result_text = ""
        self.llm_last_result_name = "llm_result"

        self.enable_diarization = False
        self.diarization_backend = "pyannote"
        self.num_speakers = None
        self._diarization_prompt_open = False

        self.output_formats = {
            'txt': True,
            'txt_timecodes': True,
            'txt_diarize': False,
            'txt_diarize_timecodes': False,
            'md': False,
            'srt': False,
            'vtt': False,
        }

        self.app_logger = AppLogger()
        self.app_logger.log_session_start()
        self.model_loader = ModelLoader()
        self.stats = ProcessingStats(STATS_FILE)
        self.time_formatter = TimeFormatter()
        self.user_settings = UserSettings()
        self.media_downloader = MediaDownloader()
        self._init_live_state()

        self._theme = self.user_settings.settings.get("theme", "dark")
        self._lang = self.user_settings.settings.get("language", "ru")
        self._ui_scale = self._effective_ui_scale()

        self.signals = WorkerSignals()
        self.signals.log_message.connect(self._append_log)
        self.signals.current_file_info.connect(self._update_current_file_info)
        self.signals.processing_finished.connect(self._on_processing_finished)
        self.signals.stage_update.connect(self._on_stage_update)
        self.signals.download_progress.connect(self._update_download_progress)
        self.signals.download_finished.connect(self._on_download_finished)
        self.signals.download_failed.connect(self._on_download_failed)
        self.signals.llm_finished.connect(self._on_llm_finished)
        self.signals.llm_progress_update.connect(self._update_llm_progress)
        self.signals.llm_progress_started.connect(self._start_llm_progress)
        self.signals.llm_response_ready.connect(self._on_llm_response_ready)
        self.signals.llm_tools_scanned.connect(self._on_llm_tools_scanned)
        self.signals.llm_tool_checked.connect(self._on_llm_tool_checked)
        self.signals.live_status.connect(self._update_live_status)
        self.signals.live_event.connect(self._update_live_event)
        self.signals.live_finished.connect(self._on_live_finished)
        self.signals.live_stop_failed.connect(self._on_live_stop_failed)
        self.signals.live_backend_prepared.connect(self._on_live_backend_prepared)
        self.signals.live_answer.connect(self._update_live_answer)
        self.signals.api_status_checked.connect(self._on_api_status_checked)
        self.signals.live_devices_probed.connect(self._on_live_devices_probed)

        saved_output_dir = self.user_settings.get_last_output_dir()
        saved_input_dir = self.user_settings.get_last_files_dir()
        saved_llm_output_dir = self.user_settings.get_value("llm_output_dir", "")
        saved_llm_transcript_dir = self.user_settings.get_value("llm_transcript_dir", "")

        if saved_output_dir:
            self.output_dir = saved_output_dir
        if saved_input_dir:
            self.input_dir = saved_input_dir
        if saved_llm_output_dir and os.path.isdir(saved_llm_output_dir):
            self.llm_output_dir = saved_llm_output_dir
        elif saved_output_dir:
            self.llm_output_dir = saved_output_dir
        if saved_llm_transcript_dir and os.path.isdir(saved_llm_transcript_dir):
            self.llm_transcript_dir = saved_llm_transcript_dir
        elif saved_input_dir:
            self.llm_transcript_dir = saved_input_dir

        self._init_ui()
        self._restore_ui_settings()
        self._restore_live_settings()
        self.setAcceptDrops(True)

        if saved_output_dir:
            self._update_output_dir_label(saved_output_dir)
        if saved_input_dir:
            self._update_input_dir_label(saved_input_dir)
        if self.llm_output_dir:
            self._update_llm_output_dir_label(self.llm_output_dir)

        self.app_logger.cleanup_old_logs()

    def _select_asr_model(self):
        if self._refuse_model_change_while_busy(self._t("Смена модели", "Model change")):
            return
        from PyQt6.QtWidgets import QInputDialog

        from ..core.asr.models import ASR_MODELS

        ids = list(ASR_MODELS)
        labels = [f"{ASR_MODELS[key]} [{key}]" for key in ids]
        current = ids.index(self.model_loader.requested_model) if self.model_loader.requested_model in ids else 0
        selected, accepted = QInputDialog.getItem(self, self._t("Модель распознавания", "Recognition model"), self._t("Модель:", "Model:"), labels, current, False)
        if accepted:
            model = ids[labels.index(selected)]
            self.model_loader.configure_model(model)
            self.user_settings.set_value("asr_model", model)
            self.log(f"ASR model selected: {model}")

    def _select_asr_backend(self):
        if self._refuse_model_change_while_busy(self._t("Смена backend", "Backend change")):
            return

        selected = ASRBackendDialog.pick_configuration(
            self,
            current_backend=self.model_loader.requested_backend,
            current_provider=self.model_loader.requested_provider,
            mlx_supported=is_mlx_supported(),
        )

        if not selected:
            return

        backend, provider = selected
        if (
            backend == self.model_loader.requested_backend
            and provider == self.model_loader.requested_provider
        ):
            return
        self.model_loader.configure_backend(backend)
        self.model_loader.configure_onnx_runtime(provider=provider)
        self.user_settings.set_value("asr_backend", backend)
        self.user_settings.set_value("onnx_provider", provider)
        self.log(
            f"Выбран ASR backend: {backend}, ONNX provider: {provider}"
            if self._lang == "ru"
            else f"ASR backend selected: {backend}, ONNX provider: {provider}"
        )

    def _change_device(self):
        """Смена вычислительного устройства (CPU / GPU / GPU 50xx) из меню."""
        from .device_dialog import change_device_interactive

        # Смена устройства выгружает модель и подменяет torch-runtime —
        # из-под идущей Live-записи тоже, не только из-под пакетной обработки.
        if self._refuse_model_change_while_busy(self._t("Устройство", "Device")):
            return

        chosen = change_device_interactive(self)
        if chosen:
            label = chosen
            try:
                from ..utils import runtime_manager as rm
                label = rm.VARIANTS.get(chosen, {}).get("label", chosen)
            except Exception:
                pass
            self.log(
                f"Активировано устройство: {label}"
                if self._lang == "ru" else
                f"Active device: {label}"
            )

    # ──────────────────────────────────────────────────────────────
    # UI
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def _is_headless() -> bool:
        app = QApplication.instance()
        return bool(app) and app.platformName() in ("offscreen", "minimal")


def run_qt_app(app=None):
    """Запускает приложение на PyQt6.

    app: уже созданный QApplication (используется на этапе выбора устройства).
    """
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('GigaAM.Transcriber.v3')
        except Exception:
            pass

    app = app or QApplication.instance() or GigaApplication(qt_argv(sys.argv))
    app.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont))

    initial_open_paths = argv_open_paths(sys.argv)
    instance_lock = getattr(app, "_gigaam_instance_lock_file", None) or try_acquire_instance_lock()
    if instance_lock is None:
        queue_open_request(initial_open_paths)
        sys.exit(0)

    # На macOS иконку приложения задаёт .app bundle через icon.icns. Если
    # переопределить её здесь Windows-файлом icon.ico, Qt заменит Dock-иконку
    # после запуска и macOS покажет неадаптированный квадратный вариант.
    if sys.platform != 'darwin':
        icon_path = os.path.join(
            getattr(sys, '_MEIPASS', os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
            'icon.ico'
        )
        if os.path.exists(icon_path):
            app.setWindowIcon(QIcon(icon_path))

    window = GigaTranscriberQtApp()
    window._instance_lock_file = instance_lock
    install_open_request_poller(window)
    install_exception_hook(window)
    if isinstance(app, GigaApplication):
        app.file_open_requested.connect(lambda paths: window.open_paths_from_system(paths, append=True))
    window.show()
    if initial_open_paths:
        QTimer.singleShot(0, lambda: window.open_paths_from_system(initial_open_paths, append=True))
    if isinstance(app, GigaApplication):
        pending_open_paths = app.take_pending_open_paths()
        if pending_open_paths:
            QTimer.singleShot(0, lambda paths=pending_open_paths: window.open_paths_from_system(paths, append=True))

    if sys.platform == 'win32' and os.path.exists(icon_path):
        def _set_win32_icon():
            try:
                import ctypes
                hwnd = int(window.winId())
                hicon = ctypes.windll.user32.LoadImageW(None, icon_path, 1, 0, 0, 0x10 | 0x40)
                if hicon:
                    ctypes.windll.user32.SendMessageW(hwnd, 0x0080, 0, hicon)
                    ctypes.windll.user32.SendMessageW(hwnd, 0x0080, 1, hicon)
            except Exception:
                pass
        QTimer.singleShot(100, _set_win32_icon)

    sys.exit(app.exec())


if __name__ == "__main__":
    run_qt_app()
