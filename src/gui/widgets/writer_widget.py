"""Writer widget for Korean -> English translation with Reddit tone polishing."""

import logging
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTextEdit, QCheckBox,
    QApplication,
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer

from src.core.i18n_manager import I18nManager
from src.gui.workers import GenerationWorker
from src.services.writer_service import WriterService

logger = logging.getLogger("reddiscribe")


class WriterWidget(QWidget):
    """Writer tab - 2-stage translation pipeline UI."""

    activity_started = pyqtSignal(str)   # task name for global status
    activity_finished = pyqtSignal()     # task completed

    def __init__(self, writer_service: WriterService, parent=None):
        super().__init__(parent)
        self._writer = writer_service
        self._i18n = I18nManager()

        self._draft_worker: Optional[GenerationWorker] = None
        self._polish_worker: Optional[GenerationWorker] = None
        self._draft_text: str = ""  # collected draft for Stage 2 input

        self._init_ui()

        # Loading animation
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(500)
        self._anim_timer.timeout.connect(self._animate_loading)
        self._anim_dot_count = 0
        self._anim_target: Optional[QTextEdit] = None

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # Header
        self._header = QLabel(self._i18n.get("writer.header"))
        self._header.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(self._header)

        # Korean input
        self._input = QTextEdit()
        self._input.setPlaceholderText(self._i18n.get("writer.placeholder"))
        self._input.setMaximumHeight(150)
        layout.addWidget(self._input)

        # Button row
        btn_layout = QHBoxLayout()

        self._translate_btn = QPushButton(self._i18n.get("writer.translate_btn"))
        self._translate_btn.clicked.connect(self._on_translate)
        btn_layout.addWidget(self._translate_btn)

        self._draft_only_cb = QCheckBox(self._i18n.get("writer.draft_only"))
        btn_layout.addWidget(self._draft_only_cb)

        btn_layout.addStretch()

        self._stop_btn = QPushButton(self._i18n.get("writer.stop_btn"))
        self._stop_btn.clicked.connect(self._on_stop)
        self._stop_btn.setEnabled(False)
        btn_layout.addWidget(self._stop_btn)

        layout.addLayout(btn_layout)

        # Stage 1: Draft
        self._draft_label = QLabel(self._i18n.get("writer.draft_label"))
        self._draft_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._draft_label)

        self._draft_output = QTextEdit()
        self._draft_output.setReadOnly(True)
        layout.addWidget(self._draft_output)

        # Stage 2: Final
        self._final_label = QLabel(self._i18n.get("writer.final_label"))
        self._final_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._final_label)

        self._final_output = QTextEdit()
        self._final_output.setReadOnly(True)
        layout.addWidget(self._final_output)

        # Copy button
        self._copy_btn = QPushButton(self._i18n.get("writer.copy_btn"))
        self._copy_btn.clicked.connect(self._on_copy)
        self._copy_btn.setEnabled(False)
        layout.addWidget(self._copy_btn)

    def _on_translate(self):
        """Start translation pipeline."""
        korean_text = self._input.toPlainText().strip()
        if not korean_text:
            return

        # Reset outputs
        self._draft_output.clear()
        self._final_output.clear()
        self._draft_text = ""
        self._copy_btn.setEnabled(False)

        # UI state: translating
        self._translate_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self.activity_started.emit(self._i18n.get("status.writer_draft"))

        # Start Stage 1
        self._start_draft(korean_text)

    def _start_draft(self, korean_text: str):
        """Stage 1: Korean -> English draft."""
        if self._draft_worker and self._draft_worker.isRunning():
            self._draft_worker.stop()
            self._draft_worker.wait(2000)

        self._draft_worker = GenerationWorker()
        self._draft_worker.token_received.connect(self._on_draft_token)
        self._draft_worker.finished_signal.connect(self._on_draft_finished)
        self._draft_worker.error_occurred.connect(self._on_error)
        self._draft_worker.configure(self._writer.draft, korean_text)
        self._start_loading_animation(self._draft_output)
        self._draft_worker.start()

    def _on_draft_token(self, token: str):
        self._stop_loading_animation()
        cursor = self._draft_output.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(token)
        self._draft_text += token

    def _on_draft_finished(self, full_text: str):
        self._draft_text = full_text

        # Check if draft only
        if self._draft_only_cb.isChecked():
            self._on_all_done()
            return

        # Start Stage 2
        self._start_polish(full_text)

    def _start_polish(self, english_draft: str):
        """Stage 2: English -> Reddit-ready."""
        if self._polish_worker and self._polish_worker.isRunning():
            self._polish_worker.stop()
            self._polish_worker.wait(2000)

        self._polish_worker = GenerationWorker()
        self._polish_worker.token_received.connect(self._on_polish_token)
        self._polish_worker.finished_signal.connect(self._on_polish_finished)
        self._polish_worker.error_occurred.connect(self._on_error)
        self._polish_worker.configure(self._writer.polish, english_draft)
        self._start_loading_animation(self._final_output)
        self.activity_started.emit(self._i18n.get("status.writer_polish"))
        self._polish_worker.start()

    def _on_polish_token(self, token: str):
        self._stop_loading_animation()
        cursor = self._final_output.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(token)

    def _on_polish_finished(self, full_text: str):
        self._on_all_done()

    def _on_all_done(self):
        """Pipeline complete. Restore button state."""
        self._translate_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._copy_btn.setEnabled(True)
        self._stop_loading_animation()
        self.activity_finished.emit()

    def _on_stop(self):
        """Stop current generation."""
        if self._draft_worker and self._draft_worker.isRunning():
            self._draft_worker.stop()
        if self._polish_worker and self._polish_worker.isRunning():
            self._polish_worker.stop()
        self._on_all_done()

    def _on_error(self, error_key: str):
        self._final_output.setPlainText(self._i18n.get(error_key))
        self._on_all_done()

    def _on_copy(self):
        """Copy final output (or draft if draft-only) to clipboard."""
        text = self._final_output.toPlainText()
        if not text:
            text = self._draft_output.toPlainText()
        if text:
            clipboard = QApplication.clipboard()
            clipboard.setText(text)
            self._copy_btn.setText(self._i18n.get("writer.copied"))
            # Reset after 2 seconds would need a QTimer, keep simple for now

    def retranslate_ui(self):
        """Update all labels for locale change."""
        self._header.setText(self._i18n.get("writer.header"))
        self._input.setPlaceholderText(self._i18n.get("writer.placeholder"))
        self._translate_btn.setText(self._i18n.get("writer.translate_btn"))
        self._draft_only_cb.setText(self._i18n.get("writer.draft_only"))
        self._stop_btn.setText(self._i18n.get("writer.stop_btn"))
        self._draft_label.setText(self._i18n.get("writer.draft_label"))
        self._final_label.setText(self._i18n.get("writer.final_label"))
        self._copy_btn.setText(self._i18n.get("writer.copy_btn"))

    def _start_loading_animation(self, target: QTextEdit):
        """Start animated '생성 중...' in a text area."""
        self._anim_target = target
        self._anim_dot_count = 0
        self._anim_timer.start()
        self._animate_loading()  # show immediately

    def _stop_loading_animation(self):
        """Stop the loading animation."""
        self._anim_timer.stop()
        self._anim_target = None

    def _animate_loading(self):
        """Update the animated loading text."""
        if self._anim_target is None:
            return
        self._anim_dot_count = (self._anim_dot_count + 1) % 4
        dots = "." * self._anim_dot_count
        base = self._i18n.get("writer.generating")
        # Remove trailing dots from base and add animated dots
        base_clean = base.rstrip(".")
        self._anim_target.setPlaceholderText(f"{base_clean}{dots}")
