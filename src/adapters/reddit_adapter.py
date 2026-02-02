"""Abstract base class for Reddit data access."""

from abc import ABC, abstractmethod
from typing import Optional

from src.core.types import PostDTO, CommentDTO


class RedditAdapter(ABC):
    """Abstract interface for fetching Reddit data."""

    @abstractmethod
    def get_subreddit_posts(
        self,
        subreddit: str,
        sort: str = "hot",
        limit: int = 25,
        time_filter: Optional[str] = None,
    ) -> list[PostDTO]:
        """Fetch posts from a subreddit.

        Args:
            subreddit: Subreddit name (without r/ prefix)
            sort: Sort method - "hot", "new", "top", "rising"
            limit: Number of posts (1-100)
            time_filter: Time filter for "top" sort - "hour", "day", "week", "month", "year", "all"

        Returns:
            List of PostDTO

        Raises:
            RedditFetchError: General fetch failure
            RateLimitError: 429 Too Many Requests
            SubredditNotFoundError: 404 Not Found
            SubredditPrivateError: 403 Forbidden
        """
        ...

    @abstractmethod
    def get_post_comments(
        self,
        post_id: str,
        subreddit: str,
        sort: str = "top",
        limit: int = 50,
    ) -> list[CommentDTO]:
        """Fetch comments for a post.

        Args:
            post_id: Reddit post ID (e.g., "8xwlg")
            subreddit: Subreddit name
            sort: Comment sort - "best", "top", "new", "controversial"
            limit: Number of top-level comments

        Returns:
            List of CommentDTO (with nested children)

        Raises:
            RedditFetchError: General fetch failure
            RateLimitError: 429 Too Many Requests
        """
        ...
