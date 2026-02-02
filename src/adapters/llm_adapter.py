"""Abstract base class for LLM access."""

from abc import ABC, abstractmethod
from typing import Iterator


class LLMAdapter(ABC):
    """Abstract interface for LLM text generation."""

    @abstractmethod
    def generate(
        self,
        prompt: str,
        model: str,
        num_ctx: int = 8192,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stream: bool = True,
    ) -> Iterator[str]:
        """Generate text from a prompt.

        Args:
            prompt: The input prompt text
            model: Model name (e.g., "llama3.1:8b")
            num_ctx: Context window size
            temperature: Sampling temperature (0.0-2.0)
            max_tokens: Maximum tokens to generate (mapped to num_predict for Ollama)
            stream: Whether to stream tokens

        Yields:
            Generated text tokens (if streaming) or full text in one yield

        Raises:
            OllamaNotRunningError: Service not reachable
            ModelNotFoundError: Model not available
            LLMTimeoutError: Request timed out
        """
        ...
