"""Tests for PublicJSONAdapter."""

import pytest
from unittest.mock import patch, MagicMock

from src.adapters.public_json_adapter import PublicJSONAdapter, RateLimiter
from src.core.exceptions import (
    RedditFetchError,
    RateLimitError,
    SubredditNotFoundError,
    SubredditPrivateError,
)
from src.core.types import PostDTO, CommentDTO


# --- Helper: Create mock Reddit JSON responses ---

def make_post_listing(*posts_data):
    """Create a Reddit listing response with post data."""
    children = []
    for p in posts_data:
        children.append({
            "kind": "t3",
            "data": {
                "id": p.get("id", "abc123"),
                "title": p.get("title", "Test Post"),
                "selftext": p.get("selftext", "body"),
                "author": p.get("author", "testuser"),
                "subreddit": p.get("subreddit", "python"),
                "score": p.get("score", 42),
                "num_comments": p.get("num_comments", 10),
                "url": p.get("url", "https://reddit.com/test"),
                "permalink": p.get("permalink", "/r/python/comments/abc123/test/"),
                "created_utc": p.get("created_utc", 1700000000.0),
                "is_self": p.get("is_self", True),
            }
        })
    return {"data": {"children": children}}


def make_comment_response(comments_data):
    """Create a Reddit comments response (array of 2 listings).
    comments_data is a list of dicts with comment fields."""
    children = []
    for c in comments_data:
        children.append({
            "kind": c.get("kind", "t1"),
            "data": {
                "id": c.get("id", "c1"),
                "author": c.get("author", "commenter"),
                "body": c.get("body", "test comment"),
                "score": c.get("score", 10),
                "created_utc": c.get("created_utc", 1700000000.0),
                "depth": c.get("depth", 0),
                "parent_id": c.get("parent_id", "t3_abc123"),
                "replies": c.get("replies", ""),
            }
        })

    post_listing = {"data": {"children": [{"kind": "t3", "data": {"id": "abc123", "title": "Test"}}]}}
    comment_listing = {"data": {"children": children}}
    return [post_listing, comment_listing]


