"""Tests for WriterService."""

import pytest
from unittest.mock import MagicMock

from src.services.writer_service import WriterService
from src.core.config_manager import ConfigManager
from src.core.exceptions import OllamaNotRunningError, ModelNotFoundError


def _make_service(llm=None, set_models=True):
    """Helper to create a WriterService with mocked dependencies.

    Args:
        llm: Mock LLM adapter. If None, creates a basic one.
        set_models: If True, set default test model names in config.
    """
    if llm is None:
        llm = MagicMock()
        llm.generate.return_value = iter(["ok"])
    ConfigManager.reset()
    config = ConfigManager()
    if set_models:
        config.set("llm.models.logic.name", "gemma2:9b")
        config.set("llm.models.persona.name", "llama3.1:70b")
    return WriterService(llm, config)


class TestDraft:
    """Test Stage 1: Korean -> English draft."""

    def test_yields_tokens(self):
        llm = MagicMock()
        llm.generate.return_value = iter(["Hello", " ", "world"])

        service = _make_service(llm)
        tokens = list(service.draft("안녕하세요"))

        assert tokens == ["Hello", " ", "world"]

    def test_uses_logic_model_from_config(self):
        llm = MagicMock()
        llm.generate.return_value = iter(["ok"])

        service = _make_service(llm)
        list(service.draft("테스트"))

        call_kwargs = llm.generate.call_args
        assert call_kwargs.kwargs.get("model") == "gemma2:9b"

    def test_uses_8k_context(self):
        llm = MagicMock()
        llm.generate.return_value = iter(["ok"])

        service = _make_service(llm)
        list(service.draft("테스트"))

        call_kwargs = llm.generate.call_args
        kwargs = call_kwargs.kwargs if call_kwargs.kwargs else {}
        assert kwargs.get("num_ctx") == 8192

    def test_uses_temperature_from_config(self):
        llm = MagicMock()
        llm.generate.return_value = iter(["ok"])

        service = _make_service(llm)
        list(service.draft("테스트"))

        call_kwargs = llm.generate.call_args
        assert call_kwargs.kwargs.get("temperature") == 0.3

    def test_prompt_contains_korean_text(self):
        llm = MagicMock()
        llm.generate.return_value = iter(["ok"])

        service = _make_service(llm)
        list(service.draft("파이썬은 좋은 언어입니다"))

        call_args = llm.generate.call_args
        prompt = call_args.kwargs.get("prompt") or call_args[1].get("prompt")
        assert "파이썬은 좋은 언어입니다" in prompt
        assert "Translate" in prompt

    def test_propagates_errors(self):
        llm = MagicMock()
        llm.generate.side_effect = OllamaNotRunningError()

        service = _make_service(llm)
        with pytest.raises(OllamaNotRunningError):
            list(service.draft("테스트"))


class TestPolish:
    """Test Stage 2: English draft -> Reddit-ready English."""

    def test_yields_tokens(self):
        llm = MagicMock()
        llm.generate.return_value = iter(["IMO", " this", " is", " great"])

        service = _make_service(llm)
        tokens = list(service.polish("This is great"))

        assert tokens == ["IMO", " this", " is", " great"]

    def test_uses_persona_model_from_config(self):
        llm = MagicMock()
        llm.generate.return_value = iter(["ok"])

        service = _make_service(llm)
        list(service.polish("test draft"))

        call_kwargs = llm.generate.call_args
        assert call_kwargs.kwargs.get("model") == "llama3.1:70b"

    def test_uses_8k_context(self):
        llm = MagicMock()
        llm.generate.return_value = iter(["ok"])

        service = _make_service(llm)
        list(service.polish("test"))

        call_kwargs = llm.generate.call_args
        kwargs = call_kwargs.kwargs if call_kwargs.kwargs else {}
        assert kwargs.get("num_ctx") == 8192

    def test_uses_temperature_from_config(self):
        llm = MagicMock()
        llm.generate.return_value = iter(["ok"])

        service = _make_service(llm)
        list(service.polish("test"))

        call_kwargs = llm.generate.call_args
        assert call_kwargs.kwargs.get("temperature") == 0.7

    def test_prompt_contains_draft_text(self):
        llm = MagicMock()
        llm.generate.return_value = iter(["ok"])

        service = _make_service(llm)
        list(service.polish("Python is a great language"))

        call_args = llm.generate.call_args
        prompt = call_args.kwargs.get("prompt") or call_args[1].get("prompt")
        assert "Python is a great language" in prompt

    def test_uses_persona_prompt_from_config(self):
        llm = MagicMock()
        llm.generate.return_value = iter(["ok"])

        service = _make_service(llm)
        list(service.polish("test"))

        call_args = llm.generate.call_args
        prompt = call_args.kwargs.get("prompt") or call_args[1].get("prompt")
        # Default persona prompt contains Reddit reference
        assert "Reddit" in prompt

    def test_propagates_errors(self):
        llm = MagicMock()
        llm.generate.side_effect = ModelNotFoundError()

        service = _make_service(llm)
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

    def test_draft_prompt_custom_language(self):
        prompt = WriterService._build_draft_prompt("테스트", "Japanese")
        assert "Japanese" in prompt


