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
            model="qwen2.5-coder:32b",  # logic model
            num_ctx=32768,
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
            f"Literally translate the following Korean text into {target_lang}.\n"
            "\n"
            "Rules:\n"
            "- Keep the colloquial tone as-is\n"
            "- Only fix grammar, follow Korean word order otherwise\n"
            "- Do NOT make it sound natural — keep it literal\n"
            "- Do NOT paraphrase or interpret\n"
            "- NEVER add facts, details, or information not in the original\n"
            "- NEVER invent names, tools, or references\n"
            f"- Output ONLY the {target_lang} translation\n"
            "\n"
            "Example:\n"
            "'관심 서브레딧을 번역해서 보고'\n"
            "→ 'checked a subreddit I'm interested in with translation and'\n"
            "(It does not need to sound natural — Stage 2 will refine it)\n"
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
