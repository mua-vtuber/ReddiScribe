"""Reader widget for browsing Reddit posts with AI summaries."""

import logging
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QPushButton, QLabel,
    QTextEdit, QComboBox, QScrollArea,
    QFrame, QInputDialog,
)
from PyQt6.QtCore import Qt

from src.core.i18n_manager import I18nManager
from src.core.types import PostDTO, CommentDTO
from src.gui.workers import RedditFetchWorker, GenerationWorker
from src.services.reader_service import ReaderService

logger = logging.getLogger("reddiscribe")

# Client-side rendering depth limit (spec 7.2)
MAX_COMMENT_DEPTH = 5


class ReaderWidget(QWidget):
    """Reader tab - browse subreddits, read posts, get AI summaries.

    Layout (spec Section 7.2):
        Left panel:  subreddit list from config + add/remove buttons
        Right panel: post list with sort selector, summary/original panes, comment tree
    """

    def __init__(self, reader_service: ReaderService, config, parent=None):
        """Initialize the reader widget.

        Args:
            reader_service: ReaderService instance for data fetching and summarization.
            config: ConfigManager instance for persisting subreddit list and reading locale.
            parent: Optional parent QWidget.
        """
        super().__init__(parent)
        self._reader = reader_service
        self._config = config
        self._i18n = I18nManager()
        self._current_posts: list[PostDTO] = []
        self._current_post: Optional[PostDTO] = None

        # Workers (kept as instance attrs to prevent GC and allow stop)
        self._fetch_worker: Optional[RedditFetchWorker] = None
        self._comment_worker: Optional[RedditFetchWorker] = None
        self._gen_worker: Optional[GenerationWorker] = None

        self._init_ui()
        self._load_subreddits()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _init_ui(self):
        """Build the full reader layout."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # === Left panel: subreddit list ===
        left_panel = self._build_left_panel()
        splitter.addWidget(left_panel)

        # === Right panel: posts + content + comments ===
        right_panel = self._build_right_panel()
        splitter.addWidget(right_panel)

        splitter.setStretchFactor(0, 1)   # left panel smaller
        splitter.setStretchFactor(1, 3)   # right panel larger

        layout.addWidget(splitter)

    def _build_left_panel(self) -> QWidget:
        """Build the subreddit list panel with add/remove buttons."""
        panel = QWidget()
        panel_layout = QVBoxLayout(panel)

        self._sub_label = QLabel(self._i18n.get("reader.subreddits"))
        panel_layout.addWidget(self._sub_label)

        self._sub_list = QListWidget()
        self._sub_list.currentItemChanged.connect(self._on_subreddit_selected)
        panel_layout.addWidget(self._sub_list)

        btn_layout = QHBoxLayout()
        self._add_btn = QPushButton(self._i18n.get("reader.add_sub"))
        self._add_btn.clicked.connect(self._on_add_subreddit)
        self._remove_btn = QPushButton(self._i18n.get("reader.remove_sub"))
        self._remove_btn.clicked.connect(self._on_remove_subreddit)
        btn_layout.addWidget(self._add_btn)
        btn_layout.addWidget(self._remove_btn)
        panel_layout.addLayout(btn_layout)

        panel.setMaximumWidth(200)
        return panel

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

        # -- Scrollable content: summary + original + comments --
        content_area = QScrollArea()
        content_area.setWidgetResizable(True)
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)

        # Summary section
        self._summary_label = QLabel(self._i18n.get("reader.summary"))
        self._summary_label.setStyleSheet("font-weight: bold;")
        content_layout.addWidget(self._summary_label)

        self._summary_text = QTextEdit()
        self._summary_text.setReadOnly(True)
        self._summary_text.setMaximumHeight(150)
        self._summary_text.setPlaceholderText("")
        content_layout.addWidget(self._summary_text)

        # Separator
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        content_layout.addWidget(sep1)

        # Original section
        self._original_label = QLabel(self._i18n.get("reader.original"))
        self._original_label.setStyleSheet("font-weight: bold;")
        content_layout.addWidget(self._original_label)

        self._original_text = QTextEdit()
        self._original_text.setReadOnly(True)
        self._original_text.setMaximumHeight(150)
        content_layout.addWidget(self._original_text)

        # Separator
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        content_layout.addWidget(sep2)

        # Comments header with refresh button
        comments_header = QHBoxLayout()
        self._comments_label = QLabel(self._i18n.get("reader.comments"))
        self._comments_label.setStyleSheet("font-weight: bold;")
        comments_header.addWidget(self._comments_label)
        comments_header.addStretch()

        self._refresh_btn = QPushButton(self._i18n.get("reader.refresh"))
        self._refresh_btn.clicked.connect(self._on_refresh_summary)
        comments_header.addWidget(self._refresh_btn)
        content_layout.addLayout(comments_header)

        # Container layout for dynamically-added comment widgets
        self._comments_area = QVBoxLayout()
        content_layout.addLayout(self._comments_area)
        content_layout.addStretch()

        content_area.setWidget(content_widget)
        panel_layout.addWidget(content_area)

        return panel

    # ------------------------------------------------------------------
    # Subreddit management
    # ------------------------------------------------------------------

    def _load_subreddits(self):
        """Load subreddit list from config into the list widget."""
        subs = self._config.get(
            "reddit.subreddits",
            ["python", "programming", "learnpython"],
        )
        self._sub_list.clear()
        for sub in subs:
            self._sub_list.addItem(sub)

    def _save_subreddits(self):
        """Persist the current subreddit list back to config."""
        subs = [
            self._sub_list.item(i).text()
            for i in range(self._sub_list.count())
        ]
        self._config.set("reddit.subreddits", subs)
        self._config.save()

    def _on_add_subreddit(self):
        """Prompt user for a new subreddit name, validate, and add."""
        text, ok = QInputDialog.getText(
            self,
            self._i18n.get("reader.add_sub"),
            self._i18n.get("reader.subreddits") + ":",
        )
        if not ok or not text.strip():
            return

        name = text.strip().lower()

        # Reject duplicates
        for i in range(self._sub_list.count()):
            if self._sub_list.item(i).text() == name:
                return

        self._sub_list.addItem(name)
        self._save_subreddits()

    def _on_remove_subreddit(self):
        """Remove the currently-selected subreddit."""
        current = self._sub_list.currentItem()
        if current is None:
            return
        row = self._sub_list.row(current)
        self._sub_list.takeItem(row)
        self._save_subreddits()

    # ------------------------------------------------------------------
    # Post fetching
    # ------------------------------------------------------------------

    def _on_subreddit_selected(self, current: QListWidgetItem, _previous):
        """Handle subreddit selection change."""
        if current is None:
            return
        self._fetch_posts(current.text())

    def _on_sort_changed(self, _sort_text: str):
        """Re-fetch posts when sort order changes."""
        current = self._sub_list.currentItem()
        if current is not None:
            self._fetch_posts(current.text())

    def _fetch_posts(self, subreddit: str):
        """Start async post fetch via RedditFetchWorker.

        Clears existing UI state immediately and shows loading indicator.
        """
        # Clear right panel state
        self._post_list.clear()
        self._summary_text.clear()
        self._original_text.clear()
        self._clear_comments()
        self._current_posts = []
        self._current_post = None

        # Stop any running fetch
        if self._fetch_worker is not None and self._fetch_worker.isRunning():
            self._fetch_worker.stop()
            self._fetch_worker.wait(2000)

        self._fetch_worker = RedditFetchWorker(self._reader)
        self._fetch_worker.posts_ready.connect(self._on_posts_ready)
        self._fetch_worker.error_occurred.connect(self._on_fetch_error)

        sort = self._sort_combo.currentText().lower()
        self._fetch_worker.fetch_posts(subreddit, sort=sort)
        self._fetch_worker.start()

        self._posts_label.setText(self._i18n.get("reader.loading"))

    def _on_posts_ready(self, posts: list):
        """Populate post list when async fetch completes."""
        self._current_posts = posts
        self._post_list.clear()
        self._posts_label.setText(self._i18n.get("reader.posts"))

        if not posts:
            self._posts_label.setText(self._i18n.get("reader.no_posts"))
            return

        for post in posts:
            item_text = f"{post.title}  [\u2191{post.score}]  [\U0001f4ac{post.num_comments}]"
            self._post_list.addItem(item_text)

    def _on_fetch_error(self, error_key: str):
        """Show localized error in the posts label."""
        self._posts_label.setText(self._i18n.get(error_key))

    # ------------------------------------------------------------------
    # Post selection -> summary + original + comments
    # ------------------------------------------------------------------

    def _on_post_selected(self, row: int):
        """Handle post list selection. Loads original, summary, and comments."""
        if row < 0 or row >= len(self._current_posts):
            return

        post = self._current_posts[row]
        self._current_post = post

        # Original body
        self._original_text.setPlainText(post.selftext or "")

        # Summary: check cache first
        locale = self._config.get("app.locale", "ko_KR")
        cached = self._reader.get_summary(post.id, locale=locale)
        if cached:
            self._summary_text.setPlainText(cached)
        else:
            self._generate_summary(post)

        # Comments (separate async request)
        self._fetch_comments(post.id, post.subreddit)

    # ------------------------------------------------------------------
    # Summary generation (streaming)
    # ------------------------------------------------------------------

    def _generate_summary(self, post: PostDTO):
        """Start async summary generation via GenerationWorker."""
        # Stop any running generation
        if self._gen_worker is not None and self._gen_worker.isRunning():
            self._gen_worker.stop()
            self._gen_worker.wait(2000)

        self._summary_text.clear()
        self._summary_text.setPlaceholderText(self._i18n.get("reader.generating"))

        self._gen_worker = GenerationWorker()
        self._gen_worker.token_received.connect(self._on_summary_token)
        self._gen_worker.finished_signal.connect(self._on_summary_finished)
        self._gen_worker.error_occurred.connect(self._on_gen_error)

        locale = self._config.get("app.locale", "ko_KR")
        self._gen_worker.configure(self._reader.generate_summary, post, locale=locale)
        self._gen_worker.start()

    def _on_summary_token(self, token: str):
        """Append a streamed token to the summary text edit."""
        self._summary_text.setPlaceholderText("")
        cursor = self._summary_text.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(token)

    def _on_summary_finished(self, full_text: str):
        """Replace streaming text with final complete summary."""
        self._summary_text.setPlainText(full_text)

    def _on_gen_error(self, error_key: str):
        """Show localized error when summary generation fails."""
        self._summary_text.setPlaceholderText("")
        self._summary_text.setPlainText(self._i18n.get(error_key))

    def _on_refresh_summary(self):
        """Delete cached summary and regenerate."""
        if self._current_post is None:
            return
        locale = self._config.get("app.locale", "ko_KR")
        self._reader.delete_summary(self._current_post.id, locale=locale)
        self._generate_summary(self._current_post)

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
        """Render the comment tree when async fetch completes."""
        self._clear_comments()
        for comment in comments:
            self._add_comment_widget(comment, self._comments_area, depth=0)

    def _add_comment_widget(
        self,
        comment: CommentDTO,
        parent_layout: QVBoxLayout,
        depth: int,
    ):
        """Recursively add a comment frame with indentation.

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

        if comment.more_count > 0:
            # "N more comments" placeholder (non-interactive in v1.0)
            more_label = QLabel(
                self._i18n.get("reader.more_comments", count=str(comment.more_count))
            )
            more_label.setStyleSheet("color: gray; font-style: italic;")
            more_label.setEnabled(False)
            frame_layout.addWidget(more_label)
        else:
            header = QLabel(f"<b>{comment.author}</b>  \u2191{comment.score}")
            frame_layout.addWidget(header)

            body = QLabel(comment.body)
            body.setWordWrap(True)
            frame_layout.addWidget(body)

        parent_layout.addWidget(frame)

        # Recurse into child comments
        for child in comment.children:
            self._add_comment_widget(child, parent_layout, depth + 1)

    def _clear_comments(self):
        """Remove all dynamically-added comment widgets from the comments area."""
        while self._comments_area.count():
            item = self._comments_area.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    # ------------------------------------------------------------------
    # i18n hot-reload
    # ------------------------------------------------------------------

    def retranslate_ui(self):
        """Update all visible text for a locale change.

        Called by MainWindow.retranslate_ui() after I18nManager.load_locale().
        """
        self._sub_label.setText(self._i18n.get("reader.subreddits"))
        self._posts_label.setText(self._i18n.get("reader.posts"))
        self._summary_label.setText(self._i18n.get("reader.summary"))
        self._original_label.setText(self._i18n.get("reader.original"))
        self._comments_label.setText(self._i18n.get("reader.comments"))
        self._refresh_btn.setText(self._i18n.get("reader.refresh"))
        self._add_btn.setText(self._i18n.get("reader.add_sub"))
        self._remove_btn.setText(self._i18n.get("reader.remove_sub"))
