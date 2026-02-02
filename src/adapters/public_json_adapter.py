"""Reddit public JSON endpoint adapter with stealth headers and rate limiting."""

import logging
import time
from typing import Optional

import requests

from src.adapters.reddit_adapter import RedditAdapter
from src.core.exceptions import (
    RedditFetchError,
    RateLimitError,
    SubredditNotFoundError,
    SubredditPrivateError,
)
from src.core.types import PostDTO, CommentDTO


logger = logging.getLogger("reddiscribe")

# App version for User-Agent
_APP_VERSION = "1.0.0"


class RateLimiter:
    """Simple minimum-interval rate limiter.

    Enforces a minimum time gap between requests.
    On 429, uses exponential backoff.
    """

    def __init__(self, interval_sec: float = 6.0, max_retries: int = 3):
        self._interval = interval_sec
        self._max_retries = max_retries
        self._last_request_time: float = 0.0

    def wait(self) -> None:
        """Wait if needed to respect minimum interval."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self._interval:
            sleep_time = self._interval - elapsed
            logger.debug(f"Rate limiter: sleeping {sleep_time:.1f}s")
            time.sleep(sleep_time)

    def mark_request(self) -> None:
        """Record that a request was just made."""
        self._last_request_time = time.time()

    @property
    def max_retries(self) -> int:
        return self._max_retries

    def get_backoff_time(self, attempt: int) -> float:
        """Exponential backoff: interval * 2^attempt.
        E.g., 6s -> 12s -> 24s -> 48s"""
        return self._interval * (2 ** attempt)


class PublicJSONAdapter(RedditAdapter):
    """Fetches Reddit data via public JSON endpoints (no API key needed)."""

    BASE_URL = "https://www.reddit.com"

    def __init__(
        self,
        request_interval_sec: float = 6.0,
        max_retries: int = 3,
        mock_mode: bool = False,
    ):
        self._mock_mode = mock_mode
        self._rate_limiter = RateLimiter(request_interval_sec, max_retries)
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": f"desktop:kr.reddiscribe:v{_APP_VERSION} (by /u/ReddiScribeApp)",
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
        })

    def get_subreddit_posts(
        self,
        subreddit: str,
        sort: str = "hot",
        limit: int = 25,
        time_filter: Optional[str] = None,
    ) -> list[PostDTO]:
        if self._mock_mode:
            return self._mock_posts(subreddit)

        params = {"limit": limit, "raw_json": 1}
        if sort == "top" and time_filter:
            params["t"] = time_filter

        url = f"{self.BASE_URL}/r/{subreddit}/{sort}.json"
        data = self._fetch_json(url, params)

        posts = []
        for child in data.get("data", {}).get("children", []):
            if child.get("kind") != "t3":
                continue
            d = child["data"]
            posts.append(PostDTO(
                id=d["id"],
                title=d["title"],
                selftext=d.get("selftext", ""),
                author=d.get("author", "[deleted]"),
                subreddit=d.get("subreddit", subreddit),
                score=d.get("score", 0),
                num_comments=d.get("num_comments", 0),
                url=d.get("url", ""),
                permalink=d.get("permalink", ""),
                created_utc=d.get("created_utc", 0.0),
                is_self=d.get("is_self", True),
            ))
        return posts

    def get_post_comments(
        self,
        post_id: str,
        subreddit: str,
        sort: str = "top",
        limit: int = 50,
    ) -> list[CommentDTO]:
        if self._mock_mode:
            return self._mock_comments()

        url = f"{self.BASE_URL}/r/{subreddit}/comments/{post_id}/.json"
        params = {"raw_json": 1, "sort": sort, "limit": limit}
        data = self._fetch_json(url, params)

        # Response is a list of 2 Listings
        # [0] = post, [1] = comments
        if not isinstance(data, list) or len(data) < 2:
            raise RedditFetchError("Unexpected comment response format")

        comments = []
        for child in data[1].get("data", {}).get("children", []):
            parsed = self._parse_comment(child)
            if parsed:
                comments.append(parsed)
        return comments

    def _fetch_json(self, url: str, params: dict) -> dict | list:
        """Fetch JSON from Reddit with rate limiting and error handling.

        Handles: rate limiting, 429 backoff, HTML response detection,
        HTTP error codes (403, 404).
        """
        self._rate_limiter.wait()

        last_error = None
        for attempt in range(self._rate_limiter.max_retries + 1):
            try:
                self._rate_limiter.mark_request()
                response = self._session.get(url, params=params, timeout=30)

                # Check for 429 Too Many Requests
                if response.status_code == 429:
                    if attempt < self._rate_limiter.max_retries:
                        backoff = self._rate_limiter.get_backoff_time(attempt)
                        logger.warning(f"Rate limited (429). Backoff: {backoff}s (attempt {attempt + 1})")
                        time.sleep(backoff)
                        continue
                    raise RateLimitError("Rate limit exceeded after max retries")

                # Check for HTTP errors
                if response.status_code == 404:
                    raise SubredditNotFoundError(f"Not found: {url}")
                if response.status_code == 403:
                    raise SubredditPrivateError(f"Forbidden: {url}")
                response.raise_for_status()

                # Check for HTML response (bot detection)
                content_type = response.headers.get("Content-Type", "")
                if "json" not in content_type and "text/html" in content_type:
                    if attempt < self._rate_limiter.max_retries:
                        logger.warning("Received HTML instead of JSON (bot detection). Waiting 30s...")
                        time.sleep(30)
                        continue
                    raise RedditFetchError("Reddit returned HTML instead of JSON")

                return response.json()

            except (RateLimitError, SubredditNotFoundError, SubredditPrivateError):
                raise
            except requests.RequestException as e:
                last_error = e
                if attempt < self._rate_limiter.max_retries:
                    backoff = self._rate_limiter.get_backoff_time(attempt)
                    logger.warning(f"Request failed: {e}. Retrying in {backoff}s")
                    time.sleep(backoff)
                    continue

        raise RedditFetchError(f"Failed to fetch data: {last_error}")

    @staticmethod
    def _parse_comment(item: dict, max_depth: int = 5) -> Optional[CommentDTO]:
        """Recursively parse a comment JSON object into CommentDTO."""
        if item.get("kind") == "more":
            return CommentDTO(
                id=item["data"].get("id", ""),
                more_count=item["data"].get("count", 0),
                depth=item["data"].get("depth", 0),
            )
        if item.get("kind") != "t1":
            return None

        d = item["data"]
        children = []
        replies = d.get("replies")

        # replies is empty string "" when no children, dict when children exist
        if isinstance(replies, dict) and d.get("depth", 0) < max_depth:
            for child in replies.get("data", {}).get("children", []):
                parsed = PublicJSONAdapter._parse_comment(child, max_depth)
                if parsed:
                    children.append(parsed)

        return CommentDTO(
            id=d["id"],
            author=d.get("author", "[deleted]"),
            body=d.get("body", ""),
            score=d.get("score", 0),
            created_utc=d.get("created_utc", 0.0),
            depth=d.get("depth", 0),
            parent_id=d.get("parent_id", ""),
            children=children,
        )

    @staticmethod
    def _mock_posts(subreddit: str) -> list[PostDTO]:
        """Return fake posts for mock mode (no network)."""
        return [
            PostDTO(
                id=f"mock_{i}",
                title=f"[Mock] Sample post {i + 1} in r/{subreddit}",
                selftext=f"This is mock post body #{i + 1}.",
                author=f"mock_user_{i}",
                subreddit=subreddit,
                score=(i + 1) * 100,
                num_comments=(i + 1) * 5,
                url=f"https://reddit.com/r/{subreddit}/mock_{i}",
                permalink=f"/r/{subreddit}/comments/mock_{i}/sample_post/",
                created_utc=1700000000.0 + i * 3600,
                is_self=True,
            )
            for i in range(5)
        ]

    @staticmethod
    def _mock_comments() -> list[CommentDTO]:
        """Return fake comments for mock mode."""
        return [
            CommentDTO(
                id="mock_c1",
                author="commenter_1",
                body="This is a top-level mock comment.",
                score=50,
                created_utc=1700000000.0,
                depth=0,
                parent_id="t3_mock_0",
                children=[
                    CommentDTO(
                        id="mock_c2",
                        author="commenter_2",
                        body="This is a reply to the first comment.",
                        score=20,
                        created_utc=1700001000.0,
                        depth=1,
                        parent_id="t1_mock_c1",
                    ),
                ],
            ),
            CommentDTO(
                id="mock_c3",
                author="commenter_3",
                body="Another top-level comment.",
                score=30,
                created_utc=1700002000.0,
                depth=0,
                parent_id="t3_mock_0",
            ),
            CommentDTO(
                id="mock_c4",
                author="[deleted]",
                body="[removed]",
                score=0,
                created_utc=1700003000.0,
                depth=0,
                parent_id="t3_mock_0",
            ),
        ]
