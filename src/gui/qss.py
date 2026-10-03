"""Таблица стилей главного окна: чистая функция от палитры и метрик.

Классическая таблица 1.6 плюс правила для виджетов страниц 2.0 (результат,
журнал, API, настройки). Все цвета — из палитры, без захардкоженных hex, поэтому
тёмная тема не требует отдельных override. Метрики приходят функциями окна
(_px/_pt_css), чтобы таблица следовала масштабу интерфейса.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping


def build_stylesheet(
    c: Mapping[str, str],
    px: Callable[[float], int],
    pt_css: Callable[[float], str],
    *,
    tab_min_width: int,
) -> str:
    r = c["progress_chunk"]
    r2 = c["progress_chunk2"]
    rad_f = px(11)
    return f"""
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
            QTabWidget::tab-bar {{
                alignment: center;
            }}
            QTabBar::tab {{
                background-color: {c["tab_bg"]};
                color: {c["tab_text"]};
                border: 1px solid {c["border"]};
                border-bottom: none;
                border-radius: {px(5)}px {px(5)}px 0 0;
                padding: {px(6)}px {px(10)}px;
                font-size: {pt_css(11)}pt;
                margin-right: {px(2)}px;
                min-width: {tab_min_width}px;
            }}
            QTabBar::tab:selected {{
                background-color: {c["tab_sel_bg"]};
                color: {c["tab_sel_text"]};
                font-weight: bold;
                border-bottom: {px(2)}px solid {c["tab_accent"]};
            }}
            QTabBar::tab:hover:!selected {{
                background-color: {c["tab_hover"]};
            }}
            QGroupBox {{
                font-weight: bold;
                font-size: {pt_css(11)}pt;
                border: 1px solid {c["border"]};
                border-radius: {px(8)}px;
                margin-top: {px(18)}px;
                padding-top: {px(10)}px;
                background-color: {c["bg_card"]};
                color: {c["text"]};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: {px(14)}px;
                padding: 0 {px(6)}px 0 {px(6)}px;
                color: {c["text_sub"]};
                background: transparent;
            }}
            QPushButton {{
                background-color: {c["btn_bg"]};
                border: 1px solid {c["btn_border"]};
                border-radius: {px(6)}px;
                padding: {px(7)}px {px(17)}px {px(7)}px {px(17)}px;
                color: {c["btn_text"]};
                font-size: {pt_css(10)}pt;
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
                font-size: {pt_css(13)}pt;
                font-weight: bold;
                border: none;
                border-radius: {px(8)}px;
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
                font-size: {pt_css(10)}pt;
                font-weight: bold;
                border: 1px solid {c["clear_border"]};
                border-radius: {px(6)}px;
            }}
            QPushButton#clear_button:hover {{
                background-color: {c["clear_hover_bg"]};
                border: 1px solid {c["clear_hover_border"]};
                color: {c["clear_hover_text"]};
            }}
            QPushButton#theme_button {{
                background-color: transparent;
                border: 1px solid {c["border"]};
                border-radius: {px(6)}px;
                padding: {px(2)}px {px(8)}px;
                font-size: {pt_css(16)}pt;
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
                font-size: {pt_css(10)}pt;
            }}
            QProgressBar::chunk {{
                background-color: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 {r}, stop:1 {r2});
                border-radius: {rad_f}px;
            }}
            QLineEdit {{
                border: 1px solid {c["btn_border"]};
                border-radius: {px(6)}px;
                padding: {px(7)}px {px(11)}px {px(7)}px {px(11)}px;
                background-color: {c["input_bg"]};
                color: {c["text"]};
                selection-background-color: {c["input_sel"]};
                font-size: {pt_css(10)}pt;
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
                border-radius: {px(6)}px;
                padding: {px(10)}px;
                background-color: {c["input_bg"]};
                color: {c["text"]};
                selection-background-color: {c["input_sel"]};
                font-size: {pt_css(10)}pt;
            }}
            QCheckBox {{
                background: transparent;
                spacing: {px(8)}px;
                color: {c["text_sub"]};
                font-size: {pt_css(10)}pt;
            }}
            QCheckBox::indicator {{
                width: {px(18)}px;
                height: {px(18)}px;
                border: 1.5px solid {c["btn_border"]};
                border-radius: {px(4)}px;
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
            QCheckBox::indicator:checked:disabled {{
                background-color: {c["accent"]};
                border: 1.5px solid {c["accent"]};
            }}
            QLabel {{
                background: transparent;
                color: {c["text_sub"]};
                font-size: {pt_css(10)}pt;
            }}
            QScrollBar:vertical {{
                background: {c["scroll_bg"]};
                width: {px(8)}px;
                border-radius: {px(4)}px;
            }}
            QScrollBar::handle:vertical {{
                background: {c["scroll_handle"]};
                border-radius: {px(4)}px;
                min-height: {px(30)}px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {c["scroll_handle_hover"]};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
            QScrollBar:horizontal {{
                background: {c["scroll_bg"]};
                height: {px(8)}px;
                border-radius: {px(4)}px;
            }}
            QScrollBar::handle:horizontal {{
                background: {c["scroll_handle"]};
                border-radius: {px(4)}px;
                min-width: {px(30)}px;
            }}
            QScrollBar::handle:horizontal:hover {{
                background: {c["scroll_handle_hover"]};
            }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
            #progress_card {{
                background-color: {c["bg_card"]};
                border: 1px solid {c["border"]};
                border-radius: {px(8)}px;
            }}
            #progress_card QLabel {{
                border: none;
                background: transparent;
            }}
            QListWidget#files_list, QListWidget#llm_files_list {{
                background-color: {c["input_bg"]};
                border: 1px solid {c["border"]};
                border-radius: {px(6)}px;
                color: {c["text"]};
                font-size: {pt_css(10)}pt;
                padding: {px(2)}px;
            }}
            QListWidget#files_list::item, QListWidget#llm_files_list::item {{
                padding: {px(3)}px {px(6)}px;
                border-radius: {px(4)}px;
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
                border-radius: {px(6)}px;
                padding: {px(2)}px {px(8)}px;
                background-color: {c["input_bg"]};
                color: {c["text"]};
                selection-background-color: {c["input_sel"]};
                font-size: {pt_css(10)}pt;
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
                font-size: {pt_css(9)}pt;
                font-weight: bold;
                border: 1px solid {c["clear_border"]};
                border-radius: {px(6)}px;
                padding: {px(2)}px {px(12)}px;
            }}
            QPushButton#cancel_button:hover {{
                background-color: {c["clear_hover_bg"]};
                border: 1px solid {c["clear_hover_border"]};
                color: {c["clear_hover_text"]};
            }}
            QPushButton#open_result_button {{
                background-color: transparent;
                color: {c["accent"]};
                font-size: {pt_css(10)}pt;
                font-weight: bold;
                border: 1px solid {c["accent"]};
                border-radius: {px(6)}px;
            }}
            QPushButton#open_result_button:hover {{
                background-color: {c["btn_hover_bg"]};
                border: 1px solid {c["btn_hover_border"]};
            }}
            QMenuBar {{
                background-color: {c["bg_card"]};
                color: {c["text_sub"]};
                border-bottom: 1px solid {c["border"]};
                font-size: {pt_css(10)}pt;
            }}
            QMenuBar::item {{
                background: transparent;
                padding: {px(4)}px {px(10)}px;
            }}
            QMenuBar::item:selected {{
                background-color: {c["btn_hover_bg"]};
                color: {c["btn_hover_text"]};
                border-radius: {px(4)}px;
            }}
            QMenu {{
                background-color: {c["bg_card"]};
                color: {c["text_sub"]};
                border: 1px solid {c["border"]};
                padding: {px(4)}px;
            }}
            QMenu::item {{
                padding: {px(5)}px {px(22)}px;
                border-radius: {px(4)}px;
            }}
            QMenu::item:selected {{
                background-color: {c["accent"]};
                color: #ffffff;
            }}
            QMenu::separator {{
                height: 1px;
                background: {c["border"]};
                margin: {px(4)}px {px(6)}px;
            }}
            QStatusBar {{
                background-color: {c["status_bg"]};
                color: {c["text_mute2"]};
                font-size: {pt_css(9)}pt;
            }}

            /* ── Виджеты страниц из 2.0 (результат, журнал, API, настройки, Live/LLM) ── */
            QLabel#page_title {{ color: {c["text"]}; font-size: {pt_css(16)}pt; font-weight: bold; }}
            QLabel#page_subtitle {{ color: {c["text_mute2"]}; font-size: {pt_css(10)}pt; }}
            QLabel#section_title, QLabel#api_panel_title, QLabel#settings_section_heading {{
                color: {c["text"]}; font-size: {pt_css(11)}pt; font-weight: bold;
            }}
            QLabel#muted_label, QLabel#field_label, QLabel#api_doc_body, QLabel#settings_section_description,
            QLabel#journal_empty {{ color: {c["text_mute2"]}; font-size: {pt_css(10)}pt; }}
            QFrame#glass_panel, QFrame#dense_panel, QFrame#api_status_bar, QFrame#api_examples_panel,
            QFrame#api_documentation_panel, QFrame#settings_form_panel {{
                background-color: {c["bg_card"]}; border: 1px solid {c["border"]}; border-radius: {px(8)}px;
            }}
            /* Compact buttons of the 2.0 pages carry fixed 24–32 px heights; the
               classic 7 px vertical padding would clip their captions. */
            QPushButton#secondary_button, QPushButton#text_button, QPushButton#primary_button,
            QPushButton#live_stop_button, QPushButton#live_record_button,
            QPushButton#llm_upload_button, QPushButton#llm_file_control, QPushButton#llm_result_action,
            QPushButton#llm_settings_button, QPushButton#llm_clear_button, QPushButton#llm_primary_action,
            QPushButton#llm_output_folder_button, QPushButton#api_action_button, QPushButton#api_primary_action,
            QPushButton#api_doc_toggle, QPushButton#settings_secondary_action, QPushButton#settings_path_button,
            QPushButton#journal_filter {{
                padding: {px(2)}px {px(10)}px;
            }}
            QTabWidget#settings_category_tabs QTabBar::tab {{
                min-width: 0; padding: {px(4)}px {px(12)}px; font-size: {pt_css(10)}pt;
            }}
            QPushButton#text_button {{
                background-color: transparent; border: none; color: {c["accent"]};
                padding: {px(2)}px {px(6)}px; font-weight: bold;
            }}
            QPushButton#text_button:hover {{ color: {c["accent2"]}; text-decoration: underline; }}
            QPushButton#primary_button, QPushButton#api_primary_action, QPushButton#llm_primary_action {{
                background-color: {c["accent"]}; color: #ffffff; border: none; font-weight: bold;
            }}
            QPushButton#primary_button:hover, QPushButton#api_primary_action:hover, QPushButton#llm_primary_action:hover {{
                background-color: {c["accent2"]};
            }}
            QPushButton#primary_button:disabled, QPushButton#api_primary_action:disabled,
            QPushButton#llm_primary_action:disabled {{
                background-color: {c["accent_dis"]}; color: {c["text_mute"]};
            }}
            QPushButton#live_record_button {{ background-color: #e05050; color: #ffffff; border: none; font-weight: bold; }}
            QPushButton#live_record_button:hover {{ background-color: #c43c3c; }}
            QPushButton#live_stop_button {{ color: {c["clear_hover_text"]}; border: 1px solid {c["clear_hover_border"]}; }}
            QPushButton#live_record_button:disabled, QPushButton#live_stop_button:disabled {{
                background-color: {c["input_dis"]}; color: {c["text_mute"]}; border: 1px solid {c["border"]};
            }}
            QPushButton#journal_filter:checked, QPushButton#api_doc_toggle:checked {{
                background-color: {c["accent_dis"]}; border: 1px solid {c["accent"]}; color: {c["text"]};
            }}
            QComboBox, QDoubleSpinBox {{
                border: 1px solid {c["btn_border"]}; border-radius: {px(6)}px;
                padding: {px(2)}px {px(8)}px; background-color: {c["input_bg"]};
                color: {c["text"]}; font-size: {pt_css(10)}pt;
            }}
            QComboBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {c["accent"]}; }}
            QComboBox::drop-down {{ border: none; width: {px(22)}px; }}
            QComboBox QAbstractItemView {{
                background-color: {c["bg_card"]}; color: {c["text"]}; border: 1px solid {c["border"]};
                selection-background-color: {c["accent"]}; selection-color: #ffffff;
            }}
            QPlainTextEdit, QTextEdit#result_document, QTextEdit#log_document,
            QTextEdit#llm_source_editor, QTextEdit#llm_result_editor, QPlainTextEdit#api_code_editor {{
                border: 1px solid {c["border"]}; border-radius: {px(6)}px; padding: {px(8)}px;
                background-color: {c["input_bg"]}; color: {c["text"]}; font-size: {pt_css(10)}pt;
            }}
            QTableWidget, QTableView, QTreeView {{
                background-color: {c["input_bg"]}; border: 1px solid {c["border"]}; border-radius: {px(6)}px;
                color: {c["text"]}; alternate-background-color: {c["bg_card"]};
                selection-background-color: {c["input_sel"]}; selection-color: {c["text"]};
                font-size: {pt_css(10)}pt;
            }}
            QHeaderView::section {{
                background-color: {c["bg_card"]}; color: {c["text_sub"]}; border: none;
                border-bottom: 1px solid {c["border"]}; padding: {px(6)}px {px(8)}px; font-weight: bold;
            }}
            QTableCornerButton::section {{ background-color: {c["bg_card"]}; border: none; }}
            QTabWidget#result_tabs::pane, QTabWidget#settings_category_tabs::pane {{
                border: 1px solid {c["border"]}; border-radius: {px(6)}px; background-color: {c["bg_card"]};
            }}
            QSlider::groove:horizontal {{ height: {px(6)}px; background: {c["progress_bg"]}; border-radius: {px(3)}px; }}
            QSlider::sub-page:horizontal {{ background: {c["accent"]}; border-radius: {px(3)}px; }}
            QSlider::handle:horizontal {{
                width: {px(14)}px; margin: -{px(4)}px 0; background: {c["accent"]}; border-radius: {px(7)}px;
            }}
            QDialog, QMessageBox {{ background-color: {c["bg"]}; color: {c["text"]}; }}
            QLineEdit#settings_path_value {{ background-color: {c["input_dis"]}; border: 1px solid {c["border"]}; }}
            QToolTip {{
                background-color: {c["bg_card"]};
                color: {c["text"]};
                border: 1px solid {c["accent"]};
                padding: {px(4)}px;
            }}
        """
