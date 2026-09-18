"""Render every page of the PyQt main window to PNG files.

The GUI builds all metrics from the system font and the Qt style, so a
layout that is fine on macOS can break on Windows (9 pt Segoe UI, native
style) or Linux (issue #54). This script needs only PyQt6 and the light GUI
imports (no torch, no models): the ASR/download modules are stubbed.

    python scripts/render_gui_screenshots.py --out shots [--style Fusion]
        [--font-pt 9] [--themes dark,light] [--size 968x850]

Output: <out>/<theme>/<index>-<page>.png plus <out>/environment.txt with the
platform, Qt version, style and font that were in effect.
"""

from __future__ import annotations

import argparse
import os
import platform
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root, so `src` imports
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Never touch the user's real settings while rendering.
os.environ.setdefault("GIGAAM_CONFIG_DIR", str(Path(".gui-screenshots-config").resolve()))

sys.modules.setdefault("gigaam", types.SimpleNamespace(load_model=lambda *args, **kwargs: object()))  # type: ignore[arg-type]
sys.modules.setdefault("yt_dlp", types.SimpleNamespace(YoutubeDL=object))  # type: ignore[arg-type]

PAGE_NAMES = ("processing", "live", "llm", "api", "log", "settings")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render the PyQt GUI pages to PNG files.")
    parser.add_argument("--out", type=Path, default=Path("gui-screenshots"))
    parser.add_argument("--style", default=None, help="Qt style name (default: platform default)")
    parser.add_argument("--font-pt", type=float, default=None, help="override the application font size")
    parser.add_argument("--themes", default="dark,light")
    parser.add_argument("--size", default="968x850", help="window size WxH (the issue #54 reporter's window)")
    args = parser.parse_args(argv)

    from PyQt6.QtCore import QT_VERSION_STR
    from PyQt6.QtGui import QFont
    from PyQt6.QtWidgets import QApplication

    app = QApplication([])
    if args.style:
        app.setStyle(args.style)
    if args.font_pt:
        font = QFont(app.font())
        font.setPointSizeF(args.font_pt)
        app.setFont(font)

    from src.gui.app_qt import GigaTranscriberQtApp

    width, height = (int(part) for part in args.size.lower().split("x", 1))
    args.out.mkdir(parents=True, exist_ok=True)
    window = GigaTranscriberQtApp()
    style = app.style()
    font = app.font()
    from PyQt6.QtGui import QFontInfo

    # The stylesheet asks for "-apple-system, SF Pro Text, Segoe UI, Arial";
    # QFontInfo reports which family the platform actually resolved.
    resolved = {
        name: QFontInfo(widget.font()).family()
        for name, widget in (
            ("nav_button", window._nav_buttons[0]),
            ("section_title", window.findChild(type(window._sidebar_footer), "section_title")),
            ("field_label", window.lbl_audio_preprocessing_mode),
            ("checkbox", window.format_checkboxes["txt"]),
            ("combo", window.combo_audio_preprocessing),
        )
        if widget is not None
    }
    environment = "\n".join([
        f"platform={platform.platform()}",
        f"qt={QT_VERSION_STR}",
        f"qpa={os.environ.get('QT_QPA_PLATFORM', '')}",
        f"style={style.objectName() if style else '?'}",
        f"font={font.family()} {font.pointSizeF()}pt",
        *(f"resolved_font[{name}]={family}" for name, family in resolved.items()),
        f"ui_scale={window._ui_scale}",
        f"size={width}x{height}",
    ])
    (args.out / "environment.txt").write_text(environment + "\n", encoding="utf-8")
    print(environment)

    window.resize(width, height)
    window.show()
    for theme in [t.strip() for t in args.themes.split(",") if t.strip()]:
        window._theme = theme
        window._apply_theme()
        target = args.out / theme
        target.mkdir(exist_ok=True)
        for index, name in enumerate(PAGE_NAMES):
            if index >= window.tabs.count():
                break
            window.tabs.setCurrentIndex(index)
            app.processEvents()
            app.processEvents()
            path = target / f"{index}-{name}.png"
            window.grab().save(str(path))
            print("saved", path)
    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
