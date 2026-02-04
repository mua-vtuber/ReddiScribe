"""Tests for ReaderService."""

import pytest
from unittest.mock import MagicMock, patch, call

from src.services.reader_service import ReaderService
from src.core.types import PostDTO, CommentDTO
from src.core.exceptions import RedditFetchError, OllamaNotRunningError


def make_post(post_id="test1", title="Test Post", selftext="Test body"):
    return PostDTO(
        id=post_id, title=title, selftext=selftext,
        author="user", subreddit="python", score=10,
        num_comments=5, url="http://test", permalink="/r/test",
        created_utc=1700000000.0, is_self=True,
    )


def make_mock_llm(tokens):
    """Create a mock LLM adapter that yields given tokens."""
    mock = MagicMock()
    mock.generate.return_value = iter(tokens)
    return mock


def make_mock_config():
    """Create a mock ConfigManager that returns default model names."""
    mock = MagicMock()
    mock.get.side_effect = lambda key, default=None: default
    return mock


class TestFetchPosts:
    def test_fetches_and_saves_posts(self):
        posts = [make_post("p1"), make_post("p2")]
        reddit = MagicMock()
        reddit.get_subreddit_posts.return_value = posts
        db = MagicMock()
        llm = MagicMock()

        service = ReaderService(reddit, llm, db, make_mock_config())
        result = service.fetch_posts("python", sort="hot", limit=25)

        assert result == posts
        assert db.save_post.call_count == 2
        reddit.get_subreddit_posts.assert_called_once_with("python", "hot", 25, None)

    def test_propagates_reddit_errors(self):
        reddit = MagicMock()
        reddit.get_subreddit_posts.side_effect = RedditFetchError("fail")
        service = ReaderService(reddit, MagicMock(), MagicMock(), make_mock_config())

        with pytest.raises(RedditFetchError):
            service.fetch_posts("python")


class TestFetchComments:
    def test_fetches_comments(self):
        comments = [
            CommentDTO(id="c1", body="Hello", depth=0),
            CommentDTO(id="c2", body="World", depth=0),
        ]
        reddit = MagicMock()
        reddit.get_post_comments.return_value = comments

        service = ReaderService(reddit, MagicMock(), MagicMock(), make_mock_config())
        result = service.fetch_comments("post1", "python")

        assert result == comments
        reddit.get_post_comments.assert_called_once_with("post1", "python", "top", 50)
