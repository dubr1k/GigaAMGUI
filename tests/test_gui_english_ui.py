"""После переключения на английский в интерфейсе не остаётся русского текста.

Сканируются все подписи, заголовки, подсказки, плейсхолдеры, пункты списков
и меню главного окна, диалога «Настройки LLM» и оверлея Live. Каждую новую
надпись нужно добавить в _retranslate_* своей поверхности, иначе тест
назовёт её.
"""

import os
import re
import sys
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import (  # noqa: E402
    QAbstractButton,
    QApplication,
    QComboBox,
    QGroupBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QSpinBox,
    QTableWidget,
    QTabWidget,
    QTextEdit,
    QWidget,
)

sys.modules.setdefault("gigaam", types.SimpleNamespace(load_model=lambda *args, **kwargs: object()))
sys.modules.setdefault("yt_dlp", types.SimpleNamespace(YoutubeDL=object))

from src.gui.app_qt import GigaTranscriberQtApp  # noqa: E402

_CYRILLIC = re.compile(r"[А-Яа-яЁё]")
# Название языка в списке языков пишется на самом языке.
_ALLOWED = {"Русский"}


@pytest.fixture
def window(monkeypatch, tmp_path):
    monkeypatch.setenv("GIGAAM_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    app = QApplication.instance() or QApplication([])
    instance = GigaTranscriberQtApp()
    instance._show_live_overlay()
    yield instance
    instance.live_overlay.hide()
    instance.close()
    app.processEvents()


def _texts(widget: QWidget):
    yield "toolTip", widget.toolTip()
    yield "windowTitle", widget.windowTitle() if widget.isWindow() else ""
    if isinstance(widget, (QLabel, QAbstractButton)):
        yield "text", widget.text()
    if isinstance(widget, QGroupBox):
        yield "title", widget.title()
    if isinstance(widget, QLineEdit):
        yield "placeholder", widget.placeholderText()
    if isinstance(widget, (QTextEdit, QPlainTextEdit)):
        yield "placeholder", widget.placeholderText()
    if isinstance(widget, QSpinBox):
        yield "specialValue", widget.specialValueText()
    if isinstance(widget, QComboBox):
        yield "placeholder", widget.placeholderText()
        for index in range(widget.count()):
            yield f"item[{index}]", widget.itemText(index)
    if isinstance(widget, QTabWidget):
        for index in range(widget.count()):
            yield f"tab[{index}]", widget.tabText(index)
    if isinstance(widget, QListWidget) and widget.objectName() not in {"files_list", "llm_source_files"}:
        for index in range(widget.count()):
            yield f"item[{index}]", widget.item(index).text()
    if isinstance(widget, QTableWidget):
        for column in range(widget.columnCount()):
            header = widget.horizontalHeaderItem(column)
            yield f"header[{column}]", header.text() if header else ""


def _russian_leftovers(roots):
    leftovers = []
    for root in roots:
        for widget in [root, *root.findChildren(QWidget)]:
            for kind, text in _texts(widget):
                if text and text not in _ALLOWED and _CYRILLIC.search(text):
                    leftovers.append(f"{type(widget).__name__}#{widget.objectName()} {kind}: {text[:70]}")
            for action in widget.actions():
                for kind, text in (("action", action.text()), ("statusTip", action.statusTip())):
                    if text and _CYRILLIC.search(text):
                        leftovers.append(f"QAction {kind}: {text[:70]}")
    return sorted(set(leftovers))


def test_switching_to_english_leaves_no_russian_text(window):
    window._lang = "ru"
    window._apply_language()
    window._lang = "en"
    window._apply_language()

    leftovers = _russian_leftovers([window, window._llm_settings_dialog, window.live_overlay])

    assert leftovers == [], "\n".join(leftovers)


def test_live_overlay_follows_the_window_language(window):
    """Оверлей был только английским, даже в русском интерфейсе."""
    window._lang = "ru"
    window._apply_language()
    assert window.live_overlay.send_button.text() == "Отправить"
    window.live_overlay._begin_question("Что решили?")
    assert window.live_overlay.answer_text.toPlainText().startswith("Вы: Что решили?")

    window._lang = "en"
    window._apply_language()

    assert window.live_overlay.send_button.text() == "Send"
    assert window.live_overlay.answer_text.toPlainText().startswith("You: Что решили?")
    window.live_overlay.finish_generation()


def test_dynamic_live_status_is_retranslated_only_when_it_is_known(window):
    window._lang = "ru"
    window._apply_language()
    window.lbl_live_status.setText("Идёт запись")
    window._lang = "en"
    window._apply_language()
    assert window.lbl_live_status.text() == "Recording"

    window.lbl_live_status.setText("Microphone permission denied")
    window._lang = "ru"
    window._apply_language()
    assert window.lbl_live_status.text() == "Microphone permission denied"
