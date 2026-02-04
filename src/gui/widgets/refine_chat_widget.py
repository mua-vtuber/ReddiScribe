"""Refine chat widget for iterative translation refinement."""

import logging
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QScrollArea, QFrame,
)
from PyQt6.QtCore import Qt, pyqtSignal

from src.core.i18n_manager import I18nManager

logger = logging.getLogger("reddiscribe")


class ChatBubble(QFrame):
    """A single chat message bubble."""

    def __init__(self, text: str, is_ai: bool, parent=None):
        super().__init__(parent)
        self._is_ai = is_ai
        self._setup_ui(text)

    def _setup_ui(self, text: str):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        if self._is_ai:
            self.setStyleSheet(
                "ChatBubble {"
                "  background-color: #2d2d3d;"
                "  border-radius: 8px;"
                "  margin-right: 40px;"
                "}"
            )
            label.setStyleSheet("color: #e0e0e0; font-size: 13px;")
        else:
            self.setStyleSheet(
                "ChatBubble {"
                "  background-color: #1a3a5c;"
                "  border-radius: 8px;"
                "  margin-left: 40px;"
                "}"
            )
            label.setStyleSheet("color: #e0e0e0; font-size: 13px;")

        layout.addWidget(label)


class TranslationBubble(QFrame):
    """A highlighted translation suggestion within chat."""

    def __init__(self, translation: str, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        label = QLabel(translation)
        label.setWordWrap(True)
        label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        self.setStyleSheet(
            "TranslationBubble {"
            "  background-color: #1a4a2a;"
            "  border: 1px solid #2d6b3d;"
            "  border-radius: 8px;"
            "  margin-right: 40px;"
            "}"
        )
        label.setStyleSheet("color: #c8e6c9; font-size: 13px; font-style: italic;")

        layout.addWidget(label)


class RefineChatWidget(QWidget):
    """Refine chat panel for iterative translation refinement.

    Signals:
        message_sent(str): User typed and sent a message
        translation_suggested(str): AI suggested a new translation via [TRANSLATION] tags
    """

    message_sent = pyqtSignal(str)
    translation_suggested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._i18n = I18nManager()
        self._init_ui()
        self._streaming_label: Optional[QLabel] = None
        self._streaming_bubble: Optional[ChatBubble] = None

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 4)

        # Header
        self._header = QLabel(self._i18n.get("writer.refine_header"))
        self._header.setStyleSheet("font-size: 14px; font-weight: bold; padding: 4px;")
        layout.addWidget(self._header)

        # Chat message area (scrollable)
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll_area.setStyleSheet(
            "QScrollArea { border: 1px solid #3d3d3d; border-radius: 4px; }"
        )

        self._chat_container = QWidget()
        self._chat_layout = QVBoxLayout(self._chat_container)
        self._chat_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._chat_layout.setSpacing(4)
        self._chat_layout.addStretch()  # Push messages to top

        self._scroll_area.setWidget(self._chat_container)
        layout.addWidget(self._scroll_area)

        # Input row
        input_layout = QHBoxLayout()
        input_layout.setSpacing(4)

        self._input = QLineEdit()
        self._input.setPlaceholderText(self._i18n.get("writer.refine_placeholder"))
        self._input.returnPressed.connect(self._on_send)
        self._input.setEnabled(False)
        input_layout.addWidget(self._input)

        self._send_btn = QPushButton(self._i18n.get("writer.refine_send"))
        self._send_btn.clicked.connect(self._on_send)
        self._send_btn.setEnabled(False)
        self._send_btn.setFixedWidth(60)
        input_layout.addWidget(self._send_btn)

        layout.addLayout(input_layout)

    def _on_send(self):
        """Handle send button click or Enter key."""
        text = self._input.text().strip()
        if not text:
            return
        self._input.clear()
        self.add_user_message(text)
        self.message_sent.emit(text)

    def add_ai_message(self, text: str):
        """Add an AI message bubble to the chat."""
        # Insert before the stretch
        idx = self._chat_layout.count() - 1
        bubble = ChatBubble(text, is_ai=True)
        self._chat_layout.insertWidget(idx, bubble)
        self._scroll_to_bottom()

    def add_user_message(self, text: str):
        """Add a user message bubble to the chat."""
        idx = self._chat_layout.count() - 1
        bubble = ChatBubble(text, is_ai=False)
        self._chat_layout.insertWidget(idx, bubble)
        self._scroll_to_bottom()

    def add_translation_suggestion(self, translation: str):
        """Add a highlighted translation suggestion bubble."""
        idx = self._chat_layout.count() - 1
        bubble = TranslationBubble(translation)
        self._chat_layout.insertWidget(idx, bubble)
        self._scroll_to_bottom()
        self.translation_suggested.emit(translation)

    def clear_chat(self):
        """Remove all messages from the chat."""
        while self._chat_layout.count() > 1:  # Keep the stretch
            item = self._chat_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def set_input_enabled(self, enabled: bool):
        """Enable or disable the input area."""
        self._input.setEnabled(enabled)
        self._send_btn.setEnabled(enabled)
        if enabled:
            self._input.setFocus()

    def _scroll_to_bottom(self):
        """Scroll chat area to the bottom."""
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self._scroll_area.verticalScrollBar().setValue(
            self._scroll_area.verticalScrollBar().maximum()
        ))

    def start_streaming_ai_message(self):
        """Start a new AI message bubble for streaming tokens."""
        idx = self._chat_layout.count() - 1
        bubble = ChatBubble("", is_ai=True)
        self._chat_layout.insertWidget(idx, bubble)
        self._streaming_bubble = bubble
        # Get the QLabel from the bubble's layout
        self._streaming_label = bubble.layout().itemAt(0).widget()
        self._scroll_to_bottom()

    def append_to_streaming_message(self, token: str):
        """Append a token to the current streaming AI message."""
        if self._streaming_label:
            current = self._streaming_label.text()
            self._streaming_label.setText(current + token)
            self._scroll_to_bottom()

    def finish_streaming_message(self, final_text: str = None):
        """Finalize the streaming message.

        Args:
            final_text: If provided, replace bubble text (e.g. comment without tags).
                        If empty string, remove the bubble entirely.
        """
        if self._streaming_label and final_text is not None:
            if final_text:
                self._streaming_label.setText(final_text)
            elif self._streaming_bubble:
                # Empty comment - remove the bubble entirely
                self._streaming_bubble.deleteLater()
        self._streaming_label = None
        self._streaming_bubble = None

    def retranslate_ui(self):
        """Update labels for locale change."""
        self._header.setText(self._i18n.get("writer.refine_header"))
        self._input.setPlaceholderText(self._i18n.get("writer.refine_placeholder"))
        self._send_btn.setText(self._i18n.get("writer.refine_send"))
