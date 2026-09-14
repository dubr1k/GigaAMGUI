"""Compact processing option controls for the Light Liquid Glass workspace."""
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
        group = QGroupBox("Режим")
        group.setObjectName("settings_group")
        self.grp_audio_preprocessing = group
        layout = QHBoxLayout(group)
        layout.setContentsMargins(0, self._px(1), 0, self._px(1))
        layout.setSpacing(self._px(6))
        self.lbl_audio_preprocessing_mode = QLabel("Режим:")
        self.lbl_audio_preprocessing_mode.setObjectName("field_label")
        layout.addWidget(self.lbl_audio_preprocessing_mode)
        self.combo_audio_preprocessing = QComboBox()
        self.combo_audio_preprocessing.addItem("Авто (рекомендуется)", "auto")
        self.combo_audio_preprocessing.addItem("Выключено", "off")
        self.combo_audio_preprocessing.addItem("Лёгкая очистка", "light")
        self.combo_audio_preprocessing.addItem("Шумоподавление", "denoise")
        self.combo_audio_preprocessing.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.combo_audio_preprocessing.setMinimumContentsLength(8)
        self.combo_audio_preprocessing.setFixedHeight(self._px(26))
        self.combo_audio_preprocessing.setToolTip(
            "Авто анализирует качество записи и применяет минимально необходимую обработку"
        )
        default_index = self.combo_audio_preprocessing.findData(AUDIO_PREPROCESSING_MODE)
        self.combo_audio_preprocessing.setCurrentIndex(default_index if default_index >= 0 else 0)
        layout.addWidget(self.combo_audio_preprocessing, 1)
        return group

    def _selected_audio_preprocessing_mode(self) -> str:
        return str(self.combo_audio_preprocessing.currentData() or "auto")

    def _create_diarization_group(self) -> QGroupBox:
        group = QGroupBox("Диаризация")
        group.setObjectName("settings_group")
        self.grp_diarization = group
        layout = QVBoxLayout(group)
        layout.setContentsMargins(0, self._px(1), 0, self._px(1))
        layout.setSpacing(self._px(4))

        top_row = QHBoxLayout()
        self.cb_diarization = QCheckBox("Вкл.")
        self.cb_diarization.setToolTip("Определять, кто из спикеров говорит (нужен HF_TOKEN)")
        self.cb_diarization.stateChanged.connect(self._toggle_diarization)
        top_row.addWidget(self.cb_diarization)
        top_row.addStretch()
        self.btn_hf_token = QPushButton("HF")
        self.btn_hf_token.setObjectName("secondary_button")
        self.btn_hf_token.setToolTip("Открыть настройку токена HuggingFace для диаризации")
        self.btn_hf_token.setFixedHeight(self._px(24))
        self.btn_hf_token.clicked.connect(self._edit_hf_token)
        top_row.addWidget(self.btn_hf_token)
        layout.addLayout(top_row)

        backend_row = QHBoxLayout()
        backend_row.setSpacing(self._px(6))
        self.lbl_diarization_backend = QLabel("Движок:")
        self.lbl_diarization_backend.setObjectName("field_label")
        backend_row.addWidget(self.lbl_diarization_backend)
        self.combo_diarization_backend = QComboBox()
        self.combo_diarization_backend.addItem("Pyannote 3.1", "pyannote")
        self.combo_diarization_backend.addItem("ONNX (PyAnnote + WeSpeaker)", "onnx")
        self.combo_diarization_backend.addItem("NVIDIA Sortformer v2.1", "sortformer")
        self.combo_diarization_backend.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.combo_diarization_backend.setMinimumContentsLength(8)
        self.combo_diarization_backend.setFixedHeight(self._px(26))
        self.combo_diarization_backend.currentIndexChanged.connect(self._change_diarization_backend)
        backend_row.addWidget(self.combo_diarization_backend, 1)
        layout.addLayout(backend_row)

        speakers_row = QHBoxLayout()
        speakers_row.setSpacing(self._px(6))
        self.lbl_num_speakers = QLabel("Спикеров:")
        self.lbl_num_speakers.setObjectName("field_label")
        speakers_row.addWidget(self.lbl_num_speakers)
        self.entry_num_speakers = QSpinBox()
        self.entry_num_speakers.setRange(0, 20)
        self.entry_num_speakers.setValue(0)
        self.entry_num_speakers.setSpecialValueText("Авто")
        self.entry_num_speakers.setToolTip("0 = автоопределение количества спикеров")
        self.entry_num_speakers.setEnabled(False)
        self.entry_num_speakers.setFixedHeight(self._px(26))
        self.entry_num_speakers.setMaximumWidth(self._px(86))
        speakers_row.addWidget(self.entry_num_speakers)
        speakers_row.addStretch()
        layout.addLayout(speakers_row)

        self.lbl_diarization_info = QLabel("Pyannote: автоопределение спикеров (требуется HF_TOKEN)")
        self.lbl_diarization_info.setObjectName("muted_label")
        self.lbl_diarization_info.setWordWrap(True)
        self.lbl_diarization_info.setVisible(False)
        layout.addWidget(self.lbl_diarization_info)
        return group
