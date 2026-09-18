"""Виджеты параметров подготовки аудио и диаризации."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from ..config import AUDIO_PREPROCESSING_MODE


class ProcessingOptionsUiMixin:
    def _create_audio_preprocessing_group(self) -> QGroupBox:
        group = QGroupBox("3. Подготовка аудио")
        self.grp_audio_preprocessing = group
        layout = QHBoxLayout()
        layout.setContentsMargins(self._px(12), self._px(8), self._px(12), self._px(10))
        layout.setSpacing(self._px(12))

        self.lbl_audio_preprocessing_mode = QLabel("Режим:")
        layout.addWidget(self.lbl_audio_preprocessing_mode)

        self.combo_audio_preprocessing = QComboBox()
        self.combo_audio_preprocessing.addItem("Авто (рекомендуется)", "auto")
        self.combo_audio_preprocessing.addItem("Выключено", "off")
        self.combo_audio_preprocessing.addItem("Лёгкая очистка", "light")
        self.combo_audio_preprocessing.addItem("Шумоподавление", "denoise")
        self.combo_audio_preprocessing.setFixedHeight(self._px(32))
        self.combo_audio_preprocessing.setMinimumWidth(self._px(260))
        self.combo_audio_preprocessing.setToolTip(
            "Авто анализирует качество записи и применяет минимально необходимую обработку"
        )
        default_index = self.combo_audio_preprocessing.findData(AUDIO_PREPROCESSING_MODE)
        self.combo_audio_preprocessing.setCurrentIndex(default_index if default_index >= 0 else 0)
        layout.addWidget(self.combo_audio_preprocessing)
        layout.addStretch()

        group.setLayout(layout)
        return group

    def _selected_audio_preprocessing_mode(self) -> str:
        return str(self.combo_audio_preprocessing.currentData() or "auto")

    def _create_diarization_group(self) -> QGroupBox:
        group = QGroupBox("4. Диаризация спикеров")
        self.grp_diarization = group
        layout = QVBoxLayout()
        layout.setContentsMargins(self._px(12), self._px(8), self._px(12), self._px(10))
        layout.setSpacing(self._px(8))
        token_row = QHBoxLayout()
        token_row.setSpacing(self._px(12))
        self.cb_diarization = QCheckBox("Включить диаризацию спикеров")
        self.cb_diarization.setToolTip("Определять, кто из спикеров говорит (нужен HF_TOKEN)")
        self.cb_diarization.stateChanged.connect(self._toggle_diarization)
        token_row.addWidget(self.cb_diarization)
        token_row.addStretch()
        self.btn_hf_token = QPushButton("Указать / изменить HF-токен")
        self.btn_hf_token.setToolTip("Открыть настройку токена HuggingFace для диаризации")
        self.btn_hf_token.setFixedHeight(self._px(32))
        self.btn_hf_token.clicked.connect(self._edit_hf_token)
        token_row.addWidget(self.btn_hf_token)
        layout.addLayout(token_row)

        backend_layout = QHBoxLayout()
        backend_layout.setSpacing(self._px(12))
        self.lbl_diarization_backend = QLabel("Движок:")
        backend_layout.addWidget(self.lbl_diarization_backend)
        self.combo_diarization_backend = QComboBox()
        self.combo_diarization_backend.addItem("Pyannote 3.1", "pyannote")
        self.combo_diarization_backend.addItem("ONNX (PyAnnote + WeSpeaker)", "onnx")
        self.combo_diarization_backend.addItem("NVIDIA Sortformer v2.1", "sortformer")
        self.combo_diarization_backend.setFixedHeight(self._px(32))
        self.combo_diarization_backend.setMinimumWidth(self._px(220))
        self.combo_diarization_backend.currentIndexChanged.connect(self._change_diarization_backend)
        backend_layout.addWidget(self.combo_diarization_backend)
        backend_layout.addStretch()
        layout.addLayout(backend_layout)

        speakers_layout = QHBoxLayout()
        speakers_layout.setSpacing(self._px(12))
        self.lbl_num_speakers = QLabel("Кол-во спикеров:")
        speakers_layout.addWidget(self.lbl_num_speakers)
        self.entry_num_speakers = QSpinBox()
        self.entry_num_speakers.setRange(0, 20)
        self.entry_num_speakers.setValue(0)
        self.entry_num_speakers.setSpecialValueText("Авто")
        self.entry_num_speakers.setToolTip("0 = автоопределение количества спикеров")
        self.entry_num_speakers.setEnabled(False)
        self.entry_num_speakers.setFixedHeight(self._px(32))
        self.entry_num_speakers.setMinimumWidth(self._px(140))
        self.entry_num_speakers.setMaximumWidth(self._px(200))
        speakers_layout.addWidget(self.entry_num_speakers)
        speakers_layout.addStretch()
        layout.addLayout(speakers_layout)

        self.lbl_diarization_info = QLabel("Автоматическое определение спикеров (требуется HF_TOKEN)")
        self.lbl_diarization_info.setStyleSheet(
            self._transparent_label_style(self._colors()["text_mute2"], font_pt=9)
        )
        layout.addWidget(self.lbl_diarization_info)
        group.setLayout(layout)
        return group
