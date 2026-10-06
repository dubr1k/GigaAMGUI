"""
Главное окно приложения GigaAM v3 Transcriber на PyQt6
Строгий профессиональный дизайн без ярких цветов
"""

import json
import os
import sys
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None

from PyQt6.QtCore import QEvent, QObject, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QFontDatabase,
    QIcon,
)
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMessageBox,
)

from ..config import (
    STATS_FILE,
    user_config_dir,
)
from ..core import ModelLoader
from ..utils import (
    AppLogger,
    MediaDownloader,
    ProcessingStats,
    TimeFormatter,
    UserSettings,
)
from .asr_backend_dialog import ASRBackendDialog, is_mlx_supported
from .download_mixin import DownloadMixin
from .files_mixin import FilesMixin
from .i18n_mixin import I18nMixin
from .live_mixin import LiveMixin
from .live_ui_mixin import LiveUiMixin
from .llm_mixin import LlmMixin
from .llm_ui_mixin import LlmUiMixin
from .processing_mixin import ProcessingMixin
from .processing_options_ui_mixin import ProcessingOptionsUiMixin
from .settings_mixin import SettingsMixin
from .style_mixin import StyleMixin
from .support_surfaces_mixin import SupportSurfacesMixin
from .theme_mixin import ThemeMixin
from .ui_build_mixin import UiBuildMixin

_INSTANCE_LOCK_NAME = "instance.lock"
_OPEN_REQUESTS_NAME = "open_requests.jsonl"


class WorkerSignals(QObject):
    """Сигналы для потока обработки"""
    log_message = pyqtSignal(str)
    progress_update = pyqtSignal(int)
    file_progress_update = pyqtSignal(int)
    current_file_info = pyqtSignal(str)
    processing_finished = pyqtSignal(bool, str)
    stage_update = pyqtSignal(object)
    download_progress = pyqtSignal(int)
    download_finished = pyqtSignal(list)
    download_failed = pyqtSignal(str)
    llm_finished = pyqtSignal(bool, str, str)
    llm_progress_update = pyqtSignal(int, int)
    llm_progress_started = pyqtSignal(int, int)
    llm_response_ready = pyqtSignal()
    llm_stream_chunk = pyqtSignal(str)
    live_status = pyqtSignal(object)
    live_event = pyqtSignal(object)
    live_finished = pyqtSignal(object)
    live_answer = pyqtSignal(str, str)


class GigaApplication(QApplication):
    """QApplication with macOS Finder/Dock open-file event support."""

    file_open_requested = pyqtSignal(list)

    def __init__(self, argv):
        super().__init__(argv)
        self._pending_open_paths = []

    def event(self, event):
        if event.type() == QEvent.Type.FileOpen:
            path = ""
            try:
                url = event.url()
                if url.isLocalFile():
                    path = url.toLocalFile()
            except Exception:
                path = ""
            if not path:
                try:
                    path = event.file()
                except Exception:
                    path = ""
            if path:
                self._pending_open_paths.append(path)
                self.file_open_requested.emit([path])
            return True
        return super().event(event)

    def take_pending_open_paths(self):
        paths = self._pending_open_paths[:]
        self._pending_open_paths.clear()
        return paths