class TestConfigIntegration:
    """Test that config changes are reflected in service behavior."""

    def test_draft_reflects_config_model_change(self):
        llm = MagicMock()
        llm.generate.return_value = iter(["ok"])

        ConfigManager.reset()
        config = ConfigManager()
        config.set("llm.models.logic.name", "custom-model:7b")
        service = WriterService(llm, config)
        list(service.draft("테스트"))

        call_kwargs = llm.generate.call_args
        assert call_kwargs.kwargs.get("model") == "custom-model:7b"

    def test_polish_reflects_config_temperature_change(self):
        llm = MagicMock()
        llm.generate.return_value = iter(["ok"])

        ConfigManager.reset()
        config = ConfigManager()
        config.set("llm.models.persona.temperature", 0.9)
        service = WriterService(llm, config)
        list(service.polish("test"))

        call_kwargs = llm.generate.call_args
        assert call_kwargs.kwargs.get("temperature") == 0.9

    def test_polish_reflects_config_prompt_change(self):
        llm = MagicMock()
        llm.generate.return_value = iter(["ok"])

        ConfigManager.reset()
        config = ConfigManager()
        config.set("llm.models.persona.prompt", "Custom persona instructions")
        service = WriterService(llm, config)
        list(service.polish("test input"))

        call_args = llm.generate.call_args
        prompt = call_args.kwargs.get("prompt")
        assert "Custom persona instructions" in prompt
        assert "test input" in prompt


class TestBuildRefineContext:
    """Test refine chat context building."""

    def test_returns_system_message(self):
        service = _make_service()
        messages = service.build_refine_context("한국어", "draft", "polished")

        assert len(messages) == 1
        assert messages[0]["role"] == "system"

    def test_system_prompt_contains_context(self):
        service = _make_service()
        messages = service.build_refine_context("테스트 입력", "test draft", "test polished")

        system_content = messages[0]["content"]
        assert "테스트 입력" in system_content
        assert "test draft" in system_content
        assert "test polished" in system_content

    def test_system_prompt_has_translation_tag_instruction(self):
        service = _make_service()
        messages = service.build_refine_context("한국어", "draft", "polished")

        system_content = messages[0]["content"]
        assert "[TRANSLATION]" in system_content


class TestRefine:
    """Test refine chat generation."""

    def test_yields_tokens(self):
        llm = MagicMock()
        llm.chat.return_value = iter(["Sure", ", ", "here"])
        llm.generate.return_value = iter(["ok"])

        service = _make_service(llm)
        messages = [{"role": "user", "content": "make it casual"}]
        tokens = list(service.refine(messages))

        assert tokens == ["Sure", ", ", "here"]

    def test_uses_persona_model(self):
        llm = MagicMock()
        llm.chat.return_value = iter(["ok"])
        llm.generate.return_value = iter(["ok"])

        service = _make_service(llm)
        list(service.refine([{"role": "user", "content": "test"}]))

        call_kwargs = llm.chat.call_args
        assert call_kwargs.kwargs.get("model") == "llama3.1:70b"

    def test_propagates_errors(self):
        llm = MagicMock()
        llm.chat.side_effect = OllamaNotRunningError()
        llm.generate.return_value = iter(["ok"])

        service = _make_service(llm)
        with pytest.raises(OllamaNotRunningError):
            list(service.refine([{"role": "user", "content": "test"}]))


class TestParseRefineResponse:
    """Test [TRANSLATION] tag parsing."""

    def test_extracts_translation_and_comment(self):
        from src.services.writer_service import parse_refine_response

        text = "수정했어요. [TRANSLATION]I'm kinda nervous[/TRANSLATION] 어떤가요?"
        translation, comment = parse_refine_response(text)

        assert translation == "I'm kinda nervous"
        assert "수정했어요." in comment
        assert "어떤가요?" in comment

    def test_no_tag_returns_none_translation(self):
        from src.services.writer_service import parse_refine_response

        text = "이 번역은 괜찮은 것 같아요."
        translation, comment = parse_refine_response(text)

        assert translation is None
        assert comment == "이 번역은 괜찮은 것 같아요."

    def test_multiline_translation(self):
        from src.services.writer_service import parse_refine_response

        text = "Here:\n[TRANSLATION]Line one.\nLine two.[/TRANSLATION]\nDone."
        translation, comment = parse_refine_response(text)

        assert translation == "Line one.\nLine two."
        assert "Done." in comment

    def test_empty_string(self):
        from src.services.writer_service import parse_refine_response

        translation, comment = parse_refine_response("")
        assert translation is None
        assert comment == ""
