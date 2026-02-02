"""Thread-safe singleton DatabaseManager for SQLite operations."""

import logging
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from src.core.exceptions import DatabaseError
from src.core.types import PostDTO, SummaryDTO

logger = logging.getLogger("reddiscribe")


class DatabaseManager:
    """Thread-safe singleton DatabaseManager for SQLite operations.

    Manages a single SQLite connection with proper thread synchronization.
    All public methods are protected with RLock for thread safety.
    """

    _instance: Optional['DatabaseManager'] = None
    _lock = threading.RLock()

    def __new__(cls, db_path: Optional[Path] = None):
        """Ensure only one instance exists (Singleton pattern)."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, db_path: Optional[Path] = None):
        """Initialize DatabaseManager with SQLite connection.

        Args:
            db_path: Absolute path to SQLite database file.
                     Only used on first initialization.
        """
        if self._initialized:
            return

        if db_path is None:
            raise DatabaseError("db_path is required for first initialization")

        try:
            # Ensure parent directory exists
            db_path.parent.mkdir(parents=True, exist_ok=True)

            # Open SQLite connection
            # check_same_thread=False allows the connection to be used from
            # different threads, but we enforce thread safety with RLock
            self._conn = sqlite3.connect(
                str(db_path),
                check_same_thread=False
            )
            self._conn.row_factory = sqlite3.Row

            # Initialize schema
            self._init_schema()

            self._initialized = True
            logger.info(f"DatabaseManager initialized with db_path: {db_path}")

        except Exception as e:
            raise DatabaseError(f"Failed to initialize database: {e}")

    def _init_schema(self) -> None:
        """Create database tables if they don't exist."""
        try:
            with self._lock:
                # Create posts table
                self._conn.execute("""
                    CREATE TABLE IF NOT EXISTS posts (
                        id            TEXT PRIMARY KEY,
                        subreddit     TEXT NOT NULL,
                        title         TEXT NOT NULL,
                        selftext      TEXT DEFAULT '',
                        author        TEXT DEFAULT '[deleted]',
                        url           TEXT,
                        permalink     TEXT,
                        score         INTEGER DEFAULT 0,
                        num_comments  INTEGER DEFAULT 0,
                        created_utc   REAL,
                        fetched_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # Create summaries table
                self._conn.execute("""
                    CREATE TABLE IF NOT EXISTS summaries (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        post_id     TEXT NOT NULL,
                        model_type  TEXT NOT NULL,
                        summary     TEXT NOT NULL,
                        locale      TEXT NOT NULL DEFAULT 'ko_KR',
                        created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (post_id) REFERENCES posts(id),
                        UNIQUE(post_id, model_type, locale)
                    )
                """)

                self._conn.commit()
                logger.debug("Database schema initialized")

        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to initialize schema: {e}")

    def save_post(self, post: PostDTO) -> None:
        """Save a post to the database (INSERT OR IGNORE).

        Args:
            post: PostDTO containing post data.

        Raises:
            DatabaseError: If database operation fails.
        """
        try:
            with self._lock:
                self._conn.execute(
                    """
                    INSERT OR IGNORE INTO posts (
                        id, subreddit, title, selftext, author,
                        url, permalink, score, num_comments, created_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        post.id,
                        post.subreddit,
                        post.title,
                        post.selftext,
                        post.author,
                        post.url,
                        post.permalink,
                        post.score,
                        post.num_comments,
                        post.created_utc,
                    )
                )
                self._conn.commit()
                logger.debug(f"Saved post: {post.id}")

        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to save post {post.id}: {e}")

    def save_summary(self, summary: SummaryDTO) -> None:
        """Save or update a summary (UPSERT).

        Args:
            summary: SummaryDTO containing summary data.

        Raises:
            DatabaseError: If database operation fails.
        """
        try:
            with self._lock:
                self._conn.execute(
                    """
                    INSERT INTO summaries (post_id, model_type, summary, locale)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(post_id, model_type, locale)
                    DO UPDATE SET
                        summary = excluded.summary,
                        created_at = CURRENT_TIMESTAMP
                    """,
                    (
                        summary.post_id,
                        summary.model_type,
                        summary.text,
                        summary.locale,
                    )
                )
                self._conn.commit()
                logger.debug(
                    f"Saved summary for post {summary.post_id} "
                    f"(model: {summary.model_type}, locale: {summary.locale})"
                )

        except sqlite3.Error as e:
            raise DatabaseError(
                f"Failed to save summary for post {summary.post_id}: {e}"
            )

    def get_summary(
        self,
        post_id: str,
        model_type: str = "summary",
        locale: str = "ko_KR"
    ) -> Optional[str]:
        """Get cached summary text.

        Args:
            post_id: Reddit post ID.
            model_type: Type of summary ('summary', 'logic', 'persona').
            locale: Locale code (default: 'ko_KR').

        Returns:
            Summary text if found, None otherwise.

        Raises:
            DatabaseError: If database operation fails.
        """
        try:
            with self._lock:
                cursor = self._conn.execute(
                    """
                    SELECT summary FROM summaries
                    WHERE post_id = ? AND model_type = ? AND locale = ?
                    """,
                    (post_id, model_type, locale)
                )
                row = cursor.fetchone()

                if row:
                    logger.debug(
                        f"Retrieved summary for post {post_id} "
                        f"(model: {model_type}, locale: {locale})"
                    )
                    return row['summary']

                logger.debug(
                    f"No summary found for post {post_id} "
                    f"(model: {model_type}, locale: {locale})"
                )
                return None

        except sqlite3.Error as e:
            raise DatabaseError(
                f"Failed to retrieve summary for post {post_id}: {e}"
            )

    def delete_summary(
        self,
        post_id: str,
        model_type: str = "summary",
        locale: str = "ko_KR"
    ) -> None:
        """Delete a cached summary (for refresh).

        Args:
            post_id: Reddit post ID.
            model_type: Type of summary ('summary', 'logic', 'persona').
            locale: Locale code (default: 'ko_KR').

        Raises:
            DatabaseError: If database operation fails.
        """
        try:
            with self._lock:
                self._conn.execute(
                    """
                    DELETE FROM summaries
                    WHERE post_id = ? AND model_type = ? AND locale = ?
                    """,
                    (post_id, model_type, locale)
                )
                self._conn.commit()
                logger.debug(
                    f"Deleted summary for post {post_id} "
                    f"(model: {model_type}, locale: {locale})"
                )

        except sqlite3.Error as e:
            raise DatabaseError(
                f"Failed to delete summary for post {post_id}: {e}"
            )

    def close(self) -> None:
        """Close the database connection."""
        try:
            with self._lock:
                if hasattr(self, '_conn') and self._conn:
                    self._conn.close()
                    logger.info("Database connection closed")

        except sqlite3.Error as e:
            logger.error(f"Error closing database connection: {e}")

    @classmethod
    def reset(cls) -> None:
        """Reset singleton instance (for testing).

        Closes the connection if open and clears the singleton instance.
        """
        with cls._lock:
            if cls._instance is not None:
                if hasattr(cls._instance, '_conn') and cls._instance._conn:
                    try:
                        cls._instance._conn.close()
                        logger.debug("Connection closed during reset")
                    except sqlite3.Error as e:
                        logger.error(f"Error closing connection during reset: {e}")

                cls._instance = None
                logger.debug("DatabaseManager singleton reset")
