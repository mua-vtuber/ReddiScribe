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
            model=self._config.get("llm.models.logic.name", ""),
            num_ctx=self._config.get("llm.models.logic.num_ctx", 8192),
            temperature=self._config.get("llm.models.logic.temperature", 0.3),
            stream=stream,
        )

    def polish(self, english_draft: str, context: WriterContext = None, stream: bool = True) -> Iterator[str]:
        """Stage 2: English draft -> Reddit-ready English using persona model.

        Args:
            english_draft: Stage 1 output (English draft)
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

        full_prompt = (
            "The following style instructions may be in any language, "
            "but you MUST always output in English.\n\n"
            f"Style instructions:\n{persona_prompt}\n"
            f"{context_instructions}\n"
            f"Original English:\n{english_draft}\n\n"
            "Rewrite the above text following the style instructions. "
            "Output ONLY in English."
        )

        yield from self._llm.generate(
            prompt=full_prompt,
            model=self._config.get("llm.models.persona.name", ""),
            num_ctx=self._config.get("llm.models.persona.num_ctx", 8192),
            temperature=self._config.get("llm.models.persona.temperature", 0.7),
            stream=stream,
        )

    def build_refine_context(
        self, korean_text: str, draft: str, polished: str,
        comment_lang: str = "Korean",
        context: WriterContext = None,
    ) -> list[dict]:
        """Build initial chat context for refine conversation.

        Args:
            korean_text: Original Korean input
            draft: Stage 1 draft output
            polished: Stage 2 polished output
            comment_lang: Language for AI comments (e.g. "Korean", "English")
            context: Optional context about the writing mode (comment/reply)

        Returns:
            List of message dicts with system prompt containing translation context.
        """
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
            "You are a translation refinement assistant. "
            "The user translated Korean text to English.\n\n"
            f"Context:\n"
            f"- Original Korean: {korean_text}\n"
            f"- Stage 1 Draft: {draft}\n"
            f"- Stage 2 Final: {polished}\n"
            f"{context_info}\n"
            "Your role:\n"
            "- Help the user refine the English translation through conversation\n"
            "- The user may write in any language - understand their feedback "
            "and apply it to the English translation\n"
            "- When you suggest a revised translation, "
            "wrap it in [TRANSLATION]...[/TRANSLATION] tags\n"
            "- Keep your comments concise and helpful\n"
            f"- ALWAYS write your comments and explanations in {comment_lang}\n"
            "- Output translations always in English inside the tags"
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
    def _build_draft_prompt(korean_text: str, target_lang: str = "English") -> str:
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
            "- Replace Korean emoticons with English equivalents "
            "(e.g. ㅋㅋㅋ→lol, ㅎㅎ→haha, ㅠㅠ→T_T, ㄷㄷ→whoa)\n"
            "\n"
            "Example:\n"
            "Korean: 번역해서 레딧을 보고 있어요\n"
            "English: I'm checking out Reddit (by translating)\n"
            "\n"
            f"Korean: {korean_text}\n"
            f"{target_lang}:"
        )
