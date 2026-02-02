"""Writer service: 2-stage translation pipeline."""

import logging
from typing import Iterator

from src.adapters.llm_adapter import LLMAdapter

logger = logging.getLogger("reddiscribe")


class WriterService:
    """Orchestrates the 2-stage writing pipeline.

    Stage 1 (Draft): Korean -> English translation using logic model (qwen2.5-coder:32b)
    Stage 2 (Polish): English draft -> Reddit-ready English using persona model (llama3.1:70b)
    """

    def __init__(self, llm: LLMAdapter):
        self._llm = llm

    def draft(self, korean_text: str, stream: bool = True) -> Iterator[str]:
        """Stage 1: Korean -> English draft using logic model.

        Args:
            korean_text: Input Korean text
            stream: Whether to stream tokens

        Yields:
            Generated English tokens

        Raises:
            OllamaNotRunningError, ModelNotFoundError, LLMTimeoutError
        """
        prompt = self._build_draft_prompt(korean_text)

        yield from self._llm.generate(
            prompt=prompt,
            model="qwen2.5-coder:32b",  # logic model
            num_ctx=32768,
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
            stream=stream,
        )

    @staticmethod
    def _build_draft_prompt(korean_text: str) -> str:
        """Build the drafting (translation) prompt from spec Section 5.3."""
        return (
            "Translate the following Korean text to English.\n"
            "\n"
            "Rules:\n"
            "- Preserve the logical structure and meaning\n"
            "- Use natural English grammar, not literal translation\n"
            "- Keep technical terms accurate\n"
            "- Do not add explanations or commentary\n"
            "- Output ONLY the English translation\n"
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
            "- Output ONLY the rewritten text\n"
            "\n"
            f"Original English:\n{draft_text}"
        )
