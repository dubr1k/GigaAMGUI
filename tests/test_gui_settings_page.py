"""Вкладка «Настройки» — второе представление тех же настроек, что и в меню,
на вкладке обработки и в диалоге LLM. Она не должна сохранять значение,
которое окно отвергло, и не должна отставать от изменений в других местах."""

import os
import sys
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

sys.modules.setdefault("gigaam", types.SimpleNamespace(load_model=lambda *args, **kwargs: object()))
sys.modules.setdefault("yt_dlp", types.SimpleNamespace(YoutubeDL=object))

from src.gui.app_qt import GigaTranscriberQtApp  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_gui_config(monkeypatch, tmp_path):
    monkeypatch.setenv("GIGAAM_CONFIG_DIR", str(tmp_path / "config"))


@pytest.fixture
def window():
    app = QApplication.instance() or QApplication([])
    instance = GigaTranscriberQtApp()
    instance._lang = "ru"
    instance._apply_language()
    yield instance
    instance.close()
    app.processEvents()


def test_language_switch_keeps_running_llm_and_status_bar_messages(window):
    window.lbl_llm_status.setText("Ошибка LLM: Неверный API key")
    window._set_status("Обработка 3 файлов…")

    window._toggle_language()

    assert window.lbl_llm_status.text() == "Ошибка LLM: Неверный API key"
    assert window.statusBar().currentMessage() != "Ready to work"


def test_language_switch_translates_idle_llm_and_status_bar_messages(window):
    assert window.lbl_llm_status.text() == "Готово к LLM-обработке"

    window._toggle_language()

    assert window.lbl_llm_status.text() == "Ready for LLM processing"
    assert window.statusBar().currentMessage() == "Ready to work"


def test_cancelled_hf_token_prompt_does_not_persist_diarization(window, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr(window, "_show_hf_token_dialog", lambda: False)
    window.combo_diarization_backend.setCurrentIndex(window.combo_diarization_backend.findData("pyannote"))

    window.settings_diarization.setChecked(True)
    window._set_settings_diarization(True)

    assert window.cb_diarization.isChecked() is False
    assert window.user_settings.get_value("enable_diarization") is False
    assert window.settings_diarization.isChecked() is False
    assert window.settings_diarization.text() == "Выключена"