def mock_response(status_code=200, json_data=None, content_type="application/json"):
    """Create a mock requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.headers = {"Content-Type": content_type}
    resp.text = str(json_data)
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        from requests.exceptions import HTTPError
        resp.raise_for_status.side_effect = HTTPError(f"HTTP {status_code}")
    return resp


class TestMockMode:
    """Test mock mode returns fake data without network."""

    def test_mock_posts_returns_5_posts(self):
        adapter = PublicJSONAdapter(mock_mode=True)
        posts = adapter.get_subreddit_posts("python")
        assert len(posts) == 5
        assert all(isinstance(p, PostDTO) for p in posts)
        assert posts[0].subreddit == "python"
        assert "mock" in posts[0].id.lower() or "mock" in posts[0].title.lower()

    def test_mock_comments_returns_comments(self):
        adapter = PublicJSONAdapter(mock_mode=True)
        comments = adapter.get_post_comments("abc", "python")
        assert len(comments) == 3
        assert all(isinstance(c, CommentDTO) for c in comments)
        # First comment should have a child
        assert len(comments[0].children) == 1


class TestGetSubredditPosts:
    """Test post fetching with mocked HTTP."""

    @patch("src.adapters.public_json_adapter.PublicJSONAdapter._fetch_json")
    def test_parses_posts_correctly(self, mock_fetch):
        mock_fetch.return_value = make_post_listing(
            {"id": "p1", "title": "First Post", "score": 100},
            {"id": "p2", "title": "Second Post", "score": 200},
        )
        adapter = PublicJSONAdapter()
        posts = adapter.get_subreddit_posts("python")

        assert len(posts) == 2
        assert posts[0].id == "p1"
        assert posts[0].title == "First Post"
        assert posts[1].score == 200

    @patch("src.adapters.public_json_adapter.PublicJSONAdapter._fetch_json")
    def test_passes_sort_and_limit(self, mock_fetch):
        mock_fetch.return_value = make_post_listing()
        adapter = PublicJSONAdapter()
        adapter.get_subreddit_posts("python", sort="top", limit=10, time_filter="week")

        call_args = mock_fetch.call_args
        url = call_args[0][0]
        params = call_args[0][1]
        assert "/top.json" in url
        assert params["limit"] == 10
        assert params["t"] == "week"

    @patch("src.adapters.public_json_adapter.PublicJSONAdapter._fetch_json")
    def test_empty_listing_returns_empty_list(self, mock_fetch):
        mock_fetch.return_value = {"data": {"children": []}}
        adapter = PublicJSONAdapter()
        posts = adapter.get_subreddit_posts("empty_sub")
        assert posts == []


class TestGetPostComments:
    """Test comment fetching with mocked HTTP."""

    @patch("src.adapters.public_json_adapter.PublicJSONAdapter._fetch_json")
    def test_parses_comments(self, mock_fetch):
        mock_fetch.return_value = make_comment_response([
            {"id": "c1", "body": "Hello", "depth": 0},
            {"id": "c2", "body": "World", "depth": 0},
        ])
        adapter = PublicJSONAdapter()
        comments = adapter.get_post_comments("abc", "python")

        assert len(comments) == 2
        assert comments[0].body == "Hello"

    @patch("src.adapters.public_json_adapter.PublicJSONAdapter._fetch_json")
    def test_handles_more_kind(self, mock_fetch):
        mock_fetch.return_value = make_comment_response([
            {"kind": "more", "id": "more1", "depth": 0, "body": "", "score": 0,
             "author": "", "parent_id": "", "created_utc": 0, "replies": ""},
        ])
        # Fix the mock data to match what _parse_comment expects for "more"
        mock_fetch.return_value[1]["data"]["children"] = [{
            "kind": "more",
            "data": {"id": "more1", "count": 15, "depth": 0}
        }]

        adapter = PublicJSONAdapter()
        comments = adapter.get_post_comments("abc", "python")
        assert len(comments) == 1
        assert comments[0].more_count == 15

    @patch("src.adapters.public_json_adapter.PublicJSONAdapter._fetch_json")
    def test_invalid_response_format_raises(self, mock_fetch):
        mock_fetch.return_value = {"not": "a list"}
        adapter = PublicJSONAdapter()
        with pytest.raises(RedditFetchError):
            adapter.get_post_comments("abc", "python")


class TestFetchJsonErrorHandling:
    """Test HTTP error handling in _fetch_json."""

    @patch("requests.Session.get")
    def test_404_raises_subreddit_not_found(self, mock_get):
        mock_get.return_value = mock_response(404)
        adapter = PublicJSONAdapter(request_interval_sec=0)
        with pytest.raises(SubredditNotFoundError):
            adapter.get_subreddit_posts("nonexistent")

    @patch("requests.Session.get")
    def test_403_raises_subreddit_private(self, mock_get):
        mock_get.return_value = mock_response(403)
        adapter = PublicJSONAdapter(request_interval_sec=0)
        with pytest.raises(SubredditPrivateError):
            adapter.get_subreddit_posts("private_sub")

    @patch("requests.Session.get")
    def test_429_retries_then_raises(self, mock_get):
        mock_get.return_value = mock_response(429)
        adapter = PublicJSONAdapter(request_interval_sec=0, max_retries=1)
        with pytest.raises(RateLimitError):
            adapter.get_subreddit_posts("python")
        # Should have been called 1 (initial) + 1 (retry) = 2 times
        assert mock_get.call_count == 2

    @patch("requests.Session.get")
    def test_html_response_retries(self, mock_get):
        html_resp = mock_response(200, content_type="text/html")
        json_resp = mock_response(200, json_data=make_post_listing({"id": "p1", "title": "OK"}))
        mock_get.side_effect = [html_resp, json_resp]

        adapter = PublicJSONAdapter(request_interval_sec=0, max_retries=2)
        posts = adapter.get_subreddit_posts("python")
        assert len(posts) == 1


class TestRateLimiter:
    """Test rate limiter behavior."""

    def test_first_request_no_wait(self):
        rl = RateLimiter(interval_sec=1.0)
        # First request should not need to wait
        rl.wait()  # should return immediately
        rl.mark_request()

    def test_backoff_time_exponential(self):
        rl = RateLimiter(interval_sec=6.0)
        assert rl.get_backoff_time(0) == 6.0
        assert rl.get_backoff_time(1) == 12.0
        assert rl.get_backoff_time(2) == 24.0


class TestParseComment:
    """Test comment tree parsing."""

    def test_parse_simple_comment(self):
        item = {
            "kind": "t1",
            "data": {
                "id": "c1", "author": "user1", "body": "Hello",
                "score": 5, "created_utc": 1700000000.0,
                "depth": 0, "parent_id": "t3_abc", "replies": "",
            }
        }
        result = PublicJSONAdapter._parse_comment(item)
        assert result.id == "c1"
        assert result.body == "Hello"
        assert result.children == []

    def test_parse_nested_comments(self):
        item = {
            "kind": "t1",
            "data": {
                "id": "c1", "author": "user1", "body": "Parent",
                "score": 5, "created_utc": 1700000000.0,
                "depth": 0, "parent_id": "t3_abc",
                "replies": {
                    "data": {
                        "children": [{
                            "kind": "t1",
                            "data": {
                                "id": "c2", "author": "user2", "body": "Child",
                                "score": 3, "created_utc": 1700001000.0,
                                "depth": 1, "parent_id": "t1_c1", "replies": "",
                            }
                        }]
                    }
                }
            }
        }
        result = PublicJSONAdapter._parse_comment(item)
        assert len(result.children) == 1
        assert result.children[0].body == "Child"

    def test_parse_more_kind(self):
        item = {"kind": "more", "data": {"id": "more1", "count": 42, "depth": 0}}
        result = PublicJSONAdapter._parse_comment(item)
        assert result.more_count == 42

    def test_parse_unknown_kind_returns_none(self):
        item = {"kind": "t5", "data": {}}
        result = PublicJSONAdapter._parse_comment(item)
        assert result is None

    def test_depth_limit_stops_recursion(self):
        """Comments deeper than max_depth should not have children parsed."""
        item = {
            "kind": "t1",
            "data": {
                "id": "deep", "author": "u", "body": "deep",
                "score": 0, "created_utc": 0, "depth": 5,
                "parent_id": "t1_x",
                "replies": {
                    "data": {
                        "children": [{
                            "kind": "t1",
                            "data": {
                                "id": "deeper", "author": "u", "body": "too deep",
                                "score": 0, "created_utc": 0, "depth": 6,
                                "parent_id": "t1_deep", "replies": "",
                            }
                        }]
                    }
                }
            }
        }
        result = PublicJSONAdapter._parse_comment(item, max_depth=5)
        assert result.children == []
