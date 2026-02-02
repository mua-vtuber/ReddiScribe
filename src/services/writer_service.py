"""Writer service: 2-stage translation pipeline."""

import logging
from typing import Iterator

from src.adapters.llm_adapter import LLMAdapter

logger = logging.getLogger("reddiscribe")


class WriterService:
    """Orchestrates the 2-stage writing pipeline.

    Stage 1 (Draft): Korean -> English translation using logic model (gemma2:9b)
    Stage 2 (Polish): English draft -> Reddit-ready English using persona model (llama3.1:70b)
    """

    def __init__(self, llm: LLMAdapter):
        self._llm = llm

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
            model="gemma2:9b",  # logic model
            num_ctx=8192,
            temperature=0.3,  # low temperature for faithful translation
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
        prompt = self._build_polish_prompt(english_draft)

        yield from self._llm.generate(
            prompt=prompt,
            model="llama3.1:70b",  # persona model
            num_ctx=8192,
            temperature=0.7,  # moderate creativity for natural Reddit tone
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

    @staticmethod
    def _build_polish_prompt(draft_text: str) -> str:
        """Build the polishing prompt from spec Section 5.3."""
        return (
            "Rewrite the following English text to sound natural for a Reddit post.\n"
            "\n"
            "Rules:\n"
            "- Use casual, conversational tone appropriate for Reddit\n"
            '- Add common Reddit expressions where natural (e.g., "IMO", "FWIW")\n'
            "- Keep the core meaning intact\n"
            "- Do not over-use slang - keep it readable\n"
            "- Match the tone to the subreddit context if provided\n"
            "- NEVER add facts, details, tool names, or motivations not present in the original\n"
            "- NEVER invent specific names (e.g., 'Google Translate') unless explicitly mentioned\n"
            "- Only rephrase existing information - do not expand or embellish\n"
            "- Output ONLY the rewritten text\n"
            "\n"
            f"Original English:\n{draft_text}"
        )
