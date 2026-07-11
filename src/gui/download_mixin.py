"""Загрузка медиа по ссылке (yt-dlp) для GigaTranscriberQtApp.

Mixin: методы работают со `self` главного окна. Поведение сохранено 1:1.
"""
from __future__ import annotations

import os
import threading

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QFileDialog, QMessageBox


class DownloadMixin:
    def _start_download(self, start_after_download: bool = False):
        url = self.input_path.text().strip()
        if not url:
            QMessageBox.warning(self, self._t("Внимание", "Attention"), self._t("Введите ссылку для загрузки.", "Enter a URL to download."))
            return
        if not (url.startswith("http://") or url.startswith("https://")):
            QMessageBox.warning(self, self._t("Внимание", "Attention"), self._t("Ссылка должна начинаться с http:// или https://.", "The URL must start with http:// or https://."))
            return
        if self.is_processing:
            QMessageBox.warning(self, self._t("Внимание", "Attention"), self._t("Дождитесь завершения обработки файлов.", "Wait for file processing to finish."))
            return
        if self.is_downloading:
            QMessageBox.information(self, self._t("Информация", "Information"), self._t("Загрузка уже выполняется.", "A download is already in progress."))
            return
        download_dir = self.input_dir
        if not download_dir:
            initial_dir = self.user_settings.get_last_files_dir() or os.path.expanduser("~")
            download_dir = QFileDialog.getExistingDirectory(self, "Выберите папку для загрузки медиа", initial_dir)
            if not download_dir:
                return
            self.input_dir = download_dir
            self.user_settings.set_last_files_dir(download_dir)
            self._update_input_dir_label(download_dir)
        self.is_downloading = True
        self.start_processing_after_download = start_after_download
        self.btn_upload.setEnabled(False)
        self.btn_start.setEnabled(False)
        self.progress_upload.setValue(0)
        self.progress_upload.setVisible(True)
        self.lbl_status.setText("Загрузка медиа по ссылке...")
        self._set_status("Загрузка по ссылке…")
        self.log(f"Загрузка медиа по ссылке в папку: {download_dir}")
        threading.Thread(target=self._download_media, args=(url, download_dir), daemon=True).start()

    def _download_media(self, url: str, download_dir: str):
        try:
            result = self.media_downloader.download(
                url, download_dir,
                progress_callback=self.signals.download_progress.emit,
                allow_playlist=False,
            )
            files = [p for p in result.files if os.path.isfile(p) and os.path.getsize(p) > 0]
            if not files:
                raise RuntimeError("yt-dlp не вернул скачанный медиафайл")
            self.signals.download_finished.emit(files)
        except Exception as e:
            self.signals.download_failed.emit(str(e))

    def _update_download_progress(self, value: int):
        self.progress_upload.setValue(value)

    def _on_download_finished(self, files: list):
        self.is_downloading = False
        self.btn_upload.setEnabled(True)
        self.btn_start.setEnabled(True)
        self.progress_upload.setValue(0)
        self.progress_upload.setVisible(False)
        if files:
            self._apply_dropped_or_selected_files(files, append=True, remember_dir=False)
            self.input_path.clear()
            self.lbl_status.setText(self._t("Медиа загружено и добавлено в очередь", "Media downloaded and added to the queue"))
            self.log(f"Загрузка завершена: {len(files)} файлов")
        else:
            QMessageBox.warning(self, self._t("Загрузка", "Download"), self._t("Не удалось получить медиафайлы по ссылке.", "Failed to get media files from the URL."))
            self.log("Загрузка завершилась без файлов")
        if self.start_processing_after_download:
            self.start_processing_after_download = False
            QTimer.singleShot(0, self._start_processing_thread)

    def _on_download_failed(self, message: str):
        self.is_downloading = False
        self.start_processing_after_download = False
        self.btn_upload.setEnabled(True)
        self.btn_start.setEnabled(True)
        self.progress_upload.setValue(0)
        self.progress_upload.setVisible(False)
        self.lbl_status.setText(self._t("Ошибка загрузки", "Download error"))
        self.log(f"Ошибка загрузки: {message}")
        QMessageBox.warning(self, self._t("Ошибка загрузки", "Download error"), message)
