"""Reader widget for browsing Reddit posts with AI translation."""

import logging
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QPushButton, QLabel,
    QTextEdit, QComboBox, QScrollArea,
    QFrame, QMessageBox,
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer

from src.core.i18n_manager import I18nManager
from src.core.types import PostDTO, CommentDTO, WriterContext
from src.gui.workers import RedditFetchWorker, GenerationWorker
from src.gui.task_coordinator import TaskCoordinator
from src.services.reader_service import ReaderService

logger = logging.getLogger("reddiscribe")

# Client-side rendering depth limit (spec 7.2)
MAX_COMMENT_DEPTH = 5
COMMENTS_RENDER_BATCH = 5


class ReaderWidget(QWidget):
    """Reader tab - browse subreddits, read posts, get AI translation.

    Layout (spec Section 7.2):
        Post list with sort selector, translation/original panes, comment tree.
        Subreddit selection is managed by MainWindow via TopBar.
    """

    activity_started = pyqtSignal(str)
    activity_finished = pyqtSignal(str)
    navigate_to_settings = pyqtSignal()
    write_requested = pyqtSignal(object)  # WriterContext

    def __init__(self, reader_service: ReaderService, config, coordinator: TaskCoordinator, parent=None):
        """Initialize the reader widget.

        Args:
            reader_service: ReaderService instance for data fetching and translation.
            config: ConfigManager instance for persisting subreddit list and reading locale.
            parent: Optional parent QWidget.
        """
        super().__init__(parent)
        self._reader = reader_service
        self._config = config
        self._i18n = I18nManager()
        self._current_posts: list[PostDTO] = []
        self._current_post: Optional[PostDTO] = None
        self._current_subreddit: str = ""

        # Workers (kept as instance attrs to prevent GC and allow stop)
        self._fetch_worker: Optional[RedditFetchWorker] = None
        self._comment_worker: Optional[RedditFetchWorker] = None
        self._gen_worker: Optional[GenerationWorker] = None
        self._title_worker: Optional[GenerationWorker] = None
        self._comment_translate_worker: Optional[GenerationWorker] = None
        self._translated_titles: dict[int, str] = {}  # row index -> translated title
        self._comments_list: list[CommentDTO] = []  # stored for lazy rendering
        self._translated_comment_count: int = 0  # how many comments translated so far
        self._rendered_comment_count: int = 0  # how many top-level comments rendered
        self._showing_original: bool = False  # toggle state for original/translation
        self._coordinator = coordinator
        self._comment_widgets: dict[str, QFrame] = {}  # comment_id -> frame widget
        self._more_indicator = None

        self._init_ui()

        # Loading animation
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(500)
        self._anim_timer.timeout.connect(self._animate_loading)
        self._anim_dot_count = 0
        self._anim_target: Optional[QTextEdit] = None

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _init_ui(self):
        """Build the full reader layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Posts + content + comments
        right_panel = self._build_right_panel()
        layout.addWidget(right_panel)

    def _build_right_panel(self) -> QWidget:
        """Build the post list, content panes, and comment tree."""
        panel = QWidget()
        panel_layout = QVBoxLayout(panel)

        # -- Post list header with sort selector --
        post_header = QHBoxLayout()
        self._posts_label = QLabel(self._i18n.get("reader.posts"))
        post_header.addWidget(self._posts_label)
        post_header.addStretch()

        self._sort_combo = QComboBox()
        self._sort_combo.addItems(["Hot", "New", "Top", "Rising"])
        self._sort_combo.currentTextChanged.connect(self._on_sort_changed)
        post_header.addWidget(self._sort_combo)
        panel_layout.addLayout(post_header)

        # -- Post list --
        self._post_list = QListWidget()
        self._post_list.currentRowChanged.connect(self._on_post_selected)
        self._post_list.setMaximumHeight(200)
        panel_layout.addWidget(self._post_list)

        # -- Scrollable content: translation + original + comments --
        content_area = QScrollArea()
        content_area.setWidgetResizable(True)
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)

        # Translation section (replaces summary)
        self._translation_label = QLabel(self._i18n.get("reader.translation"))
        self._translation_label.setStyleSheet("font-weight: bold;")
        content_layout.addWidget(self._translation_label)

        self._translation_text = QTextEdit()
        self._translation_text.setReadOnly(True)
        self._translation_text.setMaximumHeight(150)
        content_layout.addWidget(self._translation_text)

        # Toggle + action buttons row
        action_row = QHBoxLayout()

        self._toggle_btn = QPushButton(self._i18n.get("reader.toggle_original"))
        self._toggle_btn.clicked.connect(self._toggle_original_translation)
        action_row.addWidget(self._toggle_btn)

        self._refresh_btn = QPushButton(self._i18n.get("reader.refresh"))
        self._refresh_btn.clicked.connect(self._on_refresh_translation)
        action_row.addWidget(self._refresh_btn)

        action_row.addStretch()

        self._write_comment_btn = QPushButton(self._i18n.get("reader.write_comment"))
        self._write_comment_btn.clicked.connect(self._on_write_comment)
        self._write_comment_btn.setEnabled(False)
        action_row.addWidget(self._write_comment_btn)

        content_layout.addLayout(action_row)

        # Original text (hidden by default)
        self._original_text = QTextEdit()
        self._original_text.setReadOnly(True)
        self._original_text.setMaximumHeight(150)
        self._original_text.hide()
        content_layout.addWidget(self._original_text)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        content_layout.addWidget(sep)

        # Comments header
        comments_header = QHBoxLayout()
        self._comments_label = QLabel(self._i18n.get("reader.comments"))
        self._comments_label.setStyleSheet("font-weight: bold;")
        comments_header.addWidget(self._comments_label)
        comments_header.addStretch()
        content_layout.addLayout(comments_header)

        # Container layout for dynamically-added comment widgets
        self._comments_area = QVBoxLayout()
        content_layout.addLayout(self._comments_area)
        content_layout.addStretch()

        content_area.setWidget(content_widget)
        content_area.verticalScrollBar().valueChanged.connect(self._on_content_scroll)
        self._content_scroll = content_area
        panel_layout.addWidget(content_area)

        return panel

    # ------------------------------------------------------------------
    # Post fetching
    # ------------------------------------------------------------------

    def load_subreddit(self, name: str):
        """Load posts from a subreddit (called by MainWindow via TopBar signal)."""
        self._current_subreddit = name
        self._fetch_posts(name)

    def _on_sort_changed(self, _sort_text: str):
        """Re-fetch posts when sort order changes."""
        if self._current_subreddit:
            self._fetch_posts(self._current_subreddit)

    def _fetch_posts(self, subreddit: str):
        """Start async post fetch via RedditFetchWorker.

        Clears existing UI state immediately and shows loading indicator.
        """
        # Clear right panel state
        self._post_list.clear()
        self._translation_text.clear()
        self._original_text.clear()
        self._write_comment_btn.setEnabled(False)
        self._clear_comments()
        self._current_posts = []
        self._current_post = None

        # Stop ALL running workers (prevents stale results from previous subreddit)
        for worker in (self._fetch_worker, self._gen_worker, self._title_worker,
                       self._comment_worker, self._comment_translate_worker):
            if worker is not None and worker.isRunning():
                worker.stop()
                worker.wait(2000)

        self._fetch_worker = RedditFetchWorker(self._reader)
        self._fetch_worker.posts_ready.connect(self._on_posts_ready)
        self._fetch_worker.error_occurred.connect(self._on_fetch_error)

        sort = self._sort_combo.currentText().lower()
        self._fetch_worker.fetch_posts(subreddit, sort=sort)
        self._fetch_worker.start()

        self._posts_label.setText(self._i18n.get("reader.loading"))

    def _on_posts_ready(self, posts: list):
        """Populate post list when async fetch completes, then start title translation."""
        self._current_posts = posts
        self._post_list.clear()
        self._translated_titles = {}
        self._posts_label.setText(self._i18n.get("reader.posts"))

        if not posts:
            self._posts_label.setText(self._i18n.get("reader.no_posts"))
            return

        for post in posts:
            item_text = f"{post.title}  [\u2191{post.score}]  [\U0001f4ac{post.num_comments}]"
            self._post_list.addItem(item_text)

        # Start title translation (async)
        locale = self._config.get("app.locale", "ko_KR")
        if locale == "ko_KR":
            self._start_title_translation(posts)

    def _on_fetch_error(self, error_key: str):
        """Show localized error in the posts label."""
        self._posts_label.setText(self._i18n.get(error_key))

    # ------------------------------------------------------------------
    # Title translation
    # ------------------------------------------------------------------

    def _start_title_translation(self, posts: list[PostDTO]):
        """Start async batch title translation (coordinator-aware)."""
        # Silent skip if model not configured
        if not self._check_model_configured("logic", show_dialog=False):
            return

        if self._title_worker and self._title_worker.isRunning():
            self._title_worker.stop()
            self._title_worker.wait(2000)

        task_id = "reader_title_translate"

        def do_start():
            self.activity_started.emit(self._i18n.get("status.reader_titles"))
            titles = [p.title for p in self._current_posts]
            if not titles:
                return
            self._title_worker = GenerationWorker()
            self._title_worker.finished_signal.connect(self._on_titles_translated)
            self._title_worker.error_occurred.connect(self._on_title_translate_error)
            locale = self._config.get("app.locale", "ko_KR")
            self._title_worker.configure(self._reader.translate_titles, titles, locale=locale)
            self._title_worker.start()

        if not self._coordinator.request_normal(task_id, do_start):
            return  # queued, will be called back when exclusive finishes
        do_start()

    def _on_titles_translated(self, full_text: str):
        """Parse numbered translations and update post list items."""
        self._coordinator.finish_normal("reader_title_translate")
        self.activity_finished.emit(self._i18n.get("status.reader_titles"))
        lines = full_text.strip().split("\n")
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # Parse "1. translated title" format
            dot_pos = line.find(". ")
            if dot_pos > 0:
                try:
                    idx = int(line[:dot_pos]) - 1  # 1-based to 0-based
                    translated = line[dot_pos + 2:]
                    if 0 <= idx < self._post_list.count():
                        self._translated_titles[idx] = translated
                        post = self._current_posts[idx]
                        item_text = f"{translated}\n{post.title}  [\u2191{post.score}]  [\U0001f4ac{post.num_comments}]"
                        self._post_list.item(idx).setText(item_text)
                except (ValueError, IndexError):
                    continue

    def _on_title_translate_error(self, error_key: str):
        """Title translation failed - just log, posts still show English titles."""
        self._coordinator.finish_normal("reader_title_translate")
        self.activity_finished.emit(self._i18n.get("status.reader_titles"))
        logger.warning(f"Title translation failed: {error_key}")

    # ------------------------------------------------------------------
    # Post selection -> translation + original + comments
    # ------------------------------------------------------------------

    def _on_post_selected(self, row: int):
        """Handle post list selection. Loads translation, original, and comments."""
        if row < 0 or row >= len(self._current_posts):
            return

        post = self._current_posts[row]
        self._current_post = post

        # Original body
        self._original_text.setPlainText(post.selftext or "")
        self._showing_original = False
        self._original_text.hide()
        self._toggle_btn.setText(self._i18n.get("reader.toggle_original"))

        # Enable write comment button
        self._write_comment_btn.setEnabled(True)

        # Translation: check cache first
        locale = self._config.get("app.locale", "ko_KR")
        cached = self._reader.get_translation(post.id, locale=locale)
        if cached:
            self._translation_text.setPlainText(cached)
        elif locale == "ko_KR" and post.selftext:
            self._generate_translation(post)
        else:
            # English locale or no body - just show original
            self._translation_text.setPlainText(post.selftext or post.title)

        # Comments (separate async request)
        self._fetch_comments(post.id, post.subreddit)

    # ------------------------------------------------------------------
    # Translation generation (streaming)
    # ------------------------------------------------------------------

    def _check_model_configured(self, role: str, show_dialog: bool = True) -> bool:
        """Check if a model role is configured.

        Args:
            role: Model role key ("logic", "persona", "summary")
            show_dialog: If True, show navigation dialog. If False, just log warning.

        Returns True if model is configured.
        """
        missing = self._config.get_missing_models([role])
        if not missing:
            return True

        if show_dialog:
            role_names = {
                "logic": self._i18n.get("settings.model_role_logic"),
                "persona": self._i18n.get("settings.model_role_persona"),
            }
            role_name = role_names.get(role, role)

            msg = QMessageBox(self)
            msg.setWindowTitle(self._i18n.get("errors.model_not_configured"))
            msg.setText(self._i18n.get("errors.model_not_configured_detail").replace("{models}", role_name))
            msg.setIcon(QMessageBox.Icon.Warning)

            settings_btn = msg.addButton(
                self._i18n.get("errors.go_to_settings"),
                QMessageBox.ButtonRole.AcceptRole,
            )
            msg.addButton(QMessageBox.StandardButton.Cancel)
            msg.exec()

            if msg.clickedButton() == settings_btn:
                self.navigate_to_settings.emit()
        else:
            logger.warning(f"Model not configured for role: {role}, skipping operation")

        return False

    def _generate_translation(self, post: PostDTO):
        """Start async post body translation via GenerationWorker."""
        if self._gen_worker is not None and self._gen_worker.isRunning():
            self._gen_worker.stop()
            self._gen_worker.wait(2000)

        if not self._check_model_configured("logic", show_dialog=True):
            return

        task_id = "reader_translation"
        self._translation_text.clear()
        self._translation_text.setPlaceholderText(self._i18n.get("reader.translating"))

        def do_start():
            self._start_loading_animation(self._translation_text)
            self.activity_started.emit(self._i18n.get("status.reader_translation"))
            self._gen_worker = GenerationWorker()
            self._gen_worker.token_received.connect(self._on_translation_token)
            self._gen_worker.finished_signal.connect(self._on_translation_finished)
            self._gen_worker.error_occurred.connect(self._on_translation_error)
            locale = self._config.get("app.locale", "ko_KR")
            self._gen_worker.configure(self._reader.generate_translation, post, locale=locale)
            self._gen_worker.start()

        if not self._coordinator.request_normal(task_id, do_start):
            return
        do_start()

    def _on_translation_token(self, token: str):
        """Append a streamed token to the translation text edit."""
        self._stop_loading_animation()
        self._translation_text.setPlaceholderText("")
        cursor = self._translation_text.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(token)

    def _on_translation_finished(self, full_text: str):
        """Replace streaming text with final complete translation."""
        self._coordinator.finish_normal("reader_translation")
        self._translation_text.setPlainText(full_text)
        self.activity_finished.emit(self._i18n.get("status.reader_translation"))

    def _on_translation_error(self, error_key: str):
        """Show localized error when translation fails."""
        self._coordinator.finish_normal("reader_translation")
        self._stop_loading_animation()
        self.activity_finished.emit(self._i18n.get("status.reader_translation"))
        self._translation_text.setPlaceholderText("")
        self._translation_text.setPlainText(self._i18n.get(error_key))

    def _on_refresh_translation(self):
        """Delete cached translation and regenerate."""
        if self._current_post is None:
            return
        locale = self._config.get("app.locale", "ko_KR")
        self._reader.delete_translation(self._current_post.id, locale=locale)
        self._generate_translation(self._current_post)

    def _toggle_original_translation(self):
        """Toggle between showing original text and translation."""
        self._showing_original = not self._showing_original
        if self._showing_original:
            self._original_text.show()
            self._toggle_btn.setText(self._i18n.get("reader.toggle_translation"))
        else:
            self._original_text.hide()
            self._toggle_btn.setText(self._i18n.get("reader.toggle_original"))

    # ------------------------------------------------------------------
    # Comment fetching and rendering
    # ------------------------------------------------------------------

    def _fetch_comments(self, post_id: str, subreddit: str):
        """Start async comment fetch via a dedicated RedditFetchWorker."""
        self._clear_comments()

        # Stop previous comment worker if still running
        if self._comment_worker is not None and self._comment_worker.isRunning():
            self._comment_worker.stop()
            self._comment_worker.wait(2000)

        self._comment_worker = RedditFetchWorker(self._reader)
        self._comment_worker.comments_ready.connect(self._on_comments_ready)
        self._comment_worker.error_occurred.connect(self._on_fetch_error)
        self._comment_worker.fetch_comments(post_id, subreddit)
        self._comment_worker.start()

    def _on_comments_ready(self, comments: list):
        """Store comments and render first batch with lazy loading."""
        self._clear_comments()
        self._comments_list = comments
        self._rendered_comment_count = 0
        self._translated_comment_count = 0
        # Render first batch
        self._render_next_batch()

    def _render_next_batch(self):
        """Render next COMMENTS_RENDER_BATCH top-level comments + auto-translate."""
        start = self._rendered_comment_count
        end = min(start + COMMENTS_RENDER_BATCH, len(self._comments_list))

        if start >= len(self._comments_list):
            return

        # Remove "..." indicator if it exists
        if hasattr(self, '_more_indicator') and self._more_indicator is not None:
            self._more_indicator.deleteLater()
            self._more_indicator = None

        for i in range(start, end):
            comment = self._comments_list[i]
            self._add_comment_widget(comment, self._comments_area, depth=0)

        self._rendered_comment_count = end

        # Add "..." indicator if more comments available
        if end < len(self._comments_list):
            self._more_indicator = QLabel("···")
            self._more_indicator.setStyleSheet("color: #888; font-size: 18px; padding: 8px;")
            self._more_indicator.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._comments_area.addWidget(self._more_indicator)
        else:
            self._more_indicator = None

        # Auto-translate the rendered batch
        locale = self._config.get("app.locale", "ko_KR")
        if locale == "ko_KR":
            self._translate_next_comments_batch()

    def _add_comment_widget(
        self,
        comment: CommentDTO,
        parent_layout: QVBoxLayout,
        depth: int,
    ):
        """Add a comment frame with optional reply button and child translate buttons.

        Rendering is capped at MAX_COMMENT_DEPTH (spec 7.2).

        Args:
            comment: CommentDTO to render.
            parent_layout: Layout to append the comment frame into.
            depth: Current nesting depth (0 = top-level).
        """
        if depth > MAX_COMMENT_DEPTH:
            return

        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(depth * 20, 4, 4, 4)

        self._comment_widgets[comment.id] = frame

        if comment.more_count > 0:
            more_label = QLabel(
                self._i18n.get("reader.more_comments", count=str(comment.more_count))
            )
            more_label.setStyleSheet("color: gray; font-style: italic;")
            more_label.setEnabled(False)
            frame_layout.addWidget(more_label)
        else:
            header_layout = QHBoxLayout()
            header = QLabel(f"<b>{comment.author}</b>  \u2191{comment.score}")
            header_layout.addWidget(header)
            header_layout.addStretch()

            # Reply button for all comments
            reply_btn = QPushButton(self._i18n.get("reader.write_reply"))
            reply_btn.setFixedHeight(24)
            reply_btn.setStyleSheet("font-size: 11px; padding: 2px 8px;")
            reply_btn.clicked.connect(lambda checked, c=comment: self._on_write_reply(c))
            header_layout.addWidget(reply_btn)

            # Translate button for child comments (depth > 0)
            if depth > 0:
                translate_btn = QPushButton(self._i18n.get("reader.translate_comment_btn"))
                translate_btn.setFixedHeight(24)
                translate_btn.setStyleSheet("font-size: 11px; padding: 2px 8px;")
                translate_btn.clicked.connect(
                    lambda checked, c=comment, btn=translate_btn: self._on_translate_single_comment(c, btn)
                )
                header_layout.addWidget(translate_btn)

            frame_layout.addLayout(header_layout)

            body = QLabel(comment.body)
            body.setWordWrap(True)
            frame_layout.addWidget(body)

        parent_layout.addWidget(frame)

        for child in comment.children:
            self._add_comment_widget(child, parent_layout, depth + 1)

    def _clear_comments(self):
        """Remove all dynamically-added comment widgets from the comments area."""
        while self._comments_area.count():
            item = self._comments_area.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._comment_widgets.clear()

    # ------------------------------------------------------------------
    # Comment translation
    # ------------------------------------------------------------------

    def _translate_next_comments_batch(self):
        """Translate the next batch of comments (coordinator-aware)."""
        # Silent skip if model not configured
        if not self._check_model_configured("logic", show_dialog=False):
            return

        # Collect untranslated top-level comment bodies
        start = self._translated_comment_count
        end = min(start + COMMENTS_RENDER_BATCH, len(self._comments_list))

        if start >= len(self._comments_list):
            return

        comments_to_translate = self._comments_list[start:end]
        bodies = [c.body for c in comments_to_translate if c.body and c.more_count == 0]

        if not bodies:
            self._translated_comment_count = end
            return

        task_id = "reader_comment_translate"

        def do_start():
            self.activity_started.emit(self._i18n.get("status.reader_comments"))

            # Batch translate using a combined prompt
            combined = "\n---\n".join(f"[{i+1}] {b}" for i, b in enumerate(bodies))
            prompt_text = (
                f"Translate each numbered Reddit comment below to Korean.\n"
                f"\n"
                f"Rules:\n"
                f"- Keep the same numbering [1] [2] [3]...\n"
                f"- Preserve tone and style\n"
                f"- Output ONLY the numbered translations\n"
                f"\n"
                f"{combined}"
            )

            if self._comment_translate_worker and self._comment_translate_worker.isRunning():
                self._comment_translate_worker.stop()
                self._comment_translate_worker.wait(2000)

            self._comment_translate_worker = GenerationWorker()
            self._comment_translate_worker.finished_signal.connect(
                lambda text: self._on_comments_translated(text, start, end)
            )
            self._comment_translate_worker.error_occurred.connect(self._on_comment_translate_error)

            self._comment_translate_worker.configure(
                self._reader._llm.generate,
                prompt=prompt_text,
                model=self._config.get("llm.models.logic.name", ""),
                num_ctx=8192,
            )
            self._comment_translate_worker.start()

        if not self._coordinator.request_normal(task_id, do_start):
            return  # queued
        do_start()

    def _on_comments_translated(self, full_text: str, start: int, end: int):
        """Parse translated comments and add translation labels to comment widgets."""
        self._coordinator.finish_normal("reader_comment_translate")
        self.activity_finished.emit(self._i18n.get("status.reader_comments"))
        self._translated_comment_count = end

        # Parse translations by [N] markers
        translations = {}
        current_idx = None
        current_text = []
        for line in full_text.strip().split("\n"):
            line = line.strip()
            # Check for [N] pattern
            if line.startswith("[") and "]" in line:
                bracket_end = line.index("]")
                try:
                    idx = int(line[1:bracket_end])
                    if current_idx is not None:
                        translations[current_idx] = " ".join(current_text).strip()
                    current_idx = idx
                    current_text = [line[bracket_end + 1:].strip()]
                except ValueError:
                    if current_idx is not None:
                        current_text.append(line)
            else:
                if current_idx is not None:
                    current_text.append(line)

        if current_idx is not None:
            translations[current_idx] = " ".join(current_text).strip()

        # Apply translations to comment widgets by comment ID
        body_idx = 0
        for i in range(start, end):
            if i >= len(self._comments_list):
                break
            comment = self._comments_list[i]
            if not comment.body or comment.more_count > 0:
                continue
            body_idx += 1
            translation = translations.get(body_idx, "")
            if translation:
                self._add_translation_to_comment(comment.id, translation)

    def _add_translation_to_comment(self, comment_id: str, translation: str):
        """Add a translation label below a comment widget."""
        frame = self._comment_widgets.get(comment_id)
        if frame is None:
            return
        frame_layout = frame.layout()
        if frame_layout:
            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.HLine)
            sep.setStyleSheet("color: #555;")
            frame_layout.addWidget(sep)

            trans_header = QLabel(self._i18n.get("reader.comment_translation"))
            trans_header.setStyleSheet("color: #4fc3f7; font-size: 11px; font-weight: bold;")
            frame_layout.addWidget(trans_header)

            trans_label = QLabel(translation)
            trans_label.setWordWrap(True)
            trans_label.setStyleSheet("color: #ccc;")
            frame_layout.addWidget(trans_label)

    def _on_comment_translate_error(self, error_key: str):
        """Comment translation failed - just log."""
        self._coordinator.finish_normal("reader_comment_translate")
        self.activity_finished.emit(self._i18n.get("status.reader_comments"))
        logger.warning(f"Comment translation failed: {error_key}")

    # ------------------------------------------------------------------
    # Single comment translation (on-demand for child comments)
    # ------------------------------------------------------------------

    def _on_translate_single_comment(self, comment: CommentDTO, btn: QPushButton):
        """Translate a single child comment on demand."""
        if not self._check_model_configured("logic", show_dialog=False):
            return
        btn.setEnabled(False)
        btn.setText("...")

        worker = GenerationWorker()
        locale = self._config.get("app.locale", "ko_KR")
        worker.configure(self._reader.translate_comment, comment.body, locale=locale)
        worker.finished_signal.connect(
            lambda text, cid=comment.id, b=btn: self._on_single_comment_translated(cid, text, b)
        )
        worker.error_occurred.connect(
            lambda err, b=btn: self._on_single_comment_translate_error(b)
        )
        worker.start()
        # Keep reference to prevent GC
        btn._translate_worker = worker

    def _on_single_comment_translated(self, comment_id: str, text: str, btn: QPushButton):
        """Handle single comment translation completion."""
        btn.hide()
        self._add_translation_to_comment(comment_id, text)

    def _on_single_comment_translate_error(self, btn: QPushButton):
        """Handle single comment translation error."""
        btn.setEnabled(True)
        btn.setText(self._i18n.get("reader.translate_comment_btn"))

    # ------------------------------------------------------------------
    # Write comment / reply handlers
    # ------------------------------------------------------------------

    def _on_write_comment(self):
        """Handle 'Write Comment' button click."""
        if self._current_post is None:
            return
        ctx = WriterContext(
            mode="comment",
            subreddit=self._current_post.subreddit,
            post_title=self._current_post.title,
            post_permalink=self._current_post.permalink,
            post_selftext=self._current_post.selftext,
        )
        self.write_requested.emit(ctx)

    def _on_write_reply(self, comment: CommentDTO):
        """Handle 'Reply' button click on a comment."""
        if self._current_post is None:
            return
        # Build parent thread (simplified - just this comment for now)
        parent_thread = [{
            "author": comment.author,
            "body": comment.body,
            "score": comment.score,
        }]
        ctx = WriterContext(
            mode="reply",
            subreddit=self._current_post.subreddit,
            post_title=self._current_post.title,
            post_permalink=self._current_post.permalink,
            post_selftext=self._current_post.selftext,
            comment_id=comment.id,
            comment_body=comment.body,
            comment_author=comment.author,
            parent_thread=parent_thread,
        )
        self.write_requested.emit(ctx)

    # ------------------------------------------------------------------
    # Scroll-based lazy loading
    # ------------------------------------------------------------------

    def _on_content_scroll(self, value: int):
        """Detect when user scrolls near bottom and render+translate more comments."""
        scrollbar = self._content_scroll.verticalScrollBar()
        if scrollbar.maximum() == 0:
            return
        if value > scrollbar.maximum() * 0.8:
            if self._rendered_comment_count < len(self._comments_list):
                if not (self._comment_translate_worker and self._comment_translate_worker.isRunning()):
                    self._render_next_batch()

    # ------------------------------------------------------------------
    # Loading animation
    # ------------------------------------------------------------------

    def _start_loading_animation(self, target: QTextEdit):
        """Start animated loading text in a QTextEdit."""
        self._anim_target = target
        self._anim_dot_count = 0
        self._anim_timer.start()
        self._animate_loading()

    def _stop_loading_animation(self):
        """Stop the loading animation."""
        self._anim_timer.stop()
        self._anim_target = None

    def _animate_loading(self):
        """Update animated dots."""
        if self._anim_target is None:
            return
        self._anim_dot_count = (self._anim_dot_count + 1) % 4
        dots = "." * self._anim_dot_count
        base = self._i18n.get("reader.translating").rstrip(".")
        self._anim_target.setPlaceholderText(f"{base}{dots}")

    # ------------------------------------------------------------------
    # i18n hot-reload
    # ------------------------------------------------------------------

    def retranslate_ui(self):
        """Update all visible text for a locale change.

        Called by MainWindow.retranslate_ui() after I18nManager.load_locale().
        """
        self._posts_label.setText(self._i18n.get("reader.posts"))
        self._translation_label.setText(self._i18n.get("reader.translation"))
        self._comments_label.setText(self._i18n.get("reader.comments"))
        self._refresh_btn.setText(self._i18n.get("reader.refresh"))
        self._toggle_btn.setText(
            self._i18n.get("reader.toggle_translation") if self._showing_original
            else self._i18n.get("reader.toggle_original")
        )
        self._write_comment_btn.setText(self._i18n.get("reader.write_comment"))
