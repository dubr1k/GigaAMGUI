"""Application-wide Light Liquid Glass Qt stylesheet."""
from __future__ import annotations

from PyQt6.QtGui import QColor, QPalette


class ThemeMixin:
    def _apply_theme(self):
        c = self._colors()
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(c["bg"]))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(c["text"]))
        palette.setColor(QPalette.ColorRole.Base, QColor(c["input_bg"]))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(c["bg_card"]))
        palette.setColor(QPalette.ColorRole.Text, QColor(c["text"]))
        palette.setColor(QPalette.ColorRole.Button, QColor(c["btn_bg"]))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(c["btn_text"]))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(c["accent"]))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
        self.setPalette(palette)
        p = self._px
        pt = self._pt_css
        tab_min_width = self._tab_min_width(
            ("Транскрипция", "Субтитры (SRT)", "Диаризация", "Резюме", "JSON")
        )
        dark_overrides = ""
        if self._theme == "dark":
            dark_overrides = f"""
                QMainWindow, QWidget {{ background: {c['bg']}; color: {c['text']}; }}
                QWidget#app_background {{ background: {c['bg']}; }}
                QFrame#app_window, QFrame#glass_panel, QFrame#dense_panel,
                QFrame#processing_settings_panel, QGroupBox#glass_panel,
                QGroupBox#dense_panel, QFrame#progress_card,
                QFrame#live_source_card, QFrame#live_output_card,
                QFrame#live_recorder_card, QFrame#live_transcript_card,
                QFrame#live_parameters_card, QFrame#llm_source_panel,
                QFrame#llm_templates_panel, QFrame#llm_result_panel,
                QFrame#api_status_bar, QFrame#api_examples_panel,
                QFrame#api_documentation_panel, QFrame#settings_form_panel {{
                    background: {c['bg_card']}; border-color: {c['border']};
                }}
                QFrame#window_chrome, QFrame#sidebar {{ background: {c['bg_card']}; border-color: {c['border']}; }}
                QLabel#window_title, QLabel#brand_title, QLabel#page_title,
                QLabel#section_title, QLabel#api_panel_title,
                QLabel#settings_section_heading {{ color: {c['text']}; }}
                QLabel#brand_subtitle, QLabel#sidebar_footer, QLabel#muted_label,
                QLabel#page_subtitle, QLabel#field_label, QLabel#toolbar_context,
                QLabel#api_doc_body, QLabel#settings_section_description {{ color: {c['text_sub']}; }}
                QLineEdit, QComboBox, QSpinBox, QTextEdit, QPlainTextEdit,
                QListWidget, QTableWidget, QTableView, QTreeView {{
                    background: {c['input_bg']}; color: {c['text']}; border-color: {c['border']};
                }}
                QPushButton {{ background: {c['btn_bg']}; color: {c['btn_text']}; border-color: {c['btn_border']}; }}
                QPushButton:hover {{ background: {c['btn_hover_bg']}; color: {c['btn_hover_text']}; border-color: {c['btn_hover_border']}; }}
                QPushButton:disabled {{ background: {c['input_dis']}; color: {c['input_dis_text']}; border-color: {c['border']}; }}
                QPushButton#nav_button {{ color: {c['text_sub']}; }}
                QPushButton#nav_button:checked, QPushButton#journal_filter:checked,
                QTabWidget#settings_category_tabs QTabBar::tab:selected {{
                    background: {c['accent_dis']}; color: {c['text']}; border-color: {c['accent']};
                }}
                QComboBox QAbstractItemView, QAbstractItemView, QMenu,
                QDialog, QMessageBox, QToolTip {{
                    background: {c['bg_card']}; color: {c['text']}; border-color: {c['border']};
                    selection-background-color: {c['input_sel']}; selection-color: {c['text']};
                }}
                QHeaderView::section, QTableCornerButton::section {{
                    background: {c['tab_bg']}; color: {c['tab_text']}; border-color: {c['border']};
                }}
                QStatusBar {{ background: {c['status_bg']}; color: {c['status_text']}; border-color: {c['border']}; }}
                QScrollBar::handle:vertical {{ background: {c['scroll_handle']}; }}
            """
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{ background: {c['bg']}; color: #172033; font-family: -apple-system, "SF Pro Text", "Segoe UI", Arial; }}
            QWidget#app_background {{ background: qradialgradient(cx:0.08, cy:0.02, radius:1.18, fx:0.08, fy:0.02, stop:0 #C8E2FF, stop:0.34 #EDF7FF, stop:0.70 #F7FAFC, stop:1 #E6F0FA); }}
            QFrame#app_window {{ background: rgba(255,255,255,0.76); border: 1px solid rgba(255,255,255,0.80); border-radius: {p(16)}px; }}
            QFrame#window_chrome {{ background: rgba(255,255,255,0.40); border: none; border-bottom: 1px solid rgba(208,221,235,0.66); border-top-left-radius: {p(16)}px; border-top-right-radius: {p(16)}px; }}
            QLabel#window_dot {{ font-size: {pt(8)}pt; }}
            QLabel#window_title {{ color: #24324A; font-size: {pt(9)}pt; font-weight: 600; margin-left: {p(4)}px; }}
            QLabel {{ background: transparent; }}
            QFrame#sidebar {{ background: rgba(248,252,255,0.46); border: none; border-right: 1px solid rgba(208,221,235,0.66); border-bottom-left-radius: {p(16)}px; }}
            QFrame#glass_panel, QFrame#dense_panel, QFrame#processing_settings_panel, QGroupBox#glass_panel, QGroupBox#dense_panel {{ background: rgba(255,255,255,0.68); border: 1px solid rgba(214,226,239,0.84); border-radius: {p(10)}px; }}
            QFrame#progress_card {{ background: rgba(255,255,255,0.56); border: 1px solid rgba(214,226,239,0.72); border-radius: {p(10)}px; }}
            QLabel#result_segment {{ color: #253248; font-size: {pt(9)}pt; }}
            QLabel#speaker_badge {{ background: #E8F4FF; color: #1677D2; border: 1px solid #BFE0FF; border-radius: {p(8)}px; padding: {p(2)}px {p(6)}px; font-size: {pt(8)}pt; }}
            QWidget#content_shell, QWidget#processing_page, QWidget#processing_result_page, QWidget#log_page {{ background: transparent; }}
            QLabel#brand_mark {{ background: transparent; color: #198DF0; border: none; font-size: {pt(11)}pt; font-weight: 700; }}
            QLabel#brand_title {{ color: #172033; font-size: {pt(9)}pt; font-weight: 700; }}
            QLabel#brand_subtitle, QLabel#sidebar_footer, QLabel#muted_label {{ color: #718096; font-size: {pt(7)}pt; }}
            QLabel#page_title {{ color: #172033; font-size: {pt(16)}pt; font-weight: 600; }}
            QLabel#page_subtitle {{ color: #718096; font-size: {pt(9)}pt; }}
            QLabel#section_title {{ color: #202C40; font-size: {pt(9)}pt; font-weight: 600; }}
            QLabel#field_label {{ color: #526276; font-size: {pt(8)}pt; font-weight: 500; }}
            QLabel#toolbar_context {{ color: #526276; font-size: {pt(9)}pt; font-weight: 500; }}
            QPushButton#nav_button {{ background: transparent; border: 1px solid transparent; border-radius: {p(7)}px; color: #324156; padding: 0 {p(9)}px; text-align: left; font-size: {pt(8)}pt; }}
            QPushButton#nav_button:hover {{ background: rgba(230,243,255,0.64); }}
            QPushButton#nav_button:checked {{ background: #E6F3FF; border-color: #D4ECFF; color: #1677D2; font-weight: 600; }}
            QLineEdit, QComboBox, QSpinBox {{ min-height: {p(24)}px; background: rgba(255,255,255,0.74); border: 1px solid #D8E4EF; border-radius: {p(6)}px; padding: 0 {p(8)}px; color: #253248; font-size: {pt(8)}pt; }}
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: #67B8FF; }}
            QLineEdit#toolbar_search {{ min-width: {p(270)}px; }}
            QComboBox::drop-down {{ border: none; width: {p(20)}px; }}
            QComboBox QAbstractItemView {{ background: #FFFFFF; border: 1px solid {c['border']}; color: {c['text']}; selection-background-color: #EAF4FF; selection-color: {c['text']}; }}
            QPushButton {{ min-height: {p(26)}px; background: rgba(255,255,255,0.64); border: 1px solid #D8E4EF; border-radius: {p(6)}px; padding: 0 {p(10)}px; color: #2B384D; font-size: {pt(8)}pt; }}
            QPushButton:hover {{ background: #F7FBFF; border-color: #B8D8F4; }}
            QPushButton:disabled {{ background: #F8FAFC; border-color: #E5E9EF; color: #B8C0CC; }}
            QPushButton#primary_button, QPushButton#start_button {{ background: #2D95F7; border-color: #2D95F7; border-radius: {p(7)}px; color: #FFFFFF; padding: 0 {p(14)}px; font-weight: 600; }}
            QPushButton#primary_button:hover, QPushButton#start_button:hover {{ background: #147FD9; border-color: #147FD9; }}
            QPushButton#secondary_button {{ background: rgba(255,255,255,0.64); border-color: #D8E4EF; border-radius: {p(6)}px; }}
            QPushButton#text_button {{ min-height: {p(22)}px; background: transparent; border: none; color: #1677D2; padding: 0 {p(4)}px; }}
            QPushButton#toolbar_icon_button, QPushButton#chrome_button {{ border: none; background: transparent; border-radius: {p(12)}px; padding: 0; color: #43536A; }}
            QPushButton#profile_button {{ min-height: 0; background: #E8EEF5; border: none; border-radius: {p(13)}px; padding: 0; color: #43536A; font-size: {pt(7)}pt; }}
            QPushButton#live_record_button {{ background: #F15A5A; border-color: #F15A5A; color: white; border-radius: {p(18)}px; }}
            QPushButton#live_stop_button {{ background: #FFF1F2; border-color: #FECACA; color: #B42318; border-radius: {p(18)}px; }}
            QGroupBox#glass_panel, QGroupBox#dense_panel {{ margin-top: {p(9)}px; padding-top: {p(4)}px; }}
            QGroupBox#glass_panel::title, QGroupBox#dense_panel::title {{ subcontrol-origin: margin; left: {p(12)}px; padding: 0 {p(3)}px; color: #202C40; font-size: {pt(8)}pt; font-weight: 600; }}
            QGroupBox::title {{
                background: transparent;
            }}
            QGroupBox#settings_group {{ border: none; margin-top: {p(6)}px; padding-top: {p(3)}px; }}
            QGroupBox#settings_group::title {{ subcontrol-origin: margin; left: 0; padding: 0; color: #324156; font-size: {pt(8)}pt; font-weight: 600; }}
            QCheckBox {{ background: transparent; color: #3B4A5E; spacing: {p(6)}px; font-size: {pt(8)}pt; }}
            QCheckBox::indicator {{ width: {p(13)}px; height: {p(13)}px; border: 1px solid #BDD1E3; border-radius: {p(3)}px; background: #FFFFFF; }}
            QCheckBox::indicator:checked {{ background: #2D95F7; border-color: #2D95F7; }}
            QCheckBox::indicator:checked:disabled {{ background: #8BC9FF; border-color: #8BC9FF; }}
            QScrollArea, QTabWidget::pane {{ background: transparent; border: none; }}
            QTabWidget#main_tabs::pane {{ border: none; }}
            QTabBar::tab {{ min-width: {tab_min_width}px; }}
            QTabWidget#result_tabs::pane {{ border: 1px solid #D6E2EE; border-radius: {p(8)}px; background: rgba(255,255,255,0.60); top: -1px; }}
            QTabWidget#result_tabs QTabBar::tab {{ border: none; background: rgba(244,248,252,0.78); color: #65758A; padding: {p(6)}px {p(9)}px; margin-right: {p(2)}px; font-size: {pt(8)}pt; }}
            QTabWidget#result_tabs QTabBar::tab:selected {{ color: #1677D2; background: #E6F3FF; border-radius: {p(5)}px; font-weight: 600; }}
            QListWidget#files_list {{ background: transparent; border: none; color: #253248; padding: 0; }}
            QListWidget#files_list::item {{ min-height: {p(30)}px; padding: 0 {p(8)}px; border-bottom: 1px solid #E8EFF6; }}
            QListWidget#files_list::item:selected {{ background: #E6F3FF; color: #253248; }}
            QTextEdit#result_document, QTextEdit#log_document {{ background: transparent; border: none; padding: {p(9)}px; color: #253248; font-size: {pt(8)}pt; }}
            QProgressBar {{ border: none; border-radius: {p(3)}px; background: #E6EEF5; color: transparent; }}
            QProgressBar::chunk {{ background: #2D95F7; border-radius: {p(3)}px; }}
            QSlider#waveform_timeline::groove:horizontal {{ height: {p(12)}px; background: #DBE7F1; border-radius: {p(6)}px; }}
            QSlider#waveform_timeline::sub-page:horizontal {{ background: #78BDF8; border-radius: {p(6)}px; }}
            QSlider#waveform_timeline::handle:horizontal {{ width: {p(8)}px; margin: -{p(5)}px 0; background: #258CF4; border-radius: {p(4)}px; }}
            QSlider#volume_slider::groove:horizontal {{ height: {p(3)}px; background: #CBDCEB; border-radius: {p(2)}px; }}
            QSlider#volume_slider::sub-page:horizontal {{ background: #2D95F7; border-radius: {p(2)}px; }}
            QSlider#volume_slider::handle:horizontal {{ width: {p(8)}px; margin: -{p(3)}px 0; background: #2D95F7; border-radius: {p(4)}px; }}
            QScrollBar:vertical {{ width: {p(6)}px; background: transparent; }}
            QScrollBar::handle:vertical {{ min-height: {p(24)}px; background: #BFD0DF; border-radius: {p(3)}px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QStatusBar {{ background: rgba(255,255,255,0.56); color: #718096; border-top: 1px solid rgba(214,226,239,0.72); font-size: {pt(8)}pt; padding-left: {p(10)}px; }}
            QTableWidget, QTableView, QTreeView {{ background: rgba(255,255,255,0.58); border: 1px solid #D6E2EE; border-radius: {p(8)}px; color: #253248; alternate-background-color: #F8FBFD; selection-background-color: #E6F3FF; selection-color: #253248; }}
            QTableWidget#journal_table {{ background: rgba(255,255,255,0.58); border-color: #D6E2EE; }}
            QTableWidget#journal_table::item {{ border-bottom: 1px solid #E8EFF6; padding: 0 {p(8)}px; }}
            QTableWidget#journal_table::item:selected {{ background: #E6F3FF; color: #253248; }}
            QHeaderView::section {{ background: #F5F9FC; border: none; border-bottom: 1px solid #E3EBF2; color: #718096; font-size: {pt(8)}pt; font-weight: 600; padding: {p(7)}px {p(8)}px; }}
            QTableCornerButton::section {{ background: #F5F9FC; border: none; border-bottom: 1px solid #E3EBF2; }}
            QLabel#journal_empty {{ color: #718096; font-size: {pt(9)}pt; padding: {p(20)}px; }}
            QLabel#journal_status_dot {{ font-size: {pt(9)}pt; }}
            QPushButton#journal_filter {{ min-height: {p(24)}px; background: rgba(255,255,255,0.56); border: 1px solid #D8E4EF; border-radius: {p(6)}px; padding: 0 {p(8)}px; color: #526276; font-size: {pt(8)}pt; }}
            QPushButton#journal_filter:hover {{ background: #F7FBFF; border-color: #B8D8F4; }}
            QPushButton#journal_filter:checked {{ background: #E6F3FF; border-color: #C5E2FF; color: #1677D2; font-weight: 600; }}
            QWidget#live_workspace, QWidget#llm_workspace {{ background: transparent; }}
            QFrame#live_source_card, QFrame#live_output_card, QFrame#live_recorder_card, QFrame#live_transcript_card, QFrame#live_parameters_card, QFrame#llm_source_panel, QFrame#llm_templates_panel, QFrame#llm_result_panel {{ background: rgba(255,255,255,0.66); border: 1px solid #D6E2EE; border-radius: {p(10)}px; }}
            QFrame#live_recorder_card {{ background: rgba(255,255,255,0.78); }}
            QPushButton#llm_primary_action {{ background: #2D95F7; border-color: #2D95F7; color: white; border-radius: {p(7)}px; font-weight: 600; }}
            QFrame#llm_drop_zone {{ background: rgba(247,251,255,0.72); border: 1px dashed #BFD9F2; border-radius: {p(8)}px; }}
            QTextEdit#llm_source_editor, QTextEdit#llm_result_editor {{ background: rgba(255,255,255,0.48); border: 1px solid #D8E4EF; border-radius: {p(6)}px; color: #253248; font-size: {pt(8)}pt; }}
            QFrame#llm_export_bar, QFrame#llm_result_toolbar {{ background: transparent; border: none; }}
            QPushButton#llm_result_action {{ min-height: {p(24)}px; background: rgba(255,255,255,0.58); border-color: #D8E4EF; }}
            QFrame#api_status_bar {{ background: rgba(255,255,255,0.66); border: 1px solid #D6E2EE; border-radius: {p(10)}px; }}
            QFrame#api_examples_panel, QFrame#api_documentation_panel, QFrame#settings_form_panel {{ background: rgba(255,255,255,0.66); border: 1px solid #D6E2EE; border-radius: {p(10)}px; }}
            QLabel#api_panel_title, QLabel#settings_section_heading {{ color: #202C40; font-size: {pt(9)}pt; font-weight: 600; }}
            QPlainTextEdit#api_code_editor {{ background: #F4F8FC; border: 1px solid #E0EAF3; border-radius: {p(7)}px; color: #2B384D; font-size: {pt(8)}pt; }}
            QPushButton#api_primary_action {{ background: #2D95F7; border-color: #2D95F7; color: #FFFFFF; font-weight: 600; }}
            QPushButton#api_action_button, QPushButton#settings_secondary_action {{ min-height: {p(24)}px; }}
            QPushButton#api_doc_toggle {{ background: transparent; border: none; border-bottom: 1px solid #E6EEF5; border-radius: 0; color: #33435A; text-align: left; font-weight: 500; }}
            QLabel#api_doc_body, QLabel#settings_section_description {{ color: #718096; font-size: {pt(8)}pt; }}
            QFrame#settings_category_bar {{ background: transparent; border: none; }}
            QTabWidget#settings_category_tabs QTabBar::tab {{ min-height: {p(25)}px; border: none; border-radius: {p(6)}px; margin-right: {p(5)}px; padding: 0 {p(9)}px; background: rgba(244,248,252,0.72); color: #526276; font-size: {pt(8)}pt; }}
            QTabWidget#settings_category_tabs QTabBar::tab:selected {{ background: #2D95F7; color: #FFFFFF; font-weight: 600; }}
            QLineEdit#settings_path_value {{ background: rgba(255,255,255,0.48); border: none; }}
            QDialog, QMessageBox {{ background: rgba(255,255,255,0.96); border: 1px solid #D6E2EE; border-radius: {p(12)}px; color: #253248; }}
            QDialog QLabel, QMessageBox QLabel {{ background: transparent; }}
            QDialogButtonBox {{ button-layout: 3; }}
            QDialogButtonBox QPushButton, QMessageBox QPushButton {{ min-width: {p(78)}px; min-height: {p(28)}px; border-radius: {p(7)}px; }}
            QToolTip {{ background: #FFFFFF; border: 1px solid #D6E2EE; border-radius: {p(6)}px; color: #253248; padding: {p(5)}px {p(7)}px; }}
            QComboBox QAbstractItemView, QAbstractItemView {{ background: #FFFFFF; border: 1px solid #D6E2EE; border-radius: {p(7)}px; color: #253248; selection-background-color: #E6F3FF; selection-color: #253248; outline: 0; padding: {p(3)}px; }}
            QAbstractItemView::item {{ min-height: {p(24)}px; padding: 0 {p(7)}px; border-radius: {p(5)}px; }}
            QAbstractItemView::item:selected {{ background: #E6F3FF; color: #253248; }}
            QMenuBar {{ background: transparent; color: #526276; border: none; }}
            QMenu {{ background: rgba(255,255,255,0.98); color: #253248; border: 1px solid #D6E2EE; border-radius: {p(7)}px; padding: {p(4)}px; }}
            QMenu::item {{ min-height: {p(24)}px; padding: 0 {p(16)}px 0 {p(8)}px; border-radius: {p(5)}px; }}
            QMenu::item:selected {{ background: #E6F3FF; color: #253248; }}
        """ + dark_overrides)
