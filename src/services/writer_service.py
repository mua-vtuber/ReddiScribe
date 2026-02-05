"""Writer service: 2-stage translation pipeline."""

import logging
import re
from typing import Iterator

from src.adapters.llm_adapter import LLMAdapter
from src.core.config_manager import ConfigManager
from src.core.types import WriterContext

logger = logging.getLogger("reddiscribe")


def parse_refine_response(text: str) -> tuple:
    """Parse AI refine response into translation and comment.

    Extracts text wrapped in [TRANSLATION]...[/TRANSLATION] tags.

    Returns:
        (translation, comment) - translation is None if no tag found
    """
    pattern = r'\[TRANSLATION\](.*?)\[/TRANSLATION\]'
    match = re.search(pattern, text, re.DOTALL)

    if match:
        translation = match.group(1).strip()
        comment = re.sub(pattern, '', text, flags=re.DOTALL).strip()
        return translation, comment

    return None, text.strip()


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

    def draft(self, source_text: str, target_lang: str = None, stream: bool = True) -> Iterator[str]:
        """Stage 1: Source language -> target language draft using logic model.

        Args:
            source_text: Input text in source language
            target_lang: Target language name (uses config if not specified)
            stream: Whether to stream tokens

        Yields:
            Generated tokens in target language

        Raises:
            OllamaNotRunningError, ModelNotFoundError, LLMTimeoutError
        """
        if target_lang is None:
            target_lang = self._config.get("translation.target_lang", "English")
        prompt = self._build_draft_prompt(source_text, target_lang)

        yield from self._llm.generate(
            prompt=prompt,
            model=self._config.get("llm.models.logic.name", ""),
            num_ctx=self._config.get("llm.models.logic.num_ctx", 8192),
            temperature=self._config.get("llm.models.logic.temperature", 0.3),
            stream=stream,
        )

    def polish(
        self, english_draft: str, korean_text: str = "",
        context: WriterContext = None, stream: bool = True
    ) -> Iterator[str]:
        """Stage 2: English draft -> Reddit-ready English using persona model.

        Args:
            english_draft: Stage 1 output (English draft)
            korean_text: Original Korean input for nuance reference
            context: Optional context about the writing mode (comment/reply)
            stream: Whether to stream tokens

        Yields:
            Generated Reddit-ready English tokens

        Raises:
            OllamaNotRunningError, ModelNotFoundError, LLMTimeoutError
        """
        persona_prompt = self._config.get("llm.models.persona.prompt", "")

        # Build context-aware instructions
        context_instructions = ""
        if context and context.mode == "comment":
            context_instructions = (
                f"\nContext: You are writing a comment on a Reddit post "
                f"titled \"{context.post_title}\" in r/{context.subreddit}.\n"
                "Adjust the tone to be appropriate for a comment reply.\n"
            )
        elif context and context.mode == "reply":
            excerpt = (context.comment_body[:200] + "...") if len(context.comment_body) > 200 else context.comment_body
            context_instructions = (
                f"\nContext: You are replying to a comment by @{context.comment_author} "
                f"on a Reddit post in r/{context.subreddit}.\n"
                f"The comment you're replying to: \"{excerpt}\"\n"
                "Adjust the tone to be appropriate for a reply to this specific comment.\n"
            )

        # Build the prompt with fixed system rules + user persona
        full_prompt = (
            "Create a polished translation by preserving the original's feel "
            "while referencing the draft translation.\n\n"
            "ABSOLUTE RULES:\n"
            "- Do NOT add words that are not in the original\n"
            "- Do NOT add facts or information\n"
            "- Keep the meaning intact, only change the expression\n\n"
            f"Style instructions:\n{persona_prompt}\n"
            f"{context_instructions}\n"
            f"Original (Korean):\n{korean_text}\n\n"
            f"Draft translation (English):\n{english_draft}\n\n"
            "Output the polished translation in English ONLY."
        )

        yield from self._llm.generate(
            prompt=full_prompt,
            model=self._config.get("llm.models.persona.name", ""),
            num_ctx=self._config.get("llm.models.persona.num_ctx", 8192),
            temperature=self._config.get("llm.models.persona.temperature", 0.7),
            stream=stream,
        )

    def build_refine_context(
        self, source_text: str, draft: str,
        comment_lang: str = "Korean",
        context: WriterContext = None,
    ) -> list[dict]:
        """Build initial chat context for refine conversation.

        Args:
            source_text: Original input in source language
            draft: Stage 1 draft output
            comment_lang: Language for AI comments (e.g. "Korean", "English")
            context: Optional context about the writing mode (comment/reply)

        Returns:
            List of message dicts with system prompt containing translation context.
        """
        source_lang = self._config.get("translation.source_lang", "Korean")
        target_lang = self._config.get("translation.target_lang", "English")

        context_info = ""
        if context and context.mode == "comment":
            context_info = (
                f"- Writing mode: Comment on post \"{context.post_title}\" in r/{context.subreddit}\n"
            )
        elif context and context.mode == "reply":
            context_info = (
                f"- Writing mode: Reply to @{context.comment_author} in r/{context.subreddit}\n"
            )

        system_prompt = (
            f"Original ({source_lang}): {source_text}\n"
            f"Draft: {draft}\n"
            f"{context_info}\n"
            "TASK: Polish the draft translation, then explain.\n\n"
            "STRICT RULES:\n"
            "- Do NOT add words/facts not in the original\n"
            "- Keep meaning intact, only improve expression\n"
            f"- Translation must be {target_lang} ONLY\n"
            "- Preserve emoticons but convert to target language (e.g. ㅋㅋㅋ→lol, ㅠㅠ→T_T)\n\n"
            "OUTPUT FORMAT - FOLLOW EXACTLY:\n"
            "- Start with the translation DIRECTLY (no 'Here is', no labels, no intro)\n"
            "- Then %%% on a new line\n"
            f"- Then explanation in {comment_lang}, 2-3 sentences\n\n"
            "WRONG: 'Here is the translation: Hello'\n"
            "CORRECT: 'Hello'\n\n"
            "For follow-up: same format (translation + %%% + explanation)"
        )
        return [{"role": "system", "content": system_prompt}]

    def refine(
        self, messages: list[dict], stream: bool = True
    ) -> Iterator[str]:
        """Generate refine chat response using persona model.

        Args:
            messages: Full conversation history including system prompt
            stream: Whether to stream tokens

        Yields:
            Generated tokens

        Raises:
            OllamaNotRunningError, ModelNotFoundError, LLMTimeoutError
        """
        yield from self._llm.chat(
            messages=messages,
            model=self._config.get("llm.models.persona.name", ""),
            num_ctx=self._config.get("llm.models.persona.num_ctx", 8192),
            temperature=self._config.get("llm.models.persona.temperature", 0.7),
            stream=stream,
        )

    @staticmethod
    def _build_draft_prompt(source_text: str, target_lang: str = "English") -> str:
        """Build the drafting (literal translation) prompt."""
        return (
            f"Translate to natural {target_lang}. Match the original tone exactly.\n"
            "\n"
            "STRICT OUTPUT RULES:\n"
            "- Output ONLY the translated text\n"
            "- STOP immediately after the translation\n"
            "- Do NOT write anything else: no greetings, no offers, "
            "no explanations, no commentary\n"
            "- If you add ANYTHING beyond the translation, you have FAILED\n"
            "\n"
            "Translation rules:\n"
            "- Keep action verbs exact\n"
            "- Don't add or omit ANY details\n"
            "- Convert emoticons to target language equivalents "
            "(e.g. ㅋㅋㅋ→lol, ㅎㅎ→haha, ㅠㅠ→T_T, ㄷㄷ→whoa)\n"
            "\n"
            f"Text to translate:\n{source_text}\n\n"
            f"{target_lang}:"
        )
