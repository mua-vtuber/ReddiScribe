"""Tests for DatabaseManager."""

import pytest
from pathlib import Path

from src.core.database import DatabaseManager
from src.core.types import PostDTO, SummaryDTO
from src.core.exceptions import DatabaseError


def make_post(post_id: str = "abc123", **kwargs) -> PostDTO:
    """Helper to create test PostDTO."""
    defaults = {
        "id": post_id,
        "title": "Test Post",
        "selftext": "Test body",
        "author": "testuser",
        "subreddit": "python",
        "score": 42,
        "num_comments": 10,
        "url": "https://reddit.com/r/python/abc123",
        "permalink": "/r/python/comments/abc123/test_post/",
        "created_utc": 1700000000.0,
        "is_self": True,
    }
    defaults.update(kwargs)
    return PostDTO(**defaults)


def make_summary(post_id: str = "abc123", **kwargs) -> SummaryDTO:
    """Helper to create test SummaryDTO."""
    defaults = {
        "post_id": post_id,
        "model_type": "summary",
        "text": "This is a test summary.",
        "locale": "ko_KR",
    }
    defaults.update(kwargs)
    return SummaryDTO(**defaults)


class TestDatabaseManagerInit:
    """Test database initialization."""

    def test_creates_db_file(self, tmp_db_path):
        db = DatabaseManager(tmp_db_path)
        assert tmp_db_path.exists()

    def test_creates_parent_directory(self, tmp_dir):
        db_path = tmp_dir / "sub" / "dir" / "test.db"
        db = DatabaseManager(db_path)
        assert db_path.exists()

    def test_singleton_returns_same_instance(self, tmp_db_path):
        a = DatabaseManager(tmp_db_path)
        b = DatabaseManager()  # no path needed for second call
        assert a is b

    def test_raises_without_path_on_first_init(self):
        with pytest.raises(DatabaseError):
            DatabaseManager()

    def test_schema_created(self, tmp_db_path):
        db = DatabaseManager(tmp_db_path)
        # Check tables exist by querying sqlite_master
        with db._lock:
            cursor = db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            tables = {row['name'] for row in cursor.fetchall()}
        assert "posts" in tables
        assert "summaries" in tables


class TestDatabaseManagerPosts:
    """Test post CRUD operations."""

    def test_save_and_verify_post(self, tmp_db_path):
        db = DatabaseManager(tmp_db_path)
        post = make_post()
        db.save_post(post)

        with db._lock:
            cursor = db._conn.execute("SELECT * FROM posts WHERE id = ?", (post.id,))
            row = cursor.fetchone()
        assert row is not None
        assert row['title'] == "Test Post"
        assert row['subreddit'] == "python"

    def test_save_duplicate_post_ignored(self, tmp_db_path):
        db = DatabaseManager(tmp_db_path)
        post = make_post()
        db.save_post(post)
        db.save_post(post)  # should not raise

        with db._lock:
            cursor = db._conn.execute("SELECT COUNT(*) as cnt FROM posts")
            count = cursor.fetchone()['cnt']
        assert count == 1

    def test_save_multiple_posts(self, tmp_db_path):
        db = DatabaseManager(tmp_db_path)
        db.save_post(make_post("post1"))
        db.save_post(make_post("post2"))
        db.save_post(make_post("post3"))

        with db._lock:
            cursor = db._conn.execute("SELECT COUNT(*) as cnt FROM posts")
            count = cursor.fetchone()['cnt']
        assert count == 3


class TestDatabaseManagerSummaries:
    """Test summary CRUD operations."""

    def test_save_and_get_summary(self, tmp_db_path):
        db = DatabaseManager(tmp_db_path)
        db.save_post(make_post())
        db.save_summary(make_summary())

        result = db.get_summary("abc123")
        assert result == "This is a test summary."

    def test_get_nonexistent_summary_returns_none(self, tmp_db_path):
        db = DatabaseManager(tmp_db_path)
        result = db.get_summary("nonexistent")
        assert result is None

    def test_upsert_summary_updates_text(self, tmp_db_path):
        db = DatabaseManager(tmp_db_path)
        db.save_post(make_post())
        db.save_summary(make_summary(text="Original"))
        db.save_summary(make_summary(text="Updated"))

        result = db.get_summary("abc123")
        assert result == "Updated"

    def test_different_locales_stored_separately(self, tmp_db_path):
        db = DatabaseManager(tmp_db_path)
        db.save_post(make_post())
        db.save_summary(make_summary(locale="ko_KR", text="한국어 요약"))
        db.save_summary(make_summary(locale="en_US", text="English summary"))

        assert db.get_summary("abc123", locale="ko_KR") == "한국어 요약"
        assert db.get_summary("abc123", locale="en_US") == "English summary"

    def test_delete_summary(self, tmp_db_path):
        db = DatabaseManager(tmp_db_path)
        db.save_post(make_post())
        db.save_summary(make_summary())

        db.delete_summary("abc123")
        assert db.get_summary("abc123") is None

    def test_delete_nonexistent_summary_no_error(self, tmp_db_path):
        db = DatabaseManager(tmp_db_path)
        db.delete_summary("nonexistent")  # should not raise


class TestDatabaseManagerLifecycle:
    """Test close and reset."""

    def test_close_and_reset(self, tmp_db_path):
        db = DatabaseManager(tmp_db_path)
        db.close()
        DatabaseManager.reset()

        # Should be able to create new instance
        db2 = DatabaseManager(tmp_db_path)
        assert db2 is not db
