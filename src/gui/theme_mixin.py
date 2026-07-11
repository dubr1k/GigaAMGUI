"""Применение темы (палитра + QSS) для GigaTranscriberQtApp.

Mixin: метод _apply_theme работает со `self` главного окна и его хелперами стилей
(_colors/_px/_pt_css/_font/...), которые определены в app_qt. Поведение 1:1.
"""
from __future__ import annotations

from PyQt6.QtGui import QColor, QPalette


class ThemeMixin:
    def _apply_theme(self):
        c = self._colors()
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window,          QColor(c["bg"]))
        palette.setColor(QPalette.ColorRole.WindowText,      QColor(c["text"]))
        palette.setColor(QPalette.ColorRole.Base,            QColor(c["input_bg"]))
        palette.setColor(QPalette.ColorRole.AlternateBase,   QColor(c["bg_card"]))
        palette.setColor(QPalette.ColorRole.Text,            QColor(c["text"]))
        palette.setColor(QPalette.ColorRole.Button,          QColor(c["btn_bg"]))
        palette.setColor(QPalette.ColorRole.ButtonText,      QColor(c["btn_text"]))
        palette.setColor(QPalette.ColorRole.Highlight,       QColor(c["accent"]))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        self.setPalette(palette)

        r = c["progress_chunk"]
        r2 = c["progress_chunk2"]
        rad_f = self._px(11)
        rad_s = self._px(8)
        tab_min_width = self._tab_min_width(("Обработка", "Журнал обработки"))

        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background-color: {c["bg"]};
                color: {c["text"]};
            }}
            QScrollArea {{
                background-color: {c["bg"]};
                border: none;
            }}
            QTabWidget::pane {{
                border: none;
                background-color: {c["bg"]};
            }}
            QTabBar::tab {{
                background-color: {c["tab_bg"]};
                color: {c["tab_text"]};
                border: 1px solid {c["border"]};
                border-bottom: none;
                border-radius: {self._px(5)}px {self._px(5)}px 0 0;
                padding: {self._px(6)}px {self._px(18)}px;
                font-size: {self._pt_css(11)}pt;
                margin-right: {self._px(2)}px;
                min-width: {tab_min_width}px;
            }}
            QTabBar::tab:selected {{
                background-color: {c["tab_sel_bg"]};
                color: {c["tab_sel_text"]};
                font-weight: bold;
                border-bottom: {self._px(2)}px solid {c["tab_accent"]};
            }}
            QTabBar::tab:hover:!selected {{
                background-color: {c["tab_hover"]};
            }}
            QGroupBox {{
                font-weight: bold;
                font-size: {self._pt_css(11)}pt;
                border: 1px solid {c["border"]};
                border-radius: {self._px(8)}px;
                margin-top: {self._px(18)}px;
                padding-top: {self._px(10)}px;
                background-color: {c["bg_card"]};
                color: {c["text"]};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: {self._px(14)}px;
                padding: 0 {self._px(6)}px 0 {self._px(6)}px;
                color: {c["text_sub"]};
                background: transparent;
            }}
            QPushButton {{
                background-color: {c["btn_bg"]};
                border: 1px solid {c["btn_border"]};
                border-radius: {self._px(6)}px;
                padding: {self._px(7)}px {self._px(17)}px {self._px(7)}px {self._px(17)}px;
                color: {c["btn_text"]};
                font-size: {self._pt_css(10)}pt;
            }}
            QPushButton:hover {{
                background-color: {c["btn_hover_bg"]};
                border: 1px solid {c["btn_hover_border"]};
                color: {c["btn_hover_text"]};
            }}
            QPushButton:pressed {{
                background-color: {c["accent_dis"]};
                border: 1px solid {c["accent2"]};
            }}
            QPushButton:disabled {{
                background-color: {c["input_dis"]};
                color: {c["text_mute"]};
                border: 1px solid {c["border"]};
            }}
            QPushButton#start_button {{
                background-color: {c["accent"]};
                color: #ffffff;
                font-size: {self._pt_css(13)}pt;
                font-weight: bold;
                border: none;
                border-radius: {self._px(8)}px;
            }}
            QPushButton#start_button:hover {{
                background-color: {c["accent2"]};
            }}
            QPushButton#start_button:pressed {{
                background-color: {c["accent3"]};
            }}
            QPushButton#start_button:disabled {{
                background-color: {c["accent_dis"]};
                color: #ffffff;
            }}
            QPushButton#clear_button {{
                background-color: {c["clear_bg"]};
                color: {c["clear_text"]};
                font-size: {self._pt_css(10)}pt;
                font-weight: bold;
                border: 1px solid {c["clear_border"]};
                border-radius: {self._px(6)}px;
            }}
            QPushButton#clear_button:hover {{
                background-color: {c["clear_hover_bg"]};
                border: 1px solid {c["clear_hover_border"]};
                color: {c["clear_hover_text"]};
            }}
            QPushButton#theme_button {{
                background-color: transparent;
                border: 1px solid {c["border"]};
                border-radius: {self._px(6)}px;
                padding: {self._px(2)}px {self._px(8)}px;
                font-size: {self._pt_css(16)}pt;
                color: {c["text_sub"]};
            }}
            QPushButton#theme_button:hover {{
                background-color: {c["btn_hover_bg"]};
                border: 1px solid {c["btn_hover_border"]};
            }}
            QProgressBar {{
                border: none;
                border-radius: {rad_f}px;
                text-align: center;
                background-color: {c["progress_bg"]};
                color: {c["text"]};
                font-size: {self._pt_css(10)}pt;
            }}
            QProgressBar::chunk {{
                background-color: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 {r}, stop:1 {r2});
                border-radius: {rad_f}px;
            }}
            QLineEdit {{
                border: 1px solid {c["btn_border"]};
                border-radius: {self._px(6)}px;
                padding: {self._px(7)}px {self._px(11)}px {self._px(7)}px {self._px(11)}px;
                background-color: {c["input_bg"]};
                color: {c["text"]};
                selection-background-color: {c["input_sel"]};
                font-size: {self._pt_css(10)}pt;
            }}
            QLineEdit:focus {{
                border: 1px solid {c["accent"]};
            }}
            QLineEdit:disabled {{
                background-color: {c["input_dis"]};
                color: {c["input_dis_text"]};
            }}
            QTextEdit {{
                border: 1px solid {c["border"]};
                border-radius: {self._px(6)}px;
                padding: {self._px(10)}px;
                background-color: {c["input_bg"]};
                color: {c["text"]};
                selection-background-color: {c["input_sel"]};
                font-size: {self._pt_css(10)}pt;
            }}
            QCheckBox {{
                background: transparent;
                spacing: {self._px(8)}px;
                color: {c["text_sub"]};
                font-size: {self._pt_css(10)}pt;
            }}
            QCheckBox::indicator {{
                width: {self._px(18)}px;
                height: {self._px(18)}px;
                border: 1.5px solid {c["btn_border"]};
                border-radius: {self._px(4)}px;
                background-color: {c["input_bg"]};
            }}
            QCheckBox::indicator:checked {{
                background-color: {c["accent"]};
                border: 1.5px solid {c["accent"]};
            }}
            QCheckBox::indicator:hover {{
                border: 1.5px solid {c["accent"]};
            }}
            QCheckBox:disabled {{
                color: {c["text_mute"]};
            }}
            QCheckBox::indicator:disabled {{
                background-color: {c["input_dis"]};
                border: 1.5px solid {c["border"]};
            }}
            QLabel {{
                background: transparent;
                color: {c["text_sub"]};
                font-size: {self._pt_css(10)}pt;
            }}
            QScrollBar:vertical {{
                background: {c["scroll_bg"]};
                width: {self._px(8)}px;
                border-radius: {self._px(4)}px;
            }}
            QScrollBar::handle:vertical {{
                background: {c["scroll_handle"]};
                border-radius: {self._px(4)}px;
                min-height: {self._px(30)}px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {c["scroll_handle_hover"]};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
            QScrollBar:horizontal {{
                background: {c["scroll_bg"]};
                height: {self._px(8)}px;
                border-radius: {self._px(4)}px;
            }}
            QScrollBar::handle:horizontal {{
                background: {c["scroll_handle"]};
                border-radius: {self._px(4)}px;
                min-width: {self._px(30)}px;
            }}
            QScrollBar::handle:horizontal:hover {{
                background: {c["scroll_handle_hover"]};
            }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
            #progress_card {{
                background-color: {c["bg_card"]};
                border: 1px solid {c["border"]};
                border-radius: {self._px(8)}px;
            }}
            #progress_card QLabel {{
                border: none;
                background: transparent;
            }}
            QListWidget#files_list, QListWidget#llm_files_list {{
                background-color: {c["input_bg"]};
                border: 1px solid {c["border"]};
                border-radius: {self._px(6)}px;
                color: {c["text"]};
                font-size: {self._pt_css(10)}pt;
                padding: {self._px(2)}px;
            }}
            QListWidget#files_list::item, QListWidget#llm_files_list::item {{
                padding: {self._px(3)}px {self._px(6)}px;
                border-radius: {self._px(4)}px;
            }}
            QListWidget#files_list::item:selected, QListWidget#llm_files_list::item:selected {{
                background-color: {c["accent"]};
                color: #ffffff;
            }}
            QListWidget#files_list::item:hover:!selected, QListWidget#llm_files_list::item:hover:!selected {{
                background-color: {c["btn_hover_bg"]};
            }}
            QSpinBox {{
                border: 1px solid {c["btn_border"]};
                border-radius: {self._px(6)}px;
                padding: {self._px(2)}px {self._px(8)}px;
                background-color: {c["input_bg"]};
                color: {c["text"]};
                selection-background-color: {c["input_sel"]};
                font-size: {self._pt_css(10)}pt;
            }}
            QSpinBox:focus {{
                border: 1px solid {c["accent"]};
            }}
            QSpinBox:disabled {{
                background-color: {c["input_dis"]};
                color: {c["input_dis_text"]};
            }}
            QPushButton#cancel_button {{
                background-color: {c["clear_bg"]};
                color: {c["clear_text"]};
                font-size: {self._pt_css(9)}pt;
                font-weight: bold;
                border: 1px solid {c["clear_border"]};
                border-radius: {self._px(6)}px;
                padding: {self._px(2)}px {self._px(12)}px;
            }}
            QPushButton#cancel_button:hover {{
                background-color: {c["clear_hover_bg"]};
                border: 1px solid {c["clear_hover_border"]};
                color: {c["clear_hover_text"]};
            }}
            QPushButton#open_result_button {{
                background-color: transparent;
                color: {c["accent"]};
                font-size: {self._pt_css(10)}pt;
                font-weight: bold;
                border: 1px solid {c["accent"]};
                border-radius: {self._px(6)}px;
            }}
            QPushButton#open_result_button:hover {{
                background-color: {c["btn_hover_bg"]};
                border: 1px solid {c["btn_hover_border"]};
            }}
            QMenuBar {{
                background-color: {c["bg_card"]};
                color: {c["text_sub"]};
                border-bottom: 1px solid {c["border"]};
                font-size: {self._pt_css(10)}pt;
            }}
            QMenuBar::item {{
                background: transparent;
                padding: {self._px(4)}px {self._px(10)}px;
            }}
            QMenuBar::item:selected {{
                background-color: {c["btn_hover_bg"]};
                color: {c["btn_hover_text"]};
                border-radius: {self._px(4)}px;
            }}
            QMenu {{
                background-color: {c["bg_card"]};
                color: {c["text_sub"]};
                border: 1px solid {c["border"]};
                padding: {self._px(4)}px;
            }}
            QMenu::item {{
                padding: {self._px(5)}px {self._px(22)}px;
                border-radius: {self._px(4)}px;
            }}
            QMenu::item:selected {{
                background-color: {c["accent"]};
                color: #ffffff;
            }}
            QMenu::separator {{
                height: 1px;
                background: {c["border"]};
                margin: {self._px(4)}px {self._px(6)}px;
            }}
            QStatusBar {{
                background-color: {c["status_bg"]};
                color: {c["text_mute2"]};
                font-size: {self._pt_css(9)}pt;
            }}
            QToolTip {{
                background-color: {c["bg_card"]};
                color: {c["text"]};
                border: 1px solid {c["accent"]};
                padding: {self._px(4)}px;
            }}
        """)
        self._style_drop_hint()

        # Обновляем динамические стили прогресс-баров файла (тонкий)
        if hasattr(self, 'progress_bar_file'):
            self.progress_bar_file.setStyleSheet(
                f"QProgressBar {{ border: none; border-radius: {rad_s}px;"
                f"  background-color: {c['progress_bg']}; text-align: center;"
                f"  color: {c['text']}; font-size: {self._pt_css(8)}pt; font-weight: 600; }}"
                f"QProgressBar::chunk {{ border-radius: {rad_s}px;"
                f"  background-color: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                f"  stop:0 {r}, stop:1 {r2}); }}"
            )
        if hasattr(self, 'progress_bar_total'):
            self.progress_bar_total.setStyleSheet(
                f"QProgressBar {{ border: none; border-radius: {rad_f}px;"
                f"  background-color: {c['progress_bg']}; text-align: center;"
                f"  color: {c['text']}; font-size: {self._pt_css(10)}pt; font-weight: 600; }}"
                f"QProgressBar::chunk {{ border-radius: {rad_f}px;"
                f"  background-color: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                f"  stop:0 {r}, stop:1 {r2}); }}"
            )
        if hasattr(self, 'progress_upload'):
            self.progress_upload.setStyleSheet(
                f"QProgressBar {{ border: none; background-color: {c['progress_bg']};"
                f"  border-radius: {self._px(3)}px; }}"
                f"QProgressBar::chunk {{ background-color: {c['accent']}; border-radius: {self._px(3)}px; }}"
            )
        if hasattr(self, 'lbl_file_counter'):
            self.lbl_file_counter.setStyleSheet(
                f"color: {c['accent']}; font-size: {self._pt_css(11)}pt; font-weight: bold;"
            )
        if hasattr(self, 'lbl_status'):
            # Прозрачный фон: строка статуса сливается с карточкой, а не выглядит
            # как тёмная «вдавленная» рамка (status_bg темнее bg_card).
            self.lbl_status.setStyleSheet(
                f"color: {c['status_text']}; font-size: {self._pt_css(10)}pt; font-weight: bold;"
                f"background: transparent; padding: {self._px(2)}px;"
            )
        if hasattr(self, 'lbl_stage'):
            self.lbl_stage.setStyleSheet(
                self._transparent_label_style(c["text_sub"], font_pt=9, font_weight="600")
            )
        if hasattr(self, 'lbl_current_file'):
            self.lbl_current_file.setStyleSheet(self._transparent_label_style(c["text_mute2"], font_pt=9))