class GigaTranscriberQtApp(
    LlmMixin, LlmUiMixin, DownloadMixin, ProcessingMixin, FilesMixin,
    I18nMixin, SettingsMixin, SupportSurfacesMixin, StyleMixin, ThemeMixin, ProcessingOptionsUiMixin,
    LiveMixin, LiveUiMixin,
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

        self.transcript_files_for_llm = []
        self.llm_output_dir = ""
        self.llm_transcript_dir = os.path.expanduser("~")
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
        self.signals.progress_update.connect(self._update_total_progress)
        self.signals.file_progress_update.connect(self._update_file_progress)
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
        self.signals.llm_stream_chunk.connect(self._on_llm_stream_chunk)
        self.signals.live_status.connect(self._update_live_status)
        self.signals.live_event.connect(self._update_live_event)
        self.signals.live_finished.connect(self._on_live_finished)
        self.signals.live_answer.connect(self._update_live_answer)

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

    # ──────────────────────────────────────────────────────────────
    # Темы
    # ──────────────────────────────────────────────────────────────

    _LIGHT = {
        "bg":        "#f5f6f8",
        "bg_card":   "#ffffff",
        "border":    "#dde0e6",
        "text":      "#1a1a1a",
        "text_sub":  "#374151",
        "text_mute": "#9ca3af",
        "text_mute2":"#6b7280",
        "btn_bg":    "#ffffff",
        "btn_border":"#d1d5db",
        "btn_text":  "#374151",
        "btn_hover_bg":    "#f0f4ff",
        "btn_hover_border":"#3b82f6",
        "btn_hover_text":  "#1d4ed8",
        "accent":    "#3b82f6",
        "accent2":   "#2563eb",
        "accent3":   "#1d4ed8",
        "accent_dis":"#93c5fd",
        "clear_bg":  "#f3f4f6",
        "clear_text":"#6b7280",
        "clear_border":"#e5e7eb",
        "clear_hover_bg":"#fee2e2",
        "clear_hover_border":"#fca5a5",
        "clear_hover_text":"#dc2626",
        "input_bg":  "#ffffff",
        "input_sel": "#bfdbfe",
        "input_dis": "#f9fafb",
        "input_dis_text":"#9ca3af",
        "progress_bg":"#e5e7eb",
        "progress_chunk":"#3b82f6",
        "progress_chunk2":"#2563eb",
        "tab_bg":    "#e8eaee",
        "tab_text":  "#555",
        "tab_sel_bg":"#f5f6f8",
        "tab_sel_text":"#1a1a1a",
        "tab_accent":"#3b82f6",
        "tab_hover": "#dde1ea",
        "scroll_bg": "#f1f2f4",
        "scroll_handle":"#cbd5e1",
        "scroll_handle_hover":"#94a3b8",
        "status_bg": "#f3f4f6",
        "status_text":"#374151",
        "theme_btn": "🌙",
    }

    _DARK = {
        "bg":        "#06172f",
        "bg_card":   "#102c50",
        "border":    "#365f8d",
        "text":      "#edf5ff",
        "text_sub":  "#c9dcf5",
        "text_mute": "#7391b7",
        "text_mute2":"#9bb5d5",
        "btn_bg":    "#14365e",
        "btn_border":"#416b99",
        "btn_text":  "#dceaff",
        "btn_hover_bg":    "#1b4778",
        "btn_hover_border":"#5a9cff",
        "btn_hover_text":  "#ffffff",
        "accent":    "#2f7cff",
        "accent2":   "#1e66e5",
        "accent3":   "#164dae",
        "accent_dis":"#264f8c",
        "clear_bg":  "#153252",
        "clear_text":"#a8c0dd",
        "clear_border":"#365f8d",
        "clear_hover_bg":"#4a2637",
        "clear_hover_border":"#a85270",
        "clear_hover_text":"#ffd2df",
        "input_bg":  "#0b2546",
        "input_sel": "#1f5fad",
        "input_dis": "#0a1e39",
        "input_dis_text":"#597695",
        "progress_bg":"#0b2443",
        "progress_chunk":"#2f7cff",
        "progress_chunk2":"#1e66e5",
        "tab_bg":    "#0e294a",
        "tab_text":  "#9bb5d5",
        "tab_sel_bg":"#102c50",
        "tab_sel_text":"#edf5ff",
        "tab_accent":"#2f7cff",
        "tab_hover": "#173c68",
        "scroll_bg": "#0b2546",
        "scroll_handle":"#365f8d",
        "scroll_handle_hover":"#5a8ec7",
        "status_bg": "#0b2546",
        "status_text":"#c9dcf5",
        "theme_btn": "☀️",
    }
















    def _select_asr_model(self):
        if self.is_processing:
            QMessageBox.information(self, self._t("Смена модели", "Model change"), self._t("Дождитесь завершения обработки.", "Wait for processing to finish."))
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
        if self.is_processing:
            QMessageBox.information(
                self,
                self._t("Смена backend", "Backend change"),
                self._t(
                    "Дождитесь завершения обработки перед сменой backend.",
                    "Wait for processing to finish before changing backend.",
                ),
            )
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
        from PyQt6.QtWidgets import QMessageBox

        from .device_dialog import change_device_interactive

        if self.is_processing:
            QMessageBox.information(
                self,
                self._t("Устройство", "Device"),
                self._t("Дождитесь окончания обработки перед сменой устройства.", "Wait for processing to finish before changing the device."),
            )
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
















































def _argv_open_paths(argv: list) -> list:
    paths = []
    for arg in argv[1:]:
        if arg.startswith("-psn_"):
            continue
        path = os.path.abspath(os.path.expanduser(arg))
        if os.path.exists(path):
            paths.append(path)
    return paths


def _instance_lock_path() -> Path:
    return user_config_dir() / _INSTANCE_LOCK_NAME


def _open_requests_path() -> Path:
    return user_config_dir() / _OPEN_REQUESTS_NAME


def _try_acquire_instance_lock():
    lock_path = _instance_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("w", encoding="utf-8")
    if fcntl is None:
        return lock_file
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        return None
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    return lock_file


def _queue_open_request(paths: list) -> None:
    queue_path = _open_requests_path()
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"paths": paths, "pid": os.getpid(), "time": time.time()}
    with queue_path.open("a", encoding="utf-8") as queue:
        queue.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _install_open_request_poller(window: GigaTranscriberQtApp):
    queue_path = _open_requests_path()
    timer = QTimer(window)

    def poll_requests():
        if not queue_path.exists():
            return
        try:
            lines = queue_path.read_text(encoding="utf-8").splitlines()
            queue_path.write_text("", encoding="utf-8")
        except OSError as exc:
            window.log(f"Не удалось прочитать очередь open requests: {exc}")
            return
        for line in lines:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            paths = payload.get("paths") or []
            if isinstance(paths, list):
                window.open_paths_from_system(paths, append=True)

    timer.timeout.connect(poll_requests)
    timer.start(500)
    window._open_request_poller = timer
    QTimer.singleShot(0, poll_requests)
    return timer


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

    app = app or QApplication.instance() or GigaApplication(sys.argv)
    app.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont))

    initial_open_paths = _argv_open_paths(sys.argv)
    instance_lock = getattr(app, "_gigaam_instance_lock_file", None) or _try_acquire_instance_lock()
    if instance_lock is None:
        _queue_open_request(initial_open_paths)
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
    _install_open_request_poller(window)
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
