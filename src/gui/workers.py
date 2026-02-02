"""QThread workers for background operations."""

import logging
from typing import Optional, Callable

from PyQt6.QtCore import QThread, pyqtSignal

from src.core.exceptions import ReddiScribeError
from src.core.types import PostDTO, CommentDTO

logger = logging.getLogger("reddiscribe")


class RedditFetchWorker(QThread):
    """Background worker for fetching Reddit data.

    Used for both post fetching and comment fetching.
    Emits signals to main thread - UI never directly calls service methods.
    """
    posts_ready = pyqtSignal(list)       # list[PostDTO]
    comments_ready = pyqtSignal(list)    # list[CommentDTO]
    error_occurred = pyqtSignal(str)     # i18n error key
    progress = pyqtSignal(str)           # status message

    def __init__(self, reader_service, parent=None):
        """Initialize the Reddit fetch worker.

        Args:
            reader_service: ReaderService instance (type hint omitted to avoid circular import)
            parent: Optional parent QObject
        """
        super().__init__(parent)
        self._reader = reader_service
        self._task: Optional[str] = None  # "posts" or "comments"
        self._subreddit: str = ""
        self._post_id: str = ""
        self._sort: str = "hot"
        self._limit: int = 25
        self._time_filter: Optional[str] = None
        self._stopped = False

    def fetch_posts(self, subreddit: str, sort: str = "hot",
                    limit: int = 25, time_filter: Optional[str] = None):
        """Configure worker to fetch posts, then call start()."""
        self._task = "posts"
        self._subreddit = subreddit
        self._sort = sort
        self._limit = limit
        self._time_filter = time_filter
        self._stopped = False

    def fetch_comments(self, post_id: str, subreddit: str,
                       sort: str = "top", limit: int = 50):
        """Configure worker to fetch comments, then call start()."""
        self._task = "comments"
        self._post_id = post_id
        self._subreddit = subreddit
        self._sort = sort
        self._limit = limit
        self._stopped = False

    def stop(self):
        """Request the worker to stop."""
        self._stopped = True

    def run(self):
        """Execute the configured fetch task."""
        try:
            if self._task == "posts":
                self.progress.emit("loading")
                posts = self._reader.fetch_posts(
                    self._subreddit, self._sort, self._limit, self._time_filter
                )
                if not self._stopped:
                    self.posts_ready.emit(posts)
            elif self._task == "comments":
                self.progress.emit("loading")
                comments = self._reader.fetch_comments(
                    self._post_id, self._subreddit, self._sort, self._limit
                )
                if not self._stopped:
                    self.comments_ready.emit(comments)
        except ReddiScribeError as e:
            if not self._stopped:
                # Map exception to i18n error key
                error_key = self._map_error_to_i18n_key(e)
                self.error_occurred.emit(error_key)
                logger.error(f"Fetch error: {e}")
        except Exception as e:
            if not self._stopped:
                self.error_occurred.emit("errors.reddit_fetch_failed")
                logger.error(f"Unexpected fetch error: {e}")

    @staticmethod
    def _map_error_to_i18n_key(error: ReddiScribeError) -> str:
        """Map exception type to i18n error key."""
        from src.core.exceptions import (
            RateLimitError, SubredditNotFoundError, SubredditPrivateError
        )
        if isinstance(error, RateLimitError):
            return "errors.rate_limited"
        if isinstance(error, SubredditNotFoundError):
            return "errors.subreddit_not_found"
        if isinstance(error, SubredditPrivateError):
            return "errors.subreddit_private"
        return "errors.reddit_fetch_failed"


class GenerationWorker(QThread):
    """Background worker for LLM text generation (streaming).

    Used for:
    - Summary generation (ReaderService)
    - Draft generation - Writer Stage 1 (WriterService)
    - Polish generation - Writer Stage 2 (WriterService)
    """
    token_received = pyqtSignal(str)     # individual token for streaming display
    finished_signal = pyqtSignal(str)    # complete text when done
    error_occurred = pyqtSignal(str)     # i18n error key

    def __init__(self, parent=None):
        super().__init__(parent)
        self._generator: Optional[Callable] = None
        self._generator_args: tuple = ()
        self._generator_kwargs: dict = {}
        self._stopped = False

    def configure(self, generator_func: Callable, *args, **kwargs):
        """Configure the generator function to run.

        Args:
            generator_func: A method that returns Iterator[str]
                e.g., reader_service.generate_summary or writer_service.draft
            *args, **kwargs: Arguments to pass to the generator function
        """
        self._generator = generator_func
        self._generator_args = args
        self._generator_kwargs = kwargs
        self._stopped = False

    def stop(self):
        """Request the worker to stop."""
        self._stopped = True

    def run(self):
        """Execute the generator and emit tokens."""
        if self._generator is None:
            self.error_occurred.emit("errors.llm_timeout")
            return

        full_text = ""
        try:
            for token in self._generator(*self._generator_args, **self._generator_kwargs):
                if self._stopped:
                    logger.info("Generation stopped by user")
                    return  # Don't emit finished_signal on stop
                full_text += token
                self.token_received.emit(token)

            if not self._stopped:
                self.finished_signal.emit(full_text)

        except ReddiScribeError as e:
            if not self._stopped:
                error_key = self._map_error_to_i18n_key(e)
                self.error_occurred.emit(error_key)
                logger.error(f"Generation error: {e}")
        except Exception as e:
            if not self._stopped:
                self.error_occurred.emit("errors.llm_timeout")
                logger.error(f"Unexpected generation error: {e}")

    @staticmethod
    def _map_error_to_i18n_key(error: ReddiScribeError) -> str:
        """Map exception type to i18n error key."""
        from src.core.exceptions import (
            OllamaNotRunningError, ModelNotFoundError, LLMTimeoutError
        )
        if isinstance(error, OllamaNotRunningError):
            return "errors.ollama_not_running"
        if isinstance(error, ModelNotFoundError):
            return "errors.model_not_found"
        if isinstance(error, LLMTimeoutError):
            return "errors.llm_timeout"
        return "errors.ollama_not_running"
