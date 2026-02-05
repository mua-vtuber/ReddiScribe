"""Writer widget for Korean -> English translation with Reddit tone polishing."""

import logging
from typing import Optional
from urllib.parse import quote_plus

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTextEdit, QCheckBox,
    QApplication, QMessageBox, QLineEdit,
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices

from src.core.i18n_manager import I18nManager
from src.core.types import WriterContext
from src.gui.workers import GenerationWorker
from src.gui.task_coordinator import TaskCoordinator
from src.gui.widgets.refine_chat_widget import RefineChatWidget
from src.services.writer_service import WriterService, parse_refine_response

logger = logging.getLogger("reddiscribe")


class WriterWidget(QWidget):
    """Writer tab - 2-stage translation pipeline UI."""

    activity_started = pyqtSignal(str)   # task name for global status
    activity_finished = pyqtSignal(str)     # task completed
    navigate_to_settings = pyqtSignal()

    _MAX_REFINE_MESSAGES = 20  # Keep conversations manageable for context window

    def __init__(self, writer_service: WriterService, config, coordinator: TaskCoordinator, parent=None):
        super().__init__(parent)
        self._writer = writer_service
        self._config = config
        self._i18n = I18nManager()
        self._coordinator = coordinator
        self._pending_translate_text: Optional[str] = None  # queued text for after polish

        self._draft_worker: Optional[GenerationWorker] = None
        self._polish_worker: Optional[GenerationWorker] = None
        self._draft_text: str = ""  # collected draft for Stage 2 input
        self._current_activity_name: str = ""  # track last activity for finish signal
        self._refine_worker: Optional[GenerationWorker] = None
        self._refine_messages: list[dict] = []  # chat history for /api/chat
        self._pending_translation: Optional[str] = None  # Apply 대기 중인 수정안
        self._source_input_text: str = ""  # store for refine context
        self._current_context: Optional[WriterContext] = None
        self._current_subreddit: str = ""
        # Streaming parse state: before "%%%" goes to final, after goes to chat
        self._refine_started: bool = False  # True after "%%%" is detected
        self._is_first_refine: bool = True  # first response splits to final+chat
        self._chat_streamed_content: str = ""  # track what was sent to chat
        self._token_buffer: str = ""  # buffer for detecting "%%%" across tokens

        self._init_ui()

        # Loading animation
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(500)
        self._anim_timer.timeout.connect(self._animate_loading)
        self._anim_dot_count = 0
        self._anim_target: Optional[QTextEdit] = None

    def _init_ui(self):
        # Top-level vertical layout: context bar + content
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # === Context Bar (hidden by default) ===
        self._context_bar = QWidget()
        self._context_bar.setStyleSheet(
            "background-color: #1e3a5f; border-bottom: 1px solid #2d5a8f;"
        )
        ctx_layout = QVBoxLayout(self._context_bar)
        ctx_layout.setContentsMargins(12, 6, 12, 6)
        ctx_layout.setSpacing(2)

        ctx_top_row = QHBoxLayout()
        self._context_info_label = QLabel()
        self._context_info_label.setStyleSheet(
            "color: #e0e0e0; font-size: 13px; font-weight: bold;"
        )
        ctx_top_row.addWidget(self._context_info_label)
        ctx_top_row.addStretch()

        self._view_content_btn = QPushButton(self._i18n.get("writer.view_content"))
        self._view_content_btn.setFixedWidth(80)
        self._view_content_btn.setStyleSheet(
            "QPushButton { background-color: #2d5a8f; color: white; "
            "border: none; border-radius: 4px; padding: 4px 8px; font-size: 12px; }"
            "QPushButton:hover { background-color: #3d6a9f; }"
        )
        self._view_content_btn.clicked.connect(self._on_view_content)
        ctx_top_row.addWidget(self._view_content_btn)
        ctx_layout.addLayout(ctx_top_row)

        self._context_detail_label = QLabel()
        self._context_detail_label.setStyleSheet(
            "color: #aaaacc; font-size: 12px;"
        )
        self._context_detail_label.setWordWrap(True)
        self._context_detail_label.hide()
        ctx_layout.addWidget(self._context_detail_label)

        self._context_bar.hide()
        root_layout.addWidget(self._context_bar)

        # === Content area: horizontal split (left pipeline + right chat) ===
        content_layout = QHBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)

        # === LEFT PANEL: translation pipeline ===
        left_panel = QWidget()
        layout = QVBoxLayout(left_panel)
        layout.setContentsMargins(8, 8, 4, 8)

        # Header
        self._header = QLabel(self._i18n.get("writer.header"))
        self._header.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(self._header)

        # Title input (new_post mode only, hidden by default)
        self._title_input = QLineEdit()
        self._title_input.setPlaceholderText(self._i18n.get("writer.title_placeholder"))
        self._title_input.hide()
        layout.addWidget(self._title_input)

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

        # Bottom buttons: Copy + Apply + Submit
        bottom_btn_layout = QHBoxLayout()

        self._copy_btn = QPushButton(self._i18n.get("writer.copy_btn"))
        self._copy_btn.clicked.connect(self._on_copy)
        self._copy_btn.setEnabled(False)
        bottom_btn_layout.addWidget(self._copy_btn)

        self._apply_btn = QPushButton(self._i18n.get("writer.refine_apply"))
        self._apply_btn.clicked.connect(self._on_apply)
        self._apply_btn.setEnabled(False)
        bottom_btn_layout.addWidget(self._apply_btn)

        bottom_btn_layout.addStretch()

        self._submit_btn = QPushButton(self._i18n.get("writer.submit_btn"))
        self._submit_btn.clicked.connect(self._on_submit)
        self._submit_btn.setEnabled(False)
        self._submit_btn.setStyleSheet(
            "QPushButton { background-color: #2d6b3d; color: white; "
            "border: none; border-radius: 4px; padding: 6px 16px; font-weight: bold; }"
            "QPushButton:hover { background-color: #3d8b4d; }"
            "QPushButton:disabled { background-color: #444444; color: #888888; }"
        )
        bottom_btn_layout.addWidget(self._submit_btn)

        layout.addLayout(bottom_btn_layout)

        content_layout.addWidget(left_panel, stretch=3)

        # === RIGHT PANEL: Refine Chat (unchanged) ===
        self._refine_chat = RefineChatWidget()
        self._refine_chat.message_sent.connect(self._on_refine_message)
        self._refine_chat.translation_suggested.connect(self._on_translation_suggested)
        content_layout.addWidget(self._refine_chat, stretch=2)

        root_layout.addLayout(content_layout)

    def _check_models_configured(self, roles: list[str]) -> bool:
        """Check if required models are configured. Shows dialog if not.

        Returns True if all models configured, False otherwise.
        """
        missing = self._config.get_missing_models(roles)
        if not missing:
            return True

        role_names = {
            "logic": self._i18n.get("settings.model_role_logic"),
            "persona": self._i18n.get("settings.model_role_persona"),
        }
        missing_names = ", ".join(role_names.get(r, r) for r in missing)

        msg = QMessageBox(self)
        msg.setWindowTitle(self._i18n.get("errors.model_not_configured"))
        msg.setText(self._i18n.get("errors.model_not_configured_detail").replace("{models}", missing_names))
        msg.setIcon(QMessageBox.Icon.Warning)

        settings_btn = msg.addButton(
            self._i18n.get("errors.go_to_settings"),
            QMessageBox.ButtonRole.AcceptRole,
        )
        msg.addButton(QMessageBox.StandardButton.Cancel)
        msg.exec()

        if msg.clickedButton() == settings_btn:
            self.navigate_to_settings.emit()
        return False

    def _on_translate(self):
        """Start translation pipeline. Handles conflict if polish is running."""
        korean_text = self._input.toPlainText().strip()
        if not korean_text:
            return

        # Check if required models are configured
        roles = ["logic"] if self._draft_only_cb.isChecked() else ["logic", "persona"]
        if not self._check_models_configured(roles):
            return

        # Check if polish (exclusive) is currently running
        if self._coordinator.is_exclusive_active():
            self._show_polish_conflict_dialog(korean_text)
            return

        self._start_translation_pipeline(korean_text)

    def _show_polish_conflict_dialog(self, korean_text: str):
        """Show dialog when user tries to translate while polish is running."""
        msg = QMessageBox(self)
        msg.setWindowTitle(self._i18n.get("writer.polish_in_progress_title"))
        msg.setText(self._i18n.get("writer.polish_in_progress_msg"))
        msg.setIcon(QMessageBox.Icon.Information)

        cancel_btn = msg.addButton(
            self._i18n.get("writer.cancel_and_new"),
            QMessageBox.ButtonRole.AcceptRole,
        )
        wait_btn = msg.addButton(
            self._i18n.get("writer.wait_for_finish"),
            QMessageBox.ButtonRole.RejectRole,
        )
        msg.setDefaultButton(wait_btn)
        msg.exec()

        if msg.clickedButton() == cancel_btn:
            # Cancel current polish, start new pipeline
            self._on_stop()  # stops workers + calls _on_all_done which finishes exclusive
            self._start_translation_pipeline(korean_text)
        elif msg.clickedButton() == wait_btn:
            # Queue the new translation for after polish finishes
            self._pending_translate_text = korean_text
            self._coordinator.exclusive_finished.connect(self._on_exclusive_done_start_queued)

    def _on_exclusive_done_start_queued(self):
        """Start queued translation after exclusive task finishes."""
        self._coordinator.exclusive_finished.disconnect(self._on_exclusive_done_start_queued)
        if self._pending_translate_text:
            text = self._pending_translate_text
            self._pending_translate_text = None
            self._start_translation_pipeline(text)

    def _start_translation_pipeline(self, korean_text: str):
        """Start the full translation pipeline (draft + optional polish)."""
        # Reset outputs
        self._draft_output.clear()
        self._final_output.clear()
        self._draft_text = ""
        self._source_input_text = korean_text
        self._refine_messages = []
        self._pending_translation = None
        self._apply_btn.setEnabled(False)
        self._refine_chat.clear_chat()
        self._refine_chat.set_input_enabled(False)
        self._copy_btn.setEnabled(False)
        self._submit_btn.setEnabled(False)

        # UI state: translating
        self._translate_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._current_activity_name = self._i18n.get("status.writer_draft")
        self.activity_started.emit(self._current_activity_name)

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

        # Finish draft activity before transitioning to refine
        self.activity_finished.emit(self._current_activity_name)

        # Skip polish stage - go directly to refine which creates 2nd translation
        self._start_refine_chat()

    def _start_polish(self, english_draft: str):
        """Stage 2: English -> Reddit-ready."""
        if self._polish_worker and self._polish_worker.isRunning():
            self._polish_worker.stop()
            self._polish_worker.wait(2000)

        self._polish_worker = GenerationWorker()
        self._polish_worker.token_received.connect(self._on_polish_token)
        self._polish_worker.finished_signal.connect(self._on_polish_finished)
        self._polish_worker.error_occurred.connect(self._on_error)
        self._polish_worker.configure(
            self._writer.polish, english_draft,
            korean_text=self._source_input_text, context=self._current_context
        )
        self._start_loading_animation(self._final_output)
        self._current_activity_name = self._i18n.get("status.writer_polish")
        self.activity_started.emit(self._current_activity_name)
        self._polish_worker.start()

    def _on_polish_token(self, token: str):
        self._stop_loading_animation()
        cursor = self._final_output.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(token)

    def _on_polish_finished(self, full_text: str):
        # Note: Polish stage is now skipped, this method kept for compatibility
        self._on_all_done()

    def _on_all_done(self):
        """Pipeline complete. Restore button state and notify coordinator."""
        self._translate_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._copy_btn.setEnabled(True)
        self._submit_btn.setEnabled(True)
        self._stop_loading_animation()
        # Finish exclusive task if polish was running
        if self._coordinator.is_exclusive_active():
            self._coordinator.finish_exclusive()
        self.activity_finished.emit(self._current_activity_name)

    def _on_stop(self):
        """Stop current generation."""
        if self._draft_worker and self._draft_worker.isRunning():
            self._draft_worker.stop()
        if self._polish_worker and self._polish_worker.isRunning():
            self._polish_worker.stop()
        if self._refine_worker and self._refine_worker.isRunning():
            self._refine_worker.stop()
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

    def _start_refine_chat(self):
        """Start the refine chat session - creates 2nd translation + explanation."""
        # Determine comment language from current locale
        locale = self._i18n.locale
        comment_lang = "한국어" if locale.startswith("ko") else "English"

        # Reset streaming state
        self._refine_started = False
        self._is_first_refine = True
        self._chat_streamed_content = ""
        self._token_buffer = ""

        # Show loading in final output first (before chat bubble appears)
        self._start_loading_animation(self._final_output)

        # Build initial context (refine will create 2nd translation)
        self._refine_messages = self._writer.build_refine_context(
            self._source_input_text,
            self._draft_text,
            comment_lang=comment_lang,
            context=self._current_context,
        )
        # Don't enable chat input until translation is done
        self._refine_chat.set_input_enabled(False)
        # Add seed message requesting translation + explanation
        seed = ("2차 번역을 작성하고 왜 그렇게 바꿨는지 설명해 주세요."
                if locale.startswith("ko")
                else "Create the polished translation and explain your changes.")
        self._refine_messages.append({
            "role": "user",
            "content": seed,
        })
        # Auto-generate first AI response (translation + explanation)
        self._send_refine_request()

    def _on_refine_message(self, text: str):
        """Handle user message from refine chat."""
        self._refine_messages.append({"role": "user", "content": text})
        self._refine_chat.set_input_enabled(False)
        # Reset streaming state for follow-up
        self._refine_started = False
        self._is_first_refine = False  # Follow-up goes entirely to chat
        self._chat_streamed_content = ""
        self._token_buffer = ""
        self._send_refine_request()

    def _on_refine_token(self, token: str):
        """Handle streaming token - route to final output or chat based on '%%%' detection."""
        # For follow-up messages, everything goes to chat
        if not self._is_first_refine:
            self._refine_chat.append_to_streaming_message(token)
            self._chat_streamed_content += token
            return

        # First response: split at "%%%" - before goes to final, after goes to chat
        if self._refine_started:
            # Already past "%%%", send to chat
            self._refine_chat.append_to_streaming_message(token)
            self._chat_streamed_content += token
        else:
            # Buffer tokens to detect "%%%" that might be split across tokens
            self._token_buffer += token

            if "%%%" in self._token_buffer:
                idx = self._token_buffer.index("%%%")
                # Text before "%%%" goes to final output
                before = self._token_buffer[:idx].rstrip()  # strip trailing newline
                if before:
                    self._stop_loading_animation()
                    cursor = self._final_output.textCursor()
                    cursor.movePosition(cursor.MoveOperation.End)
                    cursor.insertText(before)
                # Now start the chat bubble for explanation
                self._refine_chat.start_streaming_ai_message()
                # After "%%%" goes to chat (skip the delimiter itself)
                after = self._token_buffer[idx + 3:].lstrip()  # strip leading newline
                if after:
                    self._refine_chat.append_to_streaming_message(after)
                    self._chat_streamed_content += after
                self._token_buffer = ""
                self._refine_started = True
            elif len(self._token_buffer) > 5:
                # Safe to output (keep last 3 chars in buffer for "%%%" detection)
                self._stop_loading_animation()
                output = self._token_buffer[:-3]
                self._token_buffer = self._token_buffer[-3:]
                cursor = self._final_output.textCursor()
                cursor.movePosition(cursor.MoveOperation.End)
                cursor.insertText(output)

    def _send_refine_request(self):
        """Send current messages to AI for refine response."""
        if self._refine_worker and self._refine_worker.isRunning():
            self._refine_worker.stop()
            self._refine_worker.wait(2000)

        # Prune old messages if conversation is too long (keep system + recent)
        if len(self._refine_messages) > self._MAX_REFINE_MESSAGES:
            system_msg = self._refine_messages[0]  # Always keep system prompt
            self._refine_messages = [system_msg] + self._refine_messages[-(self._MAX_REFINE_MESSAGES - 1):]

        self._refine_worker = GenerationWorker()
        self._refine_worker.token_received.connect(self._on_refine_token)
        self._refine_worker.finished_signal.connect(self._on_refine_finished)
        self._refine_worker.error_occurred.connect(self._on_refine_error)
        self._refine_worker.configure(self._writer.refine, self._refine_messages)

        self._current_activity_name = self._i18n.get("status.writer_refine")
        self.activity_started.emit(self._current_activity_name)

        # Request exclusive access (blocks reader tasks during refine)
        if self._coordinator.request_exclusive("writer_refine", self._do_start_refine):
            self._do_start_refine()

    def _do_start_refine(self):
        """Actually start the refine worker after exclusive access is granted."""
        # For first refine, don't show chat bubble until translation is done (%%% detected)
        if not self._is_first_refine:
            self._refine_chat.start_streaming_ai_message()
        self._refine_worker.start()

    def _on_refine_finished(self, full_text: str):
        """Handle completed refine response."""
        self.activity_finished.emit(self._current_activity_name)
        self._stop_loading_animation()

        # Flush any remaining buffer
        if self._token_buffer and self._is_first_refine and not self._refine_started:
            # No "%%%" was found, remaining buffer is translation
            cursor = self._final_output.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            cursor.insertText(self._token_buffer)
            self._token_buffer = ""

        # Append assistant response to history
        self._refine_messages.append({"role": "assistant", "content": full_text})

        # Finish the chat bubble with accumulated content
        self._refine_chat.finish_streaming_message(self._chat_streamed_content.strip())

        if self._is_first_refine:
            # First response: translation already in final output via streaming
            self._copy_btn.setEnabled(True)
            self._submit_btn.setEnabled(True)
        else:
            # Follow-up: check for translation (text before "%%%")
            if "%%%" in full_text:
                translation = full_text[:full_text.index("%%%")].strip()
                if translation:
                    self._refine_chat.add_translation_suggestion(translation)

        self._refine_chat.set_input_enabled(True)
        self._translate_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        if self._coordinator.is_exclusive_active():
            self._coordinator.finish_exclusive()

    def _on_refine_error(self, error_key: str):
        """Handle refine chat error."""
        self.activity_finished.emit(self._current_activity_name)
        self._refine_chat.add_ai_message(self._i18n.get(error_key))
        self._refine_chat.set_input_enabled(True)
        if self._coordinator.is_exclusive_active():
            self._coordinator.finish_exclusive()

    def _on_apply(self):
        """Apply the pending translation to Stage 2 output."""
        if self._pending_translation:
            self._final_output.setPlainText(self._pending_translation)
            self._pending_translation = None
            self._apply_btn.setEnabled(False)
            # Show confirmation in chat
            self._refine_chat.add_ai_message(self._i18n.get("writer.refine_applied"))

    def _on_translation_suggested(self, translation: str):
        """Handle translation suggestion from chat (signal receiver)."""
        self._pending_translation = translation
        self._apply_btn.setEnabled(True)

    def set_context(self, ctx: WriterContext):
        """Set writer context from Reader (comment/reply mode)."""
        self._current_context = ctx
        self._update_context_bar()
        # Reset outputs for new context
        self._draft_output.clear()
        self._final_output.clear()
        self._input.clear()
        self._refine_chat.clear_chat()
        self._refine_messages = []
        self._pending_translation = None
        self._apply_btn.setEnabled(False)
        self._submit_btn.setEnabled(False)
        self._copy_btn.setEnabled(False)

    def set_subreddit(self, subreddit: str):
        """Update current subreddit from top bar."""
        self._current_subreddit = subreddit
        # If in new_post mode, update context bar
        if self._current_context is None or self._current_context.mode == "new_post":
            self._current_context = WriterContext(mode="new_post", subreddit=subreddit)
            self._update_context_bar()

    def _update_context_bar(self):
        """Update context bar display based on current context."""
        ctx = self._current_context
        if ctx is None:
            self._context_bar.hide()
            self._title_input.hide()
            self._view_content_btn.hide()
            return

        self._context_bar.show()

        if ctx.mode == "new_post":
            mode_text = self._i18n.get("writer.context_new_post")
            self._context_info_label.setText(f"{mode_text} — r/{ctx.subreddit}")
            self._context_detail_label.hide()
            self._title_input.show()
            self._view_content_btn.hide()
        elif ctx.mode == "comment":
            mode_text = self._i18n.get("writer.context_comment")
            title_excerpt = ctx.post_title[:60] + "..." if len(ctx.post_title) > 60 else ctx.post_title
            self._context_info_label.setText(
                f"{mode_text} — r/{ctx.subreddit} > \"{title_excerpt}\""
            )
            self._context_detail_label.hide()
            self._title_input.hide()
            self._view_content_btn.setVisible(bool(ctx.post_selftext))
        elif ctx.mode == "reply":
            mode_text = self._i18n.get("writer.context_reply")
            title_excerpt = ctx.post_title[:60] + "..." if len(ctx.post_title) > 60 else ctx.post_title
            self._context_info_label.setText(
                f"{mode_text} — r/{ctx.subreddit} > \"{title_excerpt}\""
            )
            # Show reply target
            reply_text = self._i18n.get("writer.reply_to").replace("{author}", ctx.comment_author)
            body_excerpt = ctx.comment_body[:100] + "..." if len(ctx.comment_body) > 100 else ctx.comment_body
            self._context_detail_label.setText(f"{reply_text}: \"{body_excerpt}\"")
            self._context_detail_label.show()
            self._title_input.hide()
            self._view_content_btn.show()

    def _on_view_content(self):
        """Open content view dialog."""
        if self._current_context is None:
            return
        from src.gui.widgets.content_view_dialog import ContentViewDialog
        dialog = ContentViewDialog(self._current_context, parent=self)
        dialog.show()

    def _on_submit(self):
        """Submit the final text to Reddit via browser."""
        # Get the final text
        final_text = self._final_output.toPlainText().strip()
        if not final_text:
            final_text = self._draft_output.toPlainText().strip()
        if not final_text:
            return

        ctx = self._current_context
        if ctx is None:
            return

        if ctx.mode == "new_post":
            title = self._title_input.text().strip()
            sub = ctx.subreddit or self._current_subreddit
            url = (
                f"https://www.reddit.com/r/{sub}/submit"
                f"?selftext=true"
                f"&title={quote_plus(title)}"
                f"&text={quote_plus(final_text)}"
            )
            QDesktopServices.openUrl(QUrl(url))
        elif ctx.mode == "comment":
            # Copy text to clipboard and open post
            clipboard = QApplication.clipboard()
            clipboard.setText(final_text)
            url = f"https://www.reddit.com{ctx.post_permalink}"
            QDesktopServices.openUrl(QUrl(url))
            self._status_message(self._i18n.get("writer.submit_clipboard_msg"))
        elif ctx.mode == "reply":
            # Copy text to clipboard and open comment permalink
            clipboard = QApplication.clipboard()
            clipboard.setText(final_text)
            url = f"https://www.reddit.com{ctx.post_permalink}{ctx.comment_id}/"
            QDesktopServices.openUrl(QUrl(url))
            self._status_message(self._i18n.get("writer.submit_clipboard_msg"))

    def _status_message(self, msg: str):
        """Show status message via parent's status bar if available."""
        parent = self.window()
        if hasattr(parent, 'statusBar'):
            parent.statusBar().showMessage(msg, 5000)

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
        self._apply_btn.setText(self._i18n.get("writer.refine_apply"))
        self._title_input.setPlaceholderText(self._i18n.get("writer.title_placeholder"))
        self._view_content_btn.setText(self._i18n.get("writer.view_content"))
        self._submit_btn.setText(self._i18n.get("writer.submit_btn"))
        self._update_context_bar()  # refresh context labels
        self._refine_chat.retranslate_ui()

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
