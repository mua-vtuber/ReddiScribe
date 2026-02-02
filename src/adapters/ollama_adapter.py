"""Ollama REST API adapter with streaming and exception-based error handling."""

import json
import logging
from typing import Iterator

import requests

from src.adapters.llm_adapter import LLMAdapter
from src.core.exceptions import (
    OllamaNotRunningError,
    ModelNotFoundError,
    LLMTimeoutError,
)

logger = logging.getLogger("reddiscribe")


class OllamaAdapter(LLMAdapter):
    """LLM adapter using Ollama's REST API.

    Endpoint: POST {host}/api/generate
    Streaming: Each line is a JSON object {"response": "token", "done": false}
    """

    def __init__(self, host: str = "http://localhost:11434", timeout: int = 120):
        self._host = host.rstrip("/")
        self._timeout = timeout
        self._generate_url = f"{self._host}/api/generate"

    def generate(
        self,
        prompt: str,
        model: str,
        num_ctx: int = 8192,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stream: bool = True,
    ) -> Iterator[str]:
        """Generate text via Ollama API.

        Config's max_tokens maps to Ollama's num_predict parameter.
        """
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": stream,
            "options": {
                "num_ctx": num_ctx,
                "temperature": temperature,
                "num_predict": max_tokens,  # max_tokens -> num_predict mapping
            },
        }

        try:
            response = requests.post(
                self._generate_url,
                json=payload,
                stream=stream,
                timeout=self._timeout,
            )
        except requests.ConnectionError:
            raise OllamaNotRunningError(
                f"Cannot connect to Ollama at {self._host}. Is Ollama running?"
            )
        except requests.Timeout:
            raise LLMTimeoutError(
                f"Ollama request timed out after {self._timeout}s"
            )
        except requests.RequestException as e:
            raise OllamaNotRunningError(f"Failed to connect to Ollama: {e}")

        # Handle HTTP errors
        if response.status_code == 404:
            raise ModelNotFoundError(f"Model not found: {model}")
        if response.status_code != 200:
            # Try to extract error message from response
            try:
                error_data = response.json()
                error_msg = error_data.get("error", response.text)
            except (json.JSONDecodeError, ValueError):
                error_msg = response.text

            # Check if it's a model-not-found error in the response body
            if "not found" in error_msg.lower():
                raise ModelNotFoundError(f"Model not found: {model}")
            raise OllamaNotRunningError(f"Ollama API error ({response.status_code}): {error_msg}")

        if stream:
            return self._stream_response(response)
        else:
            return self._non_stream_response(response)

    def _stream_response(self, response: requests.Response) -> Iterator[str]:
        """Parse streaming response. Each line is a JSON object."""
        try:
            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse streaming line: {line}")
                    continue

                token = data.get("response", "")
                if token:
                    yield token

                if data.get("done", False):
                    break
        except requests.exceptions.ChunkedEncodingError as e:
            logger.error(f"Stream interrupted: {e}")
            raise LLMTimeoutError(f"Stream interrupted: {e}")
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection lost during streaming: {e}")
            raise OllamaNotRunningError(f"Connection lost: {e}")

    def _non_stream_response(self, response: requests.Response) -> Iterator[str]:
        """Parse non-streaming response. Returns full text in one yield."""
        try:
            data = response.json()
            text = data.get("response", "")
            if text:
                yield text
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Failed to parse non-streaming response: {e}")
            raise OllamaNotRunningError(f"Invalid response from Ollama: {e}")
