"""Tests for ReaderService."""

import pytest
from unittest.mock import MagicMock, patch, call

from src.services.reader_service import ReaderService
from src.core.types import PostDTO, CommentDTO, SummaryDTO
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


class TestFetchPosts:
    def test_fetches_and_saves_posts(self):
        posts = [make_post("p1"), make_post("p2")]
        reddit = MagicMock()
        reddit.get_subreddit_posts.return_value = posts
        db = MagicMock()
        llm = MagicMock()

        service = ReaderService(reddit, llm, db)
        result = service.fetch_posts("python", sort="hot", limit=25)

        assert result == posts
        assert db.save_post.call_count == 2
        reddit.get_subreddit_posts.assert_called_once_with("python", "hot", 25, None)

    def test_propagates_reddit_errors(self):
        reddit = MagicMock()
        reddit.get_subreddit_posts.side_effect = RedditFetchError("fail")
        service = ReaderService(reddit, MagicMock(), MagicMock())

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

        service = ReaderService(reddit, MagicMock(), MagicMock())
        result = service.fetch_comments("post1", "python")

        assert result == comments
        reddit.get_post_comments.assert_called_once_with("post1", "python", "top", 50)


class TestGetSummary:
    def test_returns_cached_summary(self):
        db = MagicMock()
        db.get_summary.return_value = "Cached summary"

        service = ReaderService(MagicMock(), MagicMock(), db)
        result = service.get_summary("post1")

        assert result == "Cached summary"
        db.get_summary.assert_called_once_with("post1", model_type="summary", locale="ko_KR")

    def test_returns_none_when_no_cache(self):
        db = MagicMock()
        db.get_summary.return_value = None

        service = ReaderService(MagicMock(), MagicMock(), db)
        assert service.get_summary("post1") is None


class TestGenerateSummary:
    def test_clean_summary_saved_to_db(self):
        """Korean summary should be saved to DB."""
        korean_tokens = ["이것은 ", "테스트 ", "요약입니다. ", "한국어로 ", "작성되었습니다."]
        llm = make_mock_llm(korean_tokens)
        db = MagicMock()

        service = ReaderService(MagicMock(), llm, db)
        post = make_post()
        tokens = list(service.generate_summary(post, locale="ko_KR"))

        assert tokens == korean_tokens
        db.save_summary.assert_called_once()
        saved = db.save_summary.call_args[0][0]
        assert saved.post_id == "test1"
        assert saved.text == "이것은 테스트 요약입니다. 한국어로 작성되었습니다."

    def test_english_locale_skips_contamination_check(self):
        """en_US locale should always save (no contamination check)."""
        tokens = ["This", " is", " English."]
        llm = make_mock_llm(tokens)
        db = MagicMock()

        service = ReaderService(MagicMock(), llm, db)
        post = make_post()
        list(service.generate_summary(post, locale="en_US"))

        db.save_summary.assert_called_once()

    def test_contaminated_summary_triggers_retry(self):
        """English output for ko_KR locale should trigger retry."""
        english_tokens = ["This", " is", " an", " English", " summary", " with", " many", " words", " that", " are", " not", " Korean."]
        korean_retry = ["이것은 ", "한국어 ", "요약입니다."]

        llm = MagicMock()
        # First call returns English, second call returns Korean
        llm.generate.side_effect = [iter(english_tokens), iter(korean_retry)]
        db = MagicMock()

        service = ReaderService(MagicMock(), llm, db)
        post = make_post()
        tokens = list(service.generate_summary(post, locale="ko_KR"))

        # First attempt tokens are yielded
        assert tokens == english_tokens
        # LLM called twice (original + retry)
        assert llm.generate.call_count == 2
        # Retry text saved (not original)
        db.save_summary.assert_called_once()
        saved_text = db.save_summary.call_args[0][0].text
        assert "한국어" in saved_text

    def test_double_contamination_no_save(self):
        """If retry is also contaminated, don't save."""
        english1 = ["This", " is", " English", " first", " attempt", " with", " lots", " of", " words", " here."]
        english2 = ["Still", " English", " second", " attempt", " with", " many", " words", " again", " not", " Korean."]

        llm = MagicMock()
        llm.generate.side_effect = [iter(english1), iter(english2)]
        db = MagicMock()

        service = ReaderService(MagicMock(), llm, db)
        post = make_post()
        list(service.generate_summary(post, locale="ko_KR"))

        db.save_summary.assert_not_called()

    def test_short_text_not_checked_for_contamination(self):
        """Text shorter than 20 chars is not checked."""
        tokens = ["Short"]
        llm = make_mock_llm(tokens)
        db = MagicMock()

        service = ReaderService(MagicMock(), llm, db)
        post = make_post()
        list(service.generate_summary(post, locale="ko_KR"))

        db.save_summary.assert_called_once()

    def test_propagates_llm_errors(self):
        llm = MagicMock()
        llm.generate.side_effect = OllamaNotRunningError()

        service = ReaderService(MagicMock(), llm, MagicMock())
        with pytest.raises(OllamaNotRunningError):
            list(service.generate_summary(make_post()))


class TestDeleteSummary:
    def test_deletes_from_db(self):
        db = MagicMock()
        service = ReaderService(MagicMock(), MagicMock(), db)
        service.delete_summary("post1")
        db.delete_summary.assert_called_once_with("post1", model_type="summary", locale="ko_KR")


class TestContaminationDetection:
    def test_pure_korean_not_contaminated(self):
        assert not ReaderService._is_language_contaminated(
            "이것은 한국어로 작성된 텍스트입니다. 오염되지 않았습니다.", "ko_KR"
        )

    def test_pure_english_is_contaminated(self):
        assert ReaderService._is_language_contaminated(
            "This is a purely English text with no Korean characters at all here.", "ko_KR"
        )

    def test_mixed_above_threshold_not_contaminated(self):
        # ~50% Korean should pass
        assert not ReaderService._is_language_contaminated(
            "한국어text한국어text한국어text", "ko_KR"
        )

    def test_en_us_locale_never_contaminated(self):
        assert not ReaderService._is_language_contaminated(
            "English only text here with no Korean at all in this long text.", "en_US"
        )

    def test_short_text_never_contaminated(self):
        assert not ReaderService._is_language_contaminated("Short", "ko_KR")
