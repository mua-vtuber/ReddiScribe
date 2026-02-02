"""Reader service: fetch + cache + summarize orchestration."""

import logging
import re
from typing import Iterator, Optional

from src.adapters.reddit_adapter import RedditAdapter
from src.adapters.llm_adapter import LLMAdapter
from src.core.database import DatabaseManager
from src.core.types import PostDTO, CommentDTO, SummaryDTO

logger = logging.getLogger("reddiscribe")


class ReaderService:
    """Orchestrates Reddit data fetching, caching, and AI summarization.

    Responsibilities:
    - Fetch posts/comments via RedditAdapter
    - Save posts to DB for caching
    - Check/generate/cache AI summaries
    - Detect language contamination and retry
    """

    def __init__(self, reddit: RedditAdapter, llm: LLMAdapter, db: DatabaseManager):
        self._reddit = reddit
        self._llm = llm
        self._db = db

    def fetch_posts(self, subreddit: str, sort: str = "hot",
                    limit: int = 25, time_filter: Optional[str] = None) -> list[PostDTO]:
        """Fetch posts from subreddit. Saves to DB for caching.

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
        posts = self._reddit.get_subreddit_posts(subreddit, sort, limit, time_filter)
        # Save posts to DB for caching
        for post in posts:
            self._db.save_post(post)
        logger.info(f"Fetched {len(posts)} posts from r/{subreddit} ({sort})")
        return posts

    def fetch_comments(self, post_id: str, subreddit: str,
                       sort: str = "top", limit: int = 50) -> list[CommentDTO]:
        """Fetch comments for a post. Comments are NOT cached in DB.

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
        comments = self._reddit.get_post_comments(post_id, subreddit, sort, limit)
        logger.info(f"Fetched {len(comments)} comments for post {post_id}")
        return comments

    def get_summary(self, post_id: str, locale: str = "ko_KR") -> Optional[str]:
        """Check DB cache for existing summary.

        Args:
            post_id: Reddit post ID
            locale: Locale code (default: 'ko_KR')

        Returns:
            Summary text if cached, None if not.
        """
        return self._db.get_summary(post_id, model_type="summary", locale=locale)

    def generate_summary(self, post: PostDTO, locale: str = "ko_KR",
                         stream: bool = True) -> Iterator[str]:
        """Generate AI summary via LLM. Streams tokens.

        Flow:
        1. Build prompt
        2. Call LLM generate (streaming)
        3. Collect full text while yielding tokens
        4. Check for language contamination
        5. If contaminated: retry once with strengthened prompt
        6. If clean: save to DB
        7. If retry also contaminated: log warning (do NOT save)

        The method yields tokens as they come for UI streaming.
        After all tokens yielded, checks contamination and saves to DB if clean.

        Args:
            post: PostDTO to summarize
            locale: Locale code (default: 'ko_KR')
            stream: Whether to stream tokens (default: True)

        Yields:
            Generated text tokens

        Raises:
            OllamaNotRunningError: Service not reachable
            ModelNotFoundError: Model not available
            LLMTimeoutError: Request timed out
        """
        # Determine target language name from locale
        target_language = "Korean" if locale == "ko_KR" else "English"

        prompt = self._build_summary_prompt(post, target_language)

        # First attempt
        full_text = ""
        for token in self._llm.generate(
            prompt=prompt,
            model="llama3.1:8b",  # summary model - will be configurable via config later
            num_ctx=8192,
            stream=stream,
        ):
            full_text += token
            yield token

        # Check contamination
        if self._is_language_contaminated(full_text, locale):
            logger.warning(f"Language contamination detected for post {post.id}. Retrying...")

            # Retry with strengthened prompt
            retry_prompt = self._build_strengthened_prompt(prompt, locale)
            retry_text = ""

            for token in self._llm.generate(
                prompt=retry_prompt,
                model="llama3.1:8b",
                num_ctx=8192,
                stream=stream,
            ):
                retry_text += token
                # Don't yield retry tokens - the first attempt tokens were already yielded
                # The caller (GenerationWorker) will use finished_signal to send final text

            # Check if retry is clean
            if self._is_language_contaminated(retry_text, locale):
                logger.warning(f"Retry also contaminated for post {post.id}. Not saving.")
                # Don't save, tokens from first attempt already yielded
                return

            # Retry succeeded - save the clean retry text
            self._db.save_summary(SummaryDTO(
                post_id=post.id,
                model_type="summary",
                text=retry_text,
                locale=locale,
            ))
            logger.info(f"Saved retry summary for post {post.id}")
            return

        # Clean on first attempt - save
        self._db.save_summary(SummaryDTO(
            post_id=post.id,
            model_type="summary",
            text=full_text,
            locale=locale,
        ))
        logger.info(f"Saved summary for post {post.id}")

    def delete_summary(self, post_id: str, locale: str = "ko_KR") -> None:
        """Delete cached summary (for refresh).

        Args:
            post_id: Reddit post ID
            locale: Locale code (default: 'ko_KR')
        """
        self._db.delete_summary(post_id, model_type="summary", locale=locale)
        logger.info(f"Deleted summary for post {post_id}")

    @staticmethod
    def _build_summary_prompt(post: PostDTO, target_language: str) -> str:
        """Build the summary prompt from spec Section 5.3.

        Args:
            post: PostDTO to summarize
            target_language: Target language name (e.g., "Korean", "English")

        Returns:
            Formatted prompt string
        """
        return (
            f"You are a summarization assistant. Summarize the following Reddit post in {target_language}.\n"
            f"\n"
            f"Rules:\n"
            f"- Write exactly 3 concise sentences\n"
            f"- Capture the main argument, key details, and conclusion\n"
            f"- Output ONLY in {target_language}. Do not mix languages.\n"
            f"- Do not add commentary or opinions\n"
            f"\n"
            f"Title: {post.title}\n"
            f"Content: {post.selftext}"
        )

    @staticmethod
    def _build_strengthened_prompt(original_prompt: str, locale: str) -> str:
        """Build strengthened prompt for contamination retry.

        Args:
            original_prompt: The original prompt that resulted in contamination
            locale: Locale code (e.g., 'ko_KR')

        Returns:
            Strengthened prompt with language enforcement prefix
        """
        if locale == "ko_KR":
            prefix = (
                "IMPORTANT: You MUST respond entirely in Korean (한국어).\n"
                "Do not write any English words except proper nouns.\n\n"
            )
        else:
            prefix = ""
        return prefix + original_prompt

    @staticmethod
    def _is_language_contaminated(text: str, expected_locale: str) -> bool:
        """Detect if output language doesn't match expected locale.

        For ko_KR: if Korean char ratio < 30% of all alpha chars, it's contaminated.

        Args:
            text: Generated text to check
            expected_locale: Expected locale code (e.g., 'ko_KR')

        Returns:
            True if language contaminated, False otherwise
        """
        if expected_locale != "ko_KR" or len(text) < 20:
            return False
        korean_chars = len(re.findall(r'[가-힣]', text))
        total_alpha = len(re.findall(r'[a-zA-Z가-힣]', text))
        if total_alpha == 0:
            return False
        korean_ratio = korean_chars / total_alpha
        return korean_ratio < 0.3
