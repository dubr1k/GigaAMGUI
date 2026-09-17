"""Regression cover for issue #54: clipped card titles, stretched settings groups
and a UI scale that collapses on Windows' 9 pt default font."""

import os
import sys
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QFont  # noqa: E402
from PyQt6.QtWidgets import QApplication, QGroupBox, QStyle, QStyleOptionGroupBox  # noqa: E402

sys.modules.setdefault("gigaam", types.SimpleNamespace(load_model=lambda *args, **kwargs: object()))
sys.modules.setdefault("yt_dlp", types.SimpleNamespace(YoutubeDL=object))

from src.gui import style_mixin  # noqa: E402
from src.gui.app_qt import GigaTranscriberQtApp  # noqa: E402

STYLED_GROUPS = {"glass_panel", "dense_panel", "settings_group"}


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, monkeypatch, tmp_path):
    monkeypatch.setenv("GIGAAM_CONFIG_DIR", str(tmp_path / "config"))
    instance = GigaTranscriberQtApp()
    # The reporter's window: 968×885 on a 100 % Windows 10 display.
    instance.resize(968, 850)
    instance.show()
    qapp.processEvents()
    qapp.processEvents()
    yield instance
    instance.close()


def _title_rect(group: QGroupBox):
    option = QStyleOptionGroupBox()
    option.initFrom(group)
    option.text = group.title()
    option.subControls = QStyle.SubControl.SC_GroupBoxLabel | QStyle.SubControl.SC_GroupBoxFrame
    style = group.style()
    label = style.subControlRect(QStyle.ComplexControl.CC_GroupBox, option, QStyle.SubControl.SC_GroupBoxLabel, group)
    frame = style.subControlRect(QStyle.ComplexControl.CC_GroupBox, option, QStyle.SubControl.SC_GroupBoxFrame, group)
    return label, frame


def test_styled_group_titles_sit_above_the_frame(window):
    # The 2.0 stylesheet reserved an 8 px margin for a 14 px title, so the
    # frame background was painted over the lower half of every card title.
    groups = [g for g in window.findChildren(QGroupBox) if g.objectName() in STYLED_GROUPS and g.title()]
    assert groups, "expected styled QGroupBox cards on the processing page"
    overlapping = []
    for group in groups:
        label, frame = _title_rect(group)
        if label.top() < 0 or label.bottom() > frame.top():
            overlapping.append((group.title(), label.getRect(), frame.getRect()))
    assert not overlapping, f"titles overlap their QGroupBox frame: {overlapping}"


def test_dark_theme_recolours_group_titles_and_keeps_labels_transparent(window):
    # The light sheet hard-codes dark title colours; the dark overrides must
    # re-colour them with the same ID selectors (a bare QGroupBox::title rule
    # loses to the ID rules). The dark QWidget background rule also comes after
    # the base "QLabel { background: transparent }", so labels grew opaque bands.
    window._theme = "dark"
    window._apply_theme()
    colours = window._colors()
    sheet = window.styleSheet()
    dark_part = sheet.split("QPushButton#nav_button {", 1)[1]
    assert f"QGroupBox#settings_group::title {{ color: {colours['text_sub']}; }}" in dark_part
    assert "QLabel { background: transparent; }" in dark_part


def test_subtitle_width_spinner_stays_inside_the_settings_column(window):
    # The compact layout used to hide the setting instead of fitting it.
    spinner = window.spin_subtitle_max_width
    group = window.grp_formats
    assert spinner.isVisible() and window.lbl_subtitle_max_width.isVisible()
    bottom_right = spinner.mapTo(group, spinner.rect().bottomRight())
    assert bottom_right.x() <= group.width(), (bottom_right.x(), group.width())
    assert bottom_right.y() <= group.height(), (bottom_right.y(), group.height())


def test_processing_settings_groups_keep_their_natural_height(window):
    # Extra vertical space must go to a stretch, not be split between the cards.
    for group in (window.grp_audio_preprocessing, window.grp_formats):
        assert group.height() <= group.sizeHint().height() + window._px(6), group.title()


@pytest.mark.parametrize(
    ("platform", "font_pt", "expected_min"),
    [("win32", 9.0, 1.0), ("darwin", 13.0, 1.0), ("linux", 10.0, 1.0)],
)
def test_platform_default_font_yields_unit_scale(platform, font_pt, expected_min):
    scale = style_mixin.font_scale_for(font_pt, platform)
    assert expected_min <= scale <= 1.15


def test_effective_scale_on_windows_default_font_is_not_minimum(qapp, monkeypatch):
    monkeypatch.setattr(style_mixin.sys, "platform", "win32")
    font = QFont(qapp.font())
    font.setPointSizeF(9.0)
    qapp.setFont(font)
    try:
        scale = style_mixin.StyleMixin._effective_ui_scale(object())
    finally:
        qapp.setFont(QFont())
    assert scale >= 1.0
