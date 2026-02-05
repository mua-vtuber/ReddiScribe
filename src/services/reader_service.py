"""Reader service: fetch + cache + translate orchestration."""

import logging
from typing import Iterator, Optional

from src.adapters.reddit_adapter import RedditAdapter
from src.adapters.llm_adapter import LLMAdapter
from src.core.database import DatabaseManager
from src.core.config_manager import ConfigManager
from src.core.types import PostDTO, CommentDTO, SummaryDTO

logger = logging.getLogger("reddiscribe")


class ReaderService:
    """Orchestrates Reddit data fetching, caching, and AI translation.

    Responsibilities:
    - Fetch posts/comments via RedditAdapter
    - Save posts to DB for caching
    - Generate/cache AI translations for posts and comments
    """

    def __init__(self, reddit: RedditAdapter, llm: LLMAdapter, db: DatabaseManager, config: ConfigManager):
        self._reddit = reddit
        self._llm = llm
        self._db = db
        self._config = config

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

    def get_translation(self, post_id: str, locale: str = "ko_KR") -> Optional[str]:
        """Check DB cache for existing post body translation.

        Args:
            post_id: Reddit post ID
            locale: Locale code

        Returns:
            Translation text if cached, None if not.
        """
        return self._db.get_summary(post_id, model_type="translation", locale=locale)

    def generate_translation(self, post: PostDTO, locale: str = "ko_KR",
                            stream: bool = True) -> Iterator[str]:
        """Generate AI translation of post body via LLM. Streams tokens.

        Translates post title + body to target language.
        Uses the logic model (same as title/comment translation).

        Args:
            post: PostDTO to translate
            locale: Target locale (default: 'ko_KR')
            stream: Whether to stream tokens

        Yields:
            Generated text tokens

        Raises:
            OllamaNotRunningError, ModelNotFoundError, LLMTimeoutError
        """
        target_language = self._config.get("translation.reader_lang", "Korean")

        text_to_translate = post.selftext or post.title
        if post.selftext and post.title:
            text_to_translate = f"Title: {post.title}\n\nContent:\n{post.selftext}"

        prompt = (
            f"Translate the following Reddit post to {target_language}.\n"
            f"\n"
            f"Rules:\n"
            f"- Preserve the tone, style, and formatting\n"
            f"- Be natural, not literal\n"
            f"- Keep technical terms and proper nouns as-is\n"
            f"- Output ONLY the translation\n"
            f"\n"
            f"{text_to_translate}"
        )

        full_text = ""
        for token in self._llm.generate(
            prompt=prompt,
            model=self._config.get("llm.models.logic.name", ""),
            num_ctx=8192,
            stream=stream,
        ):
            full_text += token
            yield token

        # Cache the translation
        self._db.save_summary(SummaryDTO(
            post_id=post.id,
            model_type="translation",
            text=full_text,
            locale=locale,
        ))
        logger.info(f"Saved translation for post {post.id}")

    def delete_translation(self, post_id: str, locale: str = "ko_KR") -> None:
        """Delete cached translation (for refresh).

        Args:
            post_id: Reddit post ID
            locale: Locale code
        """
        self._db.delete_summary(post_id, model_type="translation", locale=locale)
        logger.info(f"Deleted translation for post {post_id}")

    def translate_titles(self, titles: list[str], locale: str = "ko_KR",
                         stream: bool = True) -> Iterator[str]:
        """Batch-translate post titles via LLM.

        Sends all titles in one prompt for efficiency.
        The LLM returns numbered translations matching input order.

        Args:
            titles: List of English post titles
            locale: Target locale (default: 'ko_KR')
            stream: Whether to stream tokens

        Yields:
            Generated text tokens (full response is numbered list of translations)
        """
        target_language = self._config.get("translation.reader_lang", "Korean")
        # Skip if target is English (Reddit content is already English)
        if target_language == "English" or not titles:
            return
        numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))
        prompt = (
            f"Translate each Reddit post title below to {target_language}.\n"
            f"\n"
            f"Rules:\n"
            f"- Translate each title on its own numbered line\n"
            f"- Keep the same numbering (1. 2. 3. ...)\n"
            f"- Be concise - titles should be short\n"
            f"- Output ONLY the numbered translations, nothing else\n"
            f"\n"
            f"{numbered}"
        )

        yield from self._llm.generate(
            prompt=prompt,
            model=self._config.get("llm.models.logic.name", ""),
            num_ctx=8192,
            stream=stream,
        )

    def translate_comment(self, body: str, locale: str = "ko_KR",
                          stream: bool = True) -> Iterator[str]:
        """Translate a single comment body via LLM.

        Args:
            body: Comment text in English
            locale: Target locale (default: 'ko_KR')
            stream: Whether to stream tokens

        Yields:
            Generated text tokens
        """
        target_language = self._config.get("translation.reader_lang", "Korean")
        # Skip if target is English (Reddit content is already English)
        if target_language == "English" or not body.strip():
            return
        prompt = (
            f"Translate the following Reddit comment to {target_language}.\n"
            f"\n"
            f"Rules:\n"
            f"- Preserve the tone and style\n"
            f"- Be natural, not literal\n"
            f"- Output ONLY the translation\n"
            f"\n"
            f"{body}"
        )

        yield from self._llm.generate(
            prompt=prompt,
            model=self._config.get("llm.models.logic.name", ""),
            num_ctx=8192,
            stream=stream,
        )
