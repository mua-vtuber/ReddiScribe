"""Tests for WriterService."""

import pytest
from unittest.mock import MagicMock

from src.services.writer_service import WriterService
from src.core.exceptions import OllamaNotRunningError, ModelNotFoundError


class TestDraft:
    """Test Stage 1: Korean -> English draft."""

    def test_yields_tokens(self):
        llm = MagicMock()
        llm.generate.return_value = iter(["Hello", " ", "world"])

        service = WriterService(llm)
        tokens = list(service.draft("안녕하세요"))

        assert tokens == ["Hello", " ", "world"]

    def test_uses_logic_model(self):
        llm = MagicMock()
        llm.generate.return_value = iter(["ok"])

        service = WriterService(llm)
        list(service.draft("테스트"))

        call_kwargs = llm.generate.call_args
        assert call_kwargs.kwargs.get("model") or call_kwargs[1].get("model") == "gemma2:9b"

    def test_uses_8k_context(self):
        llm = MagicMock()
        llm.generate.return_value = iter(["ok"])

        service = WriterService(llm)
        list(service.draft("테스트"))

        # Check num_ctx in the call
        call_kwargs = llm.generate.call_args
        kwargs = call_kwargs.kwargs if call_kwargs.kwargs else {}
        assert kwargs.get("num_ctx") == 8192

    def test_prompt_contains_korean_text(self):
        llm = MagicMock()
        llm.generate.return_value = iter(["ok"])

        service = WriterService(llm)
        list(service.draft("파이썬은 좋은 언어입니다"))

        call_args = llm.generate.call_args
        prompt = call_args.kwargs.get("prompt") or call_args[1].get("prompt")
        assert "파이썬은 좋은 언어입니다" in prompt
        assert "Translate" in prompt

    def test_propagates_errors(self):
        llm = MagicMock()
        llm.generate.side_effect = OllamaNotRunningError()

        service = WriterService(llm)
        with pytest.raises(OllamaNotRunningError):
            list(service.draft("테스트"))


class TestPolish:
    """Test Stage 2: English draft -> Reddit-ready English."""

    def test_yields_tokens(self):
        llm = MagicMock()
        llm.generate.return_value = iter(["IMO", " this", " is", " great"])

        service = WriterService(llm)
        tokens = list(service.polish("This is great"))

        assert tokens == ["IMO", " this", " is", " great"]

    def test_uses_persona_model(self):
        llm = MagicMock()
        llm.generate.return_value = iter(["ok"])

        service = WriterService(llm)
        list(service.polish("test draft"))

        call_kwargs = llm.generate.call_args
        model = call_kwargs.kwargs.get("model") or call_kwargs[1].get("model")
        assert model == "llama3.1:70b"

    def test_uses_8k_context(self):
        llm = MagicMock()
        llm.generate.return_value = iter(["ok"])

        service = WriterService(llm)
        list(service.polish("test"))

        call_kwargs = llm.generate.call_args
        kwargs = call_kwargs.kwargs if call_kwargs.kwargs else {}
        assert kwargs.get("num_ctx") == 8192

    def test_prompt_contains_draft_text(self):
        llm = MagicMock()
        llm.generate.return_value = iter(["ok"])

        service = WriterService(llm)
        list(service.polish("Python is a great language"))

        call_args = llm.generate.call_args
        prompt = call_args.kwargs.get("prompt") or call_args[1].get("prompt")
        assert "Python is a great language" in prompt
        assert "Reddit" in prompt

    def test_propagates_errors(self):
        llm = MagicMock()
        llm.generate.side_effect = ModelNotFoundError()

        service = WriterService(llm)
        with pytest.raises(ModelNotFoundError):
            list(service.polish("test"))


class TestPromptBuilding:
    """Test prompt templates match spec."""

    def test_draft_prompt_structure(self):
        prompt = WriterService._build_draft_prompt("테스트 입력")
        assert "Translate" in prompt
        assert "Korean text" in prompt
        assert "테스트 입력" in prompt
        assert "English" in prompt

    def test_polish_prompt_structure(self):
        prompt = WriterService._build_polish_prompt("test draft")
        assert "Reddit" in prompt
        assert "casual" in prompt.lower() or "conversational" in prompt.lower()
        assert "test draft" in prompt
        assert "IMO" in prompt or "FWIW" in prompt
