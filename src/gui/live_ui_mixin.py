"""Live-tab widget construction for the desktop application."""

from __future__ import annotations

from PyQt6.QtCore import QElapsedTimer, Qt, QTimer
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class LiveUiMixin:
    def _create_live_tab(self) -> QWidget:
        tab = QWidget()
        tab.setObjectName("live_workspace")
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(self._px(8), self._px(8), self._px(8), self._px(8))
        layout.setSpacing(self._px(6))

        heading = QHBoxLayout()
        heading.setObjectName("live_workspace_heading")
        title_block = QVBoxLayout()
        title_block.setSpacing(self._px(1))
        title = QLabel(self._t("Live", "Live"))
        title.setObjectName("page_title")
        title_block.addWidget(title)
        subtitle = QLabel(self._t("Запись и расшифровка в реальном времени", "Live capture and transcription"))
        subtitle.setObjectName("page_subtitle")
        title_block.addWidget(subtitle)
        heading.addLayout(title_block)
        heading.addStretch()
        layout.addLayout(heading)

        workspace = QHBoxLayout()
        workspace.setObjectName("live_three_pane_layout")
        workspace.setSpacing(self._px(6))

        source_pane = QWidget()
        source_pane.setObjectName("live_source_pane")
        source_pane.setMinimumWidth(self._px(155))
        source_pane.setMaximumWidth(self._px(175))
        source_layout = QVBoxLayout(source_pane)
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_layout.setSpacing(self._px(6))

        self.grp_live_source = QGroupBox(self._t("Источник", "Source"))
        self.grp_live_source.setObjectName("live_source_card")
        source_form = QFormLayout(self.grp_live_source)
        source_form.setContentsMargins(self._px(8), self._px(7), self._px(8), self._px(8))
        source_form.setHorizontalSpacing(self._px(4))
        source_form.setVerticalSpacing(self._px(3))
        source_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.combo_live_source = QComboBox()
        self.combo_live_source.addItem(self._t("Микрофон", "Microphone"), "mic")
        self.combo_live_source.addItem(self._t("Системный звук", "System audio"), "system")
        self.combo_live_source.addItem(self._t("Микрофон + системный звук", "Microphone + system audio"), "both")
        self.combo_live_source.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.lbl_live_source = QLabel(self._t("Источник:", "Source:"))
        source_form.addRow(self.lbl_live_source, self.combo_live_source)

        self.combo_live_mic_device = QComboBox()
        self.combo_live_mic_device.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.lbl_live_mic_device = QLabel(self._t("Микрофон:", "Microphone:"))
        source_form.addRow(self.lbl_live_mic_device, self.combo_live_mic_device)
        self.combo_live_system_device = QComboBox()
        self.combo_live_system_device.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.lbl_live_system_device = QLabel(self._t("Системный звук:", "System audio:"))
        source_form.addRow(self.lbl_live_system_device, self.combo_live_system_device)

        self.cb_live_mic_audio = QCheckBox(self._t("Записывать дорожку микрофона", "Record microphone track"))
        self.cb_live_mic_audio.setChecked(True)
        self.cb_live_system_audio = QCheckBox(self._t("Записывать дорожку системного звука", "Record system audio track"))
        self.cb_live_system_audio.setChecked(True)
        self.cb_live_mic_audio.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.cb_live_system_audio.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        tracks = QWidget()
        tracks.setObjectName("live_track_options")
        tracks_layout = QVBoxLayout(tracks)
        tracks_layout.setContentsMargins(0, 0, 0, 0)
        tracks_layout.setSpacing(self._px(3))
        tracks_layout.addWidget(self.cb_live_mic_audio)
        tracks_layout.addWidget(self.cb_live_system_audio)
        self.lbl_live_tracks = QLabel(self._t("Дорожки:", "Tracks:"))
        source_form.addRow(self.lbl_live_tracks, tracks)
        source_layout.addWidget(self.grp_live_source)

        self.grp_live_output = QGroupBox(self._t("Папка сессии", "Session folder"))
        self.grp_live_output.setObjectName("live_output_card")
        output_layout = QVBoxLayout(self.grp_live_output)
        output_layout.setContentsMargins(self._px(8), self._px(6), self._px(8), self._px(8))
        output_layout.setSpacing(self._px(3))
        self.btn_live_output_select = QPushButton(self._t("Выбрать папку", "Choose folder"))
        self.btn_live_output_select.setObjectName("secondary_button")
        self.btn_live_output_select.setFixedHeight(self._px(26))
        self.btn_live_output_select.clicked.connect(self._select_live_output_folder)
        output_layout.addWidget(self.btn_live_output_select)
        self.lbl_live_output_folder = QLabel()
        self.lbl_live_output_folder.setObjectName("live_output_path")
        self.lbl_live_output_folder.setStyleSheet(self._transparent_label_style(self._colors()["text_mute"]))
        self.lbl_live_output_folder.setWordWrap(True)
        self.lbl_live_output_folder.setMaximumHeight(self._px(28))
        output_layout.addWidget(self.lbl_live_output_folder)
        self.live_output_dir = QLineEdit()
        self.live_output_dir.setVisible(False)
        self.live_output_dir.textChanged.connect(self._update_live_output_folder_label)
        output_layout.addWidget(self.live_output_dir)
        source_layout.addWidget(self.grp_live_output)

        quick_actions = QWidget()
        quick_actions.setObjectName("live_session_actions")
        quick_layout = QHBoxLayout(quick_actions)
        quick_layout.setContentsMargins(self._px(2), 0, self._px(2), 0)
        quick_layout.setSpacing(self._px(4))
        self.btn_live_overlay = QPushButton(self._t("Оверлей", "Overlay"))
        self.btn_live_overlay.setObjectName("secondary_button")
        self.btn_live_overlay.setFixedHeight(self._px(26))
        self.btn_live_overlay.setCheckable(True)
        self.btn_live_overlay.clicked.connect(self._toggle_live_overlay)
        self.btn_live_clear = QPushButton(self._t("Очистить", "Clear"))
        self.btn_live_clear.setObjectName("secondary_button")
        self.btn_live_clear.setFixedHeight(self._px(26))
        self.btn_live_clear.clicked.connect(self._clear_live_display)
        quick_layout.addWidget(self.btn_live_overlay)
        quick_layout.addWidget(self.btn_live_clear)
        source_layout.addWidget(quick_actions)
        source_layout.addStretch()
        workspace.addWidget(source_pane)

        capture_pane = QWidget()
        capture_pane.setObjectName("live_capture_pane")
        capture_pane.setMinimumWidth(self._px(235))
        capture_layout = QVBoxLayout(capture_pane)
        capture_layout.setContentsMargins(0, 0, 0, 0)
        capture_layout.setSpacing(self._px(6))

        recorder = QGroupBox(self._t("Запись", "Recording"))
        recorder.setObjectName("live_recorder_card")
        recorder_layout = QVBoxLayout(recorder)
        recorder_layout.setContentsMargins(self._px(10), self._px(7), self._px(10), self._px(8))
        recorder_layout.setSpacing(self._px(4))
        self._live_timer_clock = QElapsedTimer()
        self._live_timer_elapsed_ms = 0
        self._live_timer_active = False
        self._live_timer_ticker = QTimer(tab)
        self._live_timer_ticker.setInterval(1000)
        self._live_timer_ticker.timeout.connect(self._refresh_live_recorder_timer)
        self.lbl_live_timer = QLabel("00:00:00")
        self.lbl_live_timer.setObjectName("live_timer_display")
        self.lbl_live_timer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_live_timer.setFixedHeight(self._px(22))
        self.lbl_live_timer.setToolTip(self._t("Таймер отражает длительность активной записи.", "The timer reflects active capture duration."))
        recorder_layout.addWidget(self.lbl_live_timer)
        self.lbl_live_waveform = QLabel(self._t("Аудиосигнал появится во время записи", "Audio signal appears during capture"))
        self.lbl_live_waveform.setObjectName("live_waveform_display")
        self.lbl_live_waveform.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_live_waveform.setFixedHeight(self._px(24))
        self.lbl_live_waveform.setToolTip(self._t("Индикатор аудиосигнала ожидает активную сессию.", "The audio signal indicator is waiting for an active session."))
        recorder_layout.addWidget(self.lbl_live_waveform)
        self.lbl_live_status = QLabel(self._t("Готово к записи", "Ready for live capture"))
        self.lbl_live_status.setObjectName("live_status_display")
        self.lbl_live_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_live_status.setWordWrap(True)
        self.lbl_live_status.setMaximumHeight(self._px(24))
        recorder_layout.addWidget(self.lbl_live_status)
        self.lbl_live_problem = QLabel("")
        self.lbl_live_problem.setObjectName("live_problem_display")
        self.lbl_live_problem.setWordWrap(True)
        self.lbl_live_problem.setStyleSheet("color: #d9534f;")
        self.lbl_live_problem.hide()
        recorder_layout.addWidget(self.lbl_live_problem)

        self.live_controls_layout = QHBoxLayout()
        self.live_controls_layout.setSpacing(self._px(4))
        self.btn_live_start = QPushButton(self._t("НАЧАТЬ ЗАПИСЬ", "START LIVE"))
        self.btn_live_start.setObjectName("live_record_button")
        self.btn_live_start.setFixedHeight(self._px(30))
        self.btn_live_start.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.btn_live_start.clicked.connect(self._start_live_session)
        self.btn_live_pause = QPushButton(self._t("Пауза", "Pause"))
        self.btn_live_pause.setObjectName("secondary_button")
        self.btn_live_pause.setFixedHeight(self._px(24))
        self.btn_live_pause.clicked.connect(self._pause_live_session)
        self.btn_live_stop = QPushButton(self._t("Остановить", "Stop"))
        self.btn_live_stop.setObjectName("live_stop_button")
        self.btn_live_stop.setFixedHeight(self._px(24))
        self.btn_live_stop.clicked.connect(self._stop_live_session)
        transport_actions = QVBoxLayout()
        transport_actions.setSpacing(self._px(2))
        transport_actions.addWidget(self.btn_live_pause)
        transport_actions.addWidget(self.btn_live_stop)
        self.live_controls_layout.addWidget(self.btn_live_start, 1)
        self.live_controls_layout.addLayout(transport_actions)
        recorder_layout.addLayout(self.live_controls_layout)
        capture_layout.addWidget(recorder)

        transcript_panel = QGroupBox(self._t("Live transcript", "Live transcript"))
        transcript_panel.setObjectName("live_transcript_card")
        transcript_layout = QVBoxLayout(transcript_panel)
        transcript_layout.setContentsMargins(self._px(8), self._px(6), self._px(8), self._px(8))
        transcript_layout.setSpacing(self._px(3))
        transcript_hint = QLabel(self._t("Таймкоды и мягкие метки спикеров появятся по мере распознавания.", "Timecodes and restrained speaker labels appear as speech is recognized."))
        transcript_hint.setObjectName("page_subtitle")
        transcript_hint.setWordWrap(True)
        transcript_hint.setMaximumHeight(self._px(28))
        transcript_layout.addWidget(transcript_hint)
        self.live_transcript = QTextEdit()
        self.live_transcript.setObjectName("live_transcript_display")
        self.live_transcript.setReadOnly(True)
        self.live_transcript.setFont(self._font(10, fixed=True))
        self.live_transcript.setFixedHeight(self._px(82))
        self.live_transcript.setPlaceholderText(self._t("Расшифровка появится здесь", "Transcript appears here"))
        transcript_layout.addWidget(self.live_transcript, 1)
        capture_layout.addWidget(transcript_panel, 1)
        workspace.addWidget(capture_pane, 1)

        parameters_pane = QWidget()
        parameters_pane.setObjectName("live_parameters_pane")
        parameters_pane.setMinimumWidth(self._px(160))
        parameters_pane.setMaximumWidth(self._px(180))
        parameters_layout = QVBoxLayout(parameters_pane)
        parameters_layout.setContentsMargins(0, 0, 0, 0)
        parameters_layout.setSpacing(self._px(6))

        self.grp_live_exports = QGroupBox(self._t("Экспорт", "Export"))
        self.grp_live_exports.setObjectName("live_parameters_card")
        exports_group_layout = QVBoxLayout(self.grp_live_exports)
        exports_group_layout.setContentsMargins(self._px(8), self._px(7), self._px(8), self._px(8))
        exports_group_layout.setSpacing(self._px(4))
        parameters_form = QFormLayout()
        parameters_form.setHorizontalSpacing(self._px(4))
        parameters_form.setVerticalSpacing(self._px(3))
        parameters_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.combo_live_diarization = QComboBox()
        self.combo_live_diarization.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.combo_live_diarization.addItem(self._t("Выключено", "Off"), "off")
        self.combo_live_diarization.addItem(self._t("Оценка в реальном времени", "Live estimate"), "live_estimate")
        self.combo_live_diarization.addItem(self._t("После остановки", "After stop"), "after_stop")
        self.combo_live_diarization.setToolTip(
            self._t(
                "Оценки анонимны и могут меняться в последние 10 секунд.",
                "Live estimates are anonymous and may change during the most recent 10 seconds.",
            )
        )
        self.lbl_live_diarization = QLabel(self._t("Диаризация:", "Diarization:"))
        parameters_form.addRow(self.lbl_live_diarization, self.combo_live_diarization)
        self.spin_live_gain = QDoubleSpinBox()
        self.spin_live_gain.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.spin_live_gain.setRange(0.0, 2.0)
        self.spin_live_gain.setSingleStep(0.1)
        self.spin_live_gain.setValue(1.0)
        self.lbl_live_gain = QLabel(self._t("Усиление:", "Gain:"))
        parameters_form.addRow(self.lbl_live_gain, self.spin_live_gain)
        exports_group_layout.addLayout(parameters_form)

        export_rows = QGridLayout()
        export_rows.setHorizontalSpacing(self._px(4))
        export_rows.setVerticalSpacing(self._px(2))
        self.cb_live_export_txt = QCheckBox(self._t("Текст", "Text"))
        self.cb_live_export_txt.setChecked(True)
        self.cb_live_export_txt_timecodes = QCheckBox(self._t("Таймкоды", "Timecodes"))
        self.cb_live_export_txt_timecodes.setChecked(True)
        self.cb_live_export_txt_diarize = QCheckBox(self._t("Диар.", "Diar."))
        self.cb_live_export_txt_diarize_timecodes = QCheckBox(self._t("Диар. + время", "Diar. + time"))
        self.cb_live_export_md = QCheckBox("Markdown")
        self.cb_live_export_srt = QCheckBox("SRT")
        self.cb_live_export_vtt = QCheckBox("VTT")
        self.live_export_checkboxes = {
            "txt": self.cb_live_export_txt,
            "txt_timecodes": self.cb_live_export_txt_timecodes,
            "txt_diarize": self.cb_live_export_txt_diarize,
            "txt_diarize_timecodes": self.cb_live_export_txt_diarize_timecodes,
            "md": self.cb_live_export_md,
            "srt": self.cb_live_export_srt,
            "vtt": self.cb_live_export_vtt,
        }
        for checkbox in self.live_export_checkboxes.values():
            checkbox.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        export_rows.addWidget(self.cb_live_export_txt, 0, 0, 1, 2)
        export_rows.addWidget(self.cb_live_export_txt_timecodes, 1, 0)
        export_rows.addWidget(self.cb_live_export_txt_diarize, 1, 1)
        export_rows.addWidget(self.cb_live_export_txt_diarize_timecodes, 2, 0, 1, 2)
        export_rows.addWidget(self.cb_live_export_md, 3, 0)
        export_rows.addWidget(self.cb_live_export_srt, 3, 1)
        export_rows.addWidget(self.cb_live_export_vtt, 4, 0)
        exports_group_layout.addLayout(export_rows)

        subtitle_layout = QVBoxLayout()
        subtitle_layout.setSpacing(self._px(2))
        self.cb_live_subtitle_sentence_split = QCheckBox(self._t("По предложениям", "By sentences"))
        self.cb_live_subtitle_sentence_split.setChecked(True)
        self.cb_live_subtitle_sentence_split.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        subtitle_layout.addWidget(self.cb_live_subtitle_sentence_split)
        self.lbl_live_subtitle_max_lines = QLabel(self._t("Строк:", "Lines:"))
        self.spin_live_subtitle_max_lines = QSpinBox()
        self.spin_live_subtitle_max_lines.setRange(1, 4)
        self.spin_live_subtitle_max_lines.setValue(2)
        self.spin_live_subtitle_max_lines.setFixedWidth(self._px(44))
        self.lbl_live_subtitle_max_width = QLabel(self._t("Символов:", "Characters:"))
        self.spin_live_subtitle_max_width = QSpinBox()
        self.spin_live_subtitle_max_width.setRange(20, 100)
        self.spin_live_subtitle_max_width.setValue(64)
        self.spin_live_subtitle_max_width.setFixedWidth(self._px(52))
        subtitle_lines = QHBoxLayout()
        subtitle_lines.setSpacing(self._px(3))
        subtitle_lines.addWidget(self.lbl_live_subtitle_max_lines)
        subtitle_lines.addWidget(self.spin_live_subtitle_max_lines)
        subtitle_lines.addStretch()
        subtitle_width = QHBoxLayout()
        subtitle_width.setSpacing(self._px(3))
        subtitle_width.addWidget(self.lbl_live_subtitle_max_width)
        subtitle_width.addWidget(self.spin_live_subtitle_max_width)
        subtitle_width.addStretch()
        subtitle_layout.addLayout(subtitle_lines)
        subtitle_layout.addLayout(subtitle_width)
        exports_group_layout.addLayout(subtitle_layout)
        parameters_layout.addWidget(self.grp_live_exports)
        parameters_layout.addStretch()
        workspace.addWidget(parameters_pane)

        layout.addLayout(workspace, 1)

        self.combo_live_source.currentIndexChanged.connect(self._update_live_source_controls)
        self.combo_live_diarization.currentIndexChanged.connect(self._update_live_export_controls)
        for checkbox in self.live_export_checkboxes.values():
            checkbox.stateChanged.connect(self._update_live_export_controls)
        self._refresh_live_devices()
        self._update_live_output_folder_label(self.live_output_dir.text())
        self._update_live_source_controls()
        self._update_live_export_controls()
        self._update_live_control_state()
        self.signals.live_status.connect(self._update_live_recorder_display)
        return tab

    def _update_live_recorder_display(self, status) -> None:
        state = getattr(getattr(status, "state", None), "value", "")
        if state == "starting":
            self._live_timer_ticker.stop()
            self._live_timer_elapsed_ms = 0
            self._live_timer_active = False
            self.lbl_live_timer.setText("00:00:00")
        elif state == "recording":
            if not self._live_timer_active:
                self._live_timer_clock.start()
                self._live_timer_active = True
                self._live_timer_ticker.start()
            self.lbl_live_waveform.setText(self._t("Захват аудио", "Capturing audio"))
        elif state == "paused":
            self._pause_live_recorder_timer()
            self.lbl_live_waveform.setText(self._t("Запись на паузе", "Capture paused"))
        elif state in {"stopped", "failed"}:
            self._pause_live_recorder_timer()
            self.lbl_live_waveform.setText(self._t("Аудиосигнал завершён", "Audio capture complete"))

    def _pause_live_recorder_timer(self) -> None:
        if self._live_timer_active:
            self._live_timer_elapsed_ms += self._live_timer_clock.elapsed()
            self._live_timer_active = False
        self._live_timer_ticker.stop()
        self._refresh_live_recorder_timer()

    def _refresh_live_recorder_timer(self) -> None:
        elapsed_ms = self._live_timer_elapsed_ms
        if self._live_timer_active:
            elapsed_ms += self._live_timer_clock.elapsed()
        total_seconds = elapsed_ms // 1000
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        self.lbl_live_timer.setText(f"{hours:02d}:{minutes:02d}:{seconds:02d}")
