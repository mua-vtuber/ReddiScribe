"""Non-modal dialog to view original post/comment content."""

import logging

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QTextEdit,
)
from PyQt6.QtCore import Qt

from src.core.i18n_manager import I18nManager
from src.core.types import WriterContext

logger = logging.getLogger("reddiscribe")


class ContentViewDialog(QDialog):
    """Non-modal dialog showing original post/comment content.

    Used from Writer tab when user wants to see the original
    content they're replying to or commenting on.
    """

    def __init__(self, context: WriterContext, parent=None):
        super().__init__(parent)
        self._i18n = I18nManager()
        self._context = context
        self.setWindowTitle(self._i18n.get("writer.content_dialog_title"))
        self.setMinimumSize(500, 400)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # Post info
        if self._context.post_title:
            title_label = QLabel(f"r/{self._context.subreddit} — {self._context.post_title}")
            title_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #e0e0e0;")
            title_label.setWordWrap(True)
            layout.addWidget(title_label)

        # Parent thread (for reply mode)
        if self._context.mode == "reply" and self._context.parent_thread:
            thread_label = QLabel("Thread:")
            thread_label.setStyleSheet("font-weight: bold; color: #aaaaaa; margin-top: 8px;")
            layout.addWidget(thread_label)

            for item in self._context.parent_thread:
                author = item.get("author", "[deleted]")
                body = item.get("body", "")
                depth = item.get("depth", 0)
                indent = "  " * depth
                thread_text = QLabel(f"{indent}@{author}: {body}")
                thread_text.setWordWrap(True)
                thread_text.setStyleSheet(
                    "color: #cccccc; padding: 4px 8px; "
                    "background-color: #333333; border-radius: 4px; margin: 2px 0;"
                )
                thread_text.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse
                )
                layout.addWidget(thread_text)

        # Target comment (for reply mode)
        if self._context.mode == "reply" and self._context.comment_body:
            target_label = QLabel(f"@{self._context.comment_author}:")
            target_label.setStyleSheet(
                "font-weight: bold; color: #e0e0e0; margin-top: 8px;"
            )
            layout.addWidget(target_label)

            target_text = QTextEdit()
            target_text.setReadOnly(True)
            target_text.setPlainText(self._context.comment_body)
            target_text.setMaximumHeight(120)
            layout.addWidget(target_text)

        # Post body
        if self._context.post_selftext:
            body_label = QLabel("Post:")
            body_label.setStyleSheet("font-weight: bold; color: #aaaaaa; margin-top: 8px;")
            layout.addWidget(body_label)

            body_text = QTextEdit()
            body_text.setReadOnly(True)
            body_text.setPlainText(self._context.post_selftext)
            layout.addWidget(body_text)

        layout.addStretch()
