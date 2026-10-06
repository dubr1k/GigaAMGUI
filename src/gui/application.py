"""QApplication приложения: ловит открытие файлов из Finder и Dock (macOS).

Отдельный модуль без torch-цепочки: app.py создаёт приложение до выбора
runtime, и это должен быть именно GigaApplication. С обычным QApplication
проверки isinstance в run_qt_app не срабатывали, и файлы, брошенные на
иконку в Dock или открытые из Finder, молча терялись.
"""
from __future__ import annotations

import os

from PyQt6.QtCore import QEvent, pyqtSignal
from PyQt6.QtWidgets import QApplication


class GigaApplication(QApplication):
    """QApplication with macOS Finder/Dock open-file event support."""

    file_open_requested = pyqtSignal(list)

    def __init__(self, argv):
        super().__init__(argv)
        # Finder шлёт FileOpen и до появления окна (выбор устройства, загрузка
        # runtime): такие пути копятся здесь, окно забирает их при старте.
        self._pending_open_paths = []

    def event(self, event):
        if event.type() == QEvent.Type.FileOpen:
            path = ""
            try:
                url = event.url()
                if url.isLocalFile():
                    path = url.toLocalFile()
            except Exception:
                path = ""
            if not path:
                try:
                    path = event.file()
                except Exception:
                    path = ""
            if path:
                # QUrl.toLocalFile() даёт «/» и на Windows; остальные источники
                # путей (argv, диалоги) — платформенные разделители.
                path = os.path.normpath(path)
                self._pending_open_paths.append(path)
                self.file_open_requested.emit([path])
            return True
        return super().event(event)

    def take_pending_open_paths(self):
        paths = self._pending_open_paths[:]
        self._pending_open_paths.clear()
        return paths
