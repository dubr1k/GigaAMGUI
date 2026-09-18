"""Страница LLM и диалог настроек берут провайдеров из реестра cli_tools:
комбо со статусами на странице, таблица инструментов в диалоге, omp в настройках."""

import os
import sys
import time
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

sys.modules.setdefault("gigaam", types.SimpleNamespace(load_model=lambda *args, **kwargs: object()))
sys.modules.setdefault("yt_dlp", types.SimpleNamespace(YoutubeDL=object))

from src.gui.app_qt import GigaTranscriberQtApp  # noqa: E402
from src.services import cli_tools  # noqa: E402


def _status(spec_name, status, path=None, version=None, detail=None):
    spec = cli_tools.provider_by_name(spec_name)
    return cli_tools.ToolStatus(spec.id, spec.name, status, path, version, detail, spec.install_hint)


FAKE_SCAN = [
    _status("Claude Code", "found", "/opt/homebrew/bin/claude", "2.1.275"),
    _status("Codex", "missing"),
    _status("OpenCode", "broken", "/x/opencode", detail="exit 1: node not found"),
    _status("Pi", "missing"),
    _status("oh-my-pi", "found", "/opt/homebrew/bin/omp", "18.2.5"),
]


@pytest.fixture
def window(monkeypatch, tmp_path):
    monkeypatch.setenv("GIGAAM_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(cli_tools, "scan", lambda overrides=None, *, fresh=False: list(FAKE_SCAN))
    app = QApplication.instance() or QApplication([])
    instance = GigaTranscriberQtApp()
    instance.show()
    deadline = time.time() + 10
    while getattr(instance, "_llm_scan_running", False) and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    yield instance
    instance.close()


def test_provider_combo_lives_on_llm_page_and_lists_registry(window):
    names = [window.combo_llm_provider.itemData(i) for i in range(window.combo_llm_provider.count())]
    assert names == cli_tools.canonical_provider_names()
    assert window.combo_llm_provider.itemText(window._llm_other_index) == "Другое"
    window.tabs.setCurrentIndex(2)  # LLM
    QApplication.instance().processEvents()
    strip = window.combo_llm_provider.parentWidget()
    assert strip.objectName() == "llm_provider_strip" and strip.isVisible()
    assert window.entry_llm_model.isVisible()


def test_scan_results_render_in_table_combo_and_page_badge(window):
    table = window.tbl_llm_tools
    assert table.rowCount() == len(cli_tools.cli_specs())
    row = window._llm_tool_rows["oh-my-pi"]
    assert table.item(row, 0).text() == "●"
    assert table.item(row, 2).text() == "18.2.5"
    assert table.cellWidget(row, 3).placeholderText() == "/opt/homebrew/bin/omp"

    row = window._llm_tool_rows["Codex"]
    assert table.item(row, 0).text() == "○"
    assert table.item(row, 2).text() == "не найден"
    assert "npm" in table.item(row, 0).toolTip()

    row = window._llm_tool_rows["OpenCode"]
    assert table.item(row, 0).text() == "⚠"
    assert "node not found" in table.item(row, 2).toolTip()

    window.combo_llm_provider.setCurrentText("oh-my-pi")
    assert "18.2.5" in window.lbl_llm_provider_status.text()
    window.combo_llm_provider.setCurrentText("Codex")
    assert "не найден" in window.lbl_llm_provider_status.text()
    window.combo_llm_provider.setCurrentText("API")
    assert window.lbl_llm_provider_status.text() == "HTTP API"


def test_provider_fields_toggle_with_registry(window):
    window.combo_llm_provider.setCurrentText("oh-my-pi")
    assert window.llm_omp_settings_widget.isVisibleTo(window._llm_settings_dialog)
    assert not window.llm_pi_settings_widget.isVisibleTo(window._llm_settings_dialog)
    assert window.cb_llm_allow_tools.isVisibleTo(window._llm_settings_dialog)
    assert window.grp_llm_tools.isVisibleTo(window._llm_settings_dialog)
    window.combo_llm_provider.setCurrentText("API")
    assert not window.grp_llm_tools.isVisibleTo(window._llm_settings_dialog)
    assert not window.cb_llm_allow_tools.isVisibleTo(window._llm_settings_dialog)


def test_collect_settings_includes_omp_and_allow_tools(window, monkeypatch):
    monkeypatch.setattr(cli_tools, "locate_tool", lambda spec, override=None: "/opt/homebrew/bin/omp")
    window.combo_llm_provider.setCurrentText("oh-my-pi")
    window.entry_llm_model.setText("opus")
    window.entry_llm_omp_provider.setText("anthropic")
    window.entry_llm_omp_args.setText("--thinking low")
    window.cb_llm_allow_tools.setChecked(True)

    settings = window._collect_llm_settings()

    assert settings["provider"] == "oh-my-pi"
    assert settings["model"] == "opus"
    assert settings["omp_path"] == "omp"  # пусто в поле → голое имя, резолвит сервис
    assert settings["omp_provider"] == "anthropic"
    assert settings["omp_args"] == "--thinking low"
    assert settings["llm_allow_tools"] is True
    for key in ("claude_path", "codex_path", "opencode_path", "pi_path"):
        assert key in settings


def test_collect_settings_missing_tool_error_has_install_hint(window, monkeypatch):
    monkeypatch.setattr(cli_tools, "locate_tool", lambda spec, override=None: None)
    window.combo_llm_provider.setCurrentText("Pi")
    with pytest.raises(ValueError) as exc:
        window._collect_llm_settings()
    assert "Не найден Pi" in str(exc.value)
    assert "npm" in str(exc.value)


def test_paths_are_not_frozen_in_settings(window):
    """Голое имя бинаря из старых настроек считается «автопоиск», а не путём."""
    window.user_settings.set_value("llm_claude_path", "claude")
    window.user_settings.set_value("llm_omp_path", "/custom/omp")
    window._restore_ui_settings()
    assert window.entry_llm_claude_path.text() == ""
    assert window.entry_llm_omp_path.text() == "/custom/omp"


def test_language_switch_keeps_other_provider_selected(window):
    window.combo_llm_provider.setCurrentText("Другое")
    window._btn_lang.click()
    assert window.combo_llm_provider.currentText() == "Other"
    assert window.tbl_llm_tools.horizontalHeaderItem(1).text() == "Tool"
    window._btn_lang.click()
    assert window.combo_llm_provider.currentText() == "Другое"
