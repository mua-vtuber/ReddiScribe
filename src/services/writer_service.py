"""Writer service: 2-stage translation pipeline."""

import logging
from typing import Iterator

from src.adapters.llm_adapter import LLMAdapter
from src.core.config_manager import ConfigManager

logger = logging.getLogger("reddiscribe")


class WriterService:
    """Orchestrates the 2-stage writing pipeline.

    Stage 1 (Draft): Korean -> English literal translation using logic model
    Stage 2 (Polish): English draft -> Reddit-ready English using persona model

    All model names, temperatures, and prompts are read from ConfigManager
    so Settings UI changes take effect immediately.
    """

    def __init__(self, llm: LLMAdapter, config: ConfigManager):
        self._llm = llm
        self._config = config

    def draft(self, korean_text: str, target_lang: str = "English", stream: bool = True) -> Iterator[str]:
        """Stage 1: Korean -> target language draft using logic model.

        Args:
            korean_text: Input Korean text
            target_lang: Target language name (e.g. "English", "Korean")
            stream: Whether to stream tokens

        Yields:
            Generated tokens in target language

        Raises:
            OllamaNotRunningError, ModelNotFoundError, LLMTimeoutError
        """
        prompt = self._build_draft_prompt(korean_text, target_lang)

        yield from self._llm.generate(
            prompt=prompt,
            model=self._config.get("llm.models.logic.name", "gemma2:9b"),
            num_ctx=self._config.get("llm.models.logic.num_ctx", 8192),
            temperature=self._config.get("llm.models.logic.temperature", 0.3),
            stream=stream,
        )

    def polish(self, english_draft: str, stream: bool = True) -> Iterator[str]:
        """Stage 2: English draft -> Reddit-ready English using persona model.

        Args:
            english_draft: Stage 1 output (English draft)
            stream: Whether to stream tokens

        Yields:
            Generated Reddit-ready English tokens

        Raises:
            OllamaNotRunningError, ModelNotFoundError, LLMTimeoutError
        """
        persona_prompt = self._config.get("llm.models.persona.prompt", "")
        full_prompt = f"{persona_prompt}\n\nOriginal English:\n{english_draft}"

        yield from self._llm.generate(
            prompt=full_prompt,
            model=self._config.get("llm.models.persona.name", "llama3.1:70b"),
            num_ctx=self._config.get("llm.models.persona.num_ctx", 8192),
            temperature=self._config.get("llm.models.persona.temperature", 0.7),
            stream=stream,
        )

    @staticmethod
    def _build_draft_prompt(korean_text: str, target_lang: str = "English") -> str:
        """Build the drafting (literal translation) prompt."""
        return (
            f"Translate the following Korean text into {target_lang}.\n"
            "\n"
            "Absolute rules for translation:\n"
            "1. Do NOT change action verbs\n"
            "   - '보다' (to see/check) ≠ 'subscribe'\n"
            "   - '만들다' (to make) ≠ 'develop'\n"
            "2. Do NOT add concepts not in the original\n"
            "   - '번역해서' (by translating) must NOT be omitted\n"
            "   - '도구' (tool) must NOT be specified as 'Google Translate' etc.\n"
            "3. If meaning is ambiguous, add clarification in parentheses\n"
            "   - e.g., 'checked out (by translating)'\n"
            "- NEVER invent names, tools, or references\n"
            f"- Output ONLY the {target_lang} translation\n"
            "\n"
            f"Korean text:\n{korean_text}"
        )
