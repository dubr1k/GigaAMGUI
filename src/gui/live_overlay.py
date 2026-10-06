"""Always-on-top transcript overlay for live transcription."""

from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QTextCursor
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextEdit, QVBoxLayout, QWidget

from ..live.types import TranscriptEvent
from .live_transcript import LiveTranscriptPresenter


class _DragHandle(QFrame):
    def __init__(self, overlay: LiveOverlay) -> None:
        super().__init__(overlay)
        self._overlay = overlay
        self._drag_origin: QPoint | None = None

    def mousePressEvent(self, event) -> None:
        if event.button() is Qt.MouseButton.LeftButton:
            self._drag_origin = event.globalPosition().toPoint() - self._overlay.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._overlay.move(event.globalPosition().toPoint() - self._drag_origin)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_origin = None
        super().mouseReleaseEvent(event)


class LiveOverlay(QWidget):
    """Small movable window that keeps final and partial text visible."""

    question_submitted = pyqtSignal(str)
    cancel_requested = pyqtSignal()
    visibility_changed = pyqtSignal(bool)

    def __init__(self, parent: QWidget | None = None, language: str = "en") -> None:
        flags = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        super().__init__(parent, flags)
        # Окно приложения передаёт свой язык; сам по себе оверлей англоязычный.
        self._language = language
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumWidth(360)
        self.resize(480, 240)
        self._transcript_presenter = LiveTranscriptPresenter()
        self._active_question: str | None = None
        self._generation_ellipsis = 0
        self._generation_timer = QTimer(self)
        self._generation_timer.setInterval(350)
        self._generation_timer.timeout.connect(self._advance_generating_ellipsis)

        glass = QFrame()
        glass.setObjectName("liveGlass")
        glass.setStyleSheet(
            "QFrame#liveGlass { background: rgba(28, 32, 40, 218); "
            "border: 1px solid rgba(255, 255, 255, 75); border-radius: 16px; } "
            "QTextEdit { background: transparent; border: none; color: #f4f6fa; } "
            "QLabel { color: #d9e2f2; background: transparent; } "
            "QPushButton { background: rgba(255, 255, 255, 26); color: #f4f6fa; "
            "border: none; border-radius: 10px; min-width: 24px; min-height: 24px; } "
            "QPushButton:hover { background: rgba(255, 255, 255, 52); }"
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(glass)
        layout = QVBoxLayout(glass)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(8)

        self.header = _DragHandle(self)
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("LIVE")
        title.setStyleSheet("font-weight: 700; letter-spacing: 1px; color: #9fc5ff;")
        header_layout.addWidget(title)
        header_layout.addStretch()
        self.collapse_button = QPushButton("−")
        self.collapse_button.clicked.connect(self.toggle_collapsed)
        header_layout.addWidget(self.collapse_button)
        self.close_button = QPushButton("✕")
        self.close_button.setStyleSheet("font-weight: 700;")
        self.close_button.clicked.connect(self.hide)
        header_layout.addWidget(self.close_button)
        layout.addWidget(self.header)

        self.content = QWidget()
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(7)
        self.final_text = QTextEdit()
        self.final_text.setReadOnly(True)
        self.final_text.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.final_text.setMinimumHeight(112)
        content_layout.addWidget(self.final_text)
        self.partial_label = QLabel()
        self.partial_label.setWordWrap(True)
        self.partial_label.setStyleSheet("color: #a9c9ff; font-style: italic;")
        content_layout.addWidget(self.partial_label)

        answer_header = QHBoxLayout()
        answer_header.setContentsMargins(0, 0, 0, 0)
        self.answer_title = QLabel()
        answer_header.addWidget(self.answer_title)
        answer_header.addStretch()
        self.answer_toggle_button = QPushButton()
        self.answer_toggle_button.clicked.connect(self.toggle_answer_visibility)
        answer_header.addWidget(self.answer_toggle_button)
        content_layout.addLayout(answer_header)
        self.answer_card = QFrame()
        answer_layout = QVBoxLayout(self.answer_card)
        answer_layout.setContentsMargins(0, 0, 0, 0)
        self.answer_text = QTextEdit()
        self.answer_text.setReadOnly(True)
        self.answer_text.setMinimumHeight(104)
        answer_layout.addWidget(self.answer_text)
        self.answer_card.hide()
        content_layout.addWidget(self.answer_card)

        question_layout = QHBoxLayout()
        question_layout.setContentsMargins(0, 0, 0, 0)
        self.question_input = QLineEdit()
        self.question_input.returnPressed.connect(self._submit_question)
        question_layout.addWidget(self.question_input)
        self.send_button = QPushButton()
        self.send_button.clicked.connect(self._submit_question)
        question_layout.addWidget(self.send_button)
        self.cancel_button = QPushButton()
        self.cancel_button.clicked.connect(self._cancel_generation)
        self.cancel_button.hide()
        question_layout.addWidget(self.cancel_button)
        content_layout.addLayout(question_layout)
        layout.addWidget(self.content)
        self._apply_language()

    def _t(self, ru: str, en: str) -> str:
        return ru if self._language == "ru" else en

    def set_language(self, language: str) -> None:
        if language == self._language:
            return
        old_you, old_assistant, old_generating = self._you(), self._assistant(), self._generating_word()
        self._language = language
        self._apply_language()
        # Уже показанный разговор: подписи реплик — на новом языке.
        self.answer_text.setPlainText(
            self.answer_text.toPlainText()
            .replace(old_you, self._you())
            .replace(old_assistant, self._assistant())
            .replace(old_generating, self._generating_word())
        )

    def _apply_language(self) -> None:
        self.setWindowTitle(self._t("Live-расшифровка", "Live transcription"))
        self.collapse_button.setToolTip(self._t("Свернуть", "Collapse"))
        self.close_button.setToolTip(self._t("Скрыть оверлей (Esc)", "Hide overlay (Esc)"))
        self.answer_title.setText(self._t("АССИСТЕНТ", "ASSISTANT"))
        self.answer_toggle_button.setText(self._answer_toggle_text(self.answer_card.isHidden() and bool(self.answer_text.toPlainText())))
        self.question_input.setPlaceholderText(self._t("Спросите об этой сессии", "Ask about this session"))
        self.send_button.setText(self._t("Отправить", "Send"))
        self.cancel_button.setText(self._t("Отмена", "Cancel"))

    def _answer_toggle_text(self, hidden: bool) -> str:
        return self._t("Показать ответ", "Show answer") if hidden else self._t("Скрыть ответ", "Hide answer")

    def _you(self) -> str:
        return self._t("Вы:", "You:")

    def _assistant(self) -> str:
        return self._t("Ассистент:", "Assistant:")

    def _generating_word(self) -> str:
        return self._t("Генерация", "Generating")

    def update_transcript(self, event: TranscriptEvent) -> None:
        presenter = self._transcript_presenter
        delta = presenter.add_event(event)
        if not delta:
            return
        scrollbar = self.final_text.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 2
        position = scrollbar.value()
        piece = presenter.rendered_delta(event, delta)
        if presenter.rewrote:
            # A corrected final replaces the words already shown for its event.
            self.final_text.setPlainText("\n".join(presenter.rendered_pieces()))
        else:
            cursor = self.final_text.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            if cursor.position() > 0:
                cursor.insertText("\n")
            cursor.insertText(piece)
        scrollbar.setValue(scrollbar.maximum() if at_bottom else position)
        self.partial_label.clear()

    def clear_transcript(self) -> None:
        self._transcript_presenter.clear()
        self.final_text.clear()
        self.partial_label.clear()

    def toggle_collapsed(self) -> None:
        collapsed = self.content.isVisible()
        self.content.setVisible(not collapsed)
        self.collapse_button.setText("+" if collapsed else "−")
        self.adjustSize()

    def set_answer(self, text: str) -> None:
        self.answer_text.setPlainText(
            text if self._active_question is None else f"{self._you()} {self._active_question}\n{self._assistant()} {text}"
        )
        self.answer_card.show()
        self.answer_toggle_button.setText(self._answer_toggle_text(False))
        self.adjustSize()

    def append_answer(self, text: str) -> None:
        scrollbar = self.answer_text.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 2
        position = scrollbar.value()
        placeholder = self._generating_text()
        current = self.answer_text.toPlainText()
        if current.endswith(placeholder):
            cursor = self.answer_text.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.KeepAnchor, len(placeholder))
            cursor.insertText(text)
        else:
            cursor = self.answer_text.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertText(text)
        scrollbar.setValue(scrollbar.maximum() if at_bottom else position)

    def finish_generation(self) -> None:
        self._generation_timer.stop()
        self._active_question = None
        self.cancel_button.hide()
        self.send_button.setEnabled(True)

    def _begin_question(self, question: str) -> None:
        self._active_question = question
        self._generation_ellipsis = 0
        self.answer_text.setPlainText(f"{self._you()} {question}\n{self._assistant()} {self._generating_text()}")
        self._generation_timer.start()
        self.answer_card.show()
        self.answer_toggle_button.setText(self._answer_toggle_text(False))
        self.send_button.setEnabled(False)
        self.cancel_button.show()
        self.adjustSize()

    def _cancel_generation(self) -> None:
        if self._active_question is None:
            return
        self.answer_text.setPlainText(f"{self._you()} {self._active_question}")
        self._generation_timer.stop()
        self._active_question = None
        self.cancel_button.hide()
        self.send_button.setEnabled(True)
        self.cancel_requested.emit()

    def set_conversation(self, turns) -> None:
        scrollbar = self.answer_text.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 2
        position = scrollbar.value()
        self.answer_text.setPlainText("\n\n".join(
            f"{self._you()} {turn.question}\n{self._assistant()} "
            f"{turn.answer or self._generating_text()}"
            if turn.status == "generating"
            else f"{self._you()} {turn.question}" + (f"\n{self._assistant()} {turn.answer}" if turn.answer else "")
            for turn in turns
        ))
        self.answer_card.setVisible(bool(turns))
        scrollbar.setValue(scrollbar.maximum() if at_bottom else position)

    def _advance_generating_ellipsis(self) -> None:
        if self._active_question is None:
            return
        self._generation_ellipsis = (self._generation_ellipsis + 1) % 4
        self.answer_text.setPlainText(self.answer_text.toPlainText().replace(
            self._generating_text((self._generation_ellipsis - 1) % 4),
            self._generating_text(),
        ))

    def _generating_text(self, ellipsis: int | None = None) -> str:
        return f"{self._generating_word()}{'.' * (self._generation_ellipsis if ellipsis is None else ellipsis)}"

    def toggle_answer_visibility(self) -> None:
        visible = self.answer_card.isVisible()
        self.answer_card.setVisible(not visible)
        self.answer_toggle_button.setText(self._answer_toggle_text(visible))
        self.adjustSize()

    def _submit_question(self) -> None:
        question = self.question_input.text().strip()
        if not question:
            return
        self.question_input.clear()
        self._begin_question(question)
        self.question_submitted.emit(question)

    def keyPressEvent(self, event) -> None:
        if event is not None and event.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.visibility_changed.emit(True)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.visibility_changed.emit(False)

    def closeEvent(self, event: QCloseEvent) -> None:
        event.ignore()
        self.hide()
