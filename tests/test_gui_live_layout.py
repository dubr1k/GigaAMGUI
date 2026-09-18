"""The Live page's side panes were capped at 175/180 pt and every control in
them had an Ignored horizontal size policy, so device combos collapsed to a
few characters, checkbox captions were cut to "Записыв" and the subtitle
spinners showed no digits (seen on the Windows runner render, issue #54)."""

import os
import sys
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

sys.modules.setdefault("gigaam", types.SimpleNamespace(load_model=lambda *args, **kwargs: object()))
sys.modules.setdefault("yt_dlp", types.SimpleNamespace(YoutubeDL=object))

from src.gui.app_qt import GigaTranscriberQtApp  # noqa: E402


@pytest.fixture
def window(monkeypatch, tmp_path):
    monkeypatch.setenv("GIGAAM_CONFIG_DIR", str(tmp_path / "config"))
    app = QApplication.instance() or QApplication([])
    instance = GigaTranscriberQtApp()
    instance.resize(968, 850)
    instance.show()
    instance.tabs.setCurrentIndex(1)
    app.processEvents()
    app.processEvents()
    yield instance
    instance.close()


def test_live_device_combos_are_usable(window):
    for combo in (window.combo_live_source, window.combo_live_mic_device, window.combo_live_system_device):
        assert combo.width() >= window._px(120), (combo.objectName(), combo.width())


def test_live_captions_are_not_truncated(window):
    widgets = [
        window.cb_live_mic_audio,
        window.cb_live_system_audio,
        window.cb_live_export_txt_timecodes,
        window.cb_live_export_md,
        window.cb_live_subtitle_sentence_split,
        window.spin_live_subtitle_max_lines,
        window.spin_live_subtitle_max_width,
        window.spin_live_gain,
    ]
    short = [(w.text() if hasattr(w, "text") else w.objectName(), w.width(), w.sizeHint().width())
             for w in widgets if w.width() < w.sizeHint().width()]
    assert not short, f"widgets narrower than their size hint: {short}"
