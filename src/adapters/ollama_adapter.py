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


def format_model_size(size_bytes: int) -> str:
    """Format model size in bytes to human-readable string.

    Args:
        size_bytes: Model size in bytes

    Returns:
        Formatted size string:
        - ">= 1GB": "X.X GB" (one decimal)
        - "< 1GB but > 0": "XXX MB" (no decimal)
        - "0": ""
    """
    if size_bytes == 0:
        return ""
    elif size_bytes >= 1_073_741_824:  # >= 1GB
        size_gb = size_bytes / 1_073_741_824
        return f"{size_gb:.1f} GB"
    else:  # < 1GB but > 0
        size_mb = size_bytes / 1_048_576
        return f"{int(size_mb)} MB"


class OllamaAdapter(LLMAdapter):
    """LLM adapter using Ollama's REST API.

    Endpoint: POST {host}/api/generate
    Streaming: Each line is a JSON object {"response": "token", "done": false}
    """

    def __init__(self, host: str = "http://localhost:11434", timeout: int = 120):
        self._host = host.rstrip("/")
        self._timeout = timeout
        self._generate_url = f"{self._host}/api/generate"
        self._tags_url = f"{self._host}/api/tags"
        self._chat_url = f"{self._host}/api/chat"
        self._used_models: set[str] = set()

    def list_models(self) -> list[str]:
        """List available models from Ollama.

        Returns:
            List of model names. Returns empty list if Ollama is unreachable
            or if the response is malformed.

        Note:
            Uses a 5-second timeout (shorter than generation timeout).
            Handles both 'name' and 'model' fields for Ollama version compatibility.
        """
        try:
            response = requests.get(self._tags_url, timeout=5)

            if response.status_code != 200:
                logger.warning(
                    f"Failed to list models: HTTP {response.status_code}"
                )
                return []

            data = response.json()
            models = data.get("models", [])

            if not isinstance(models, list):
                logger.warning("Malformed response: 'models' is not a list")
                return []

            # Extract model names - handle both 'name' and 'model' fields
            model_names = []
            for model in models:
                if not isinstance(model, dict):
                    continue
                name = model.get("name") or model.get("model")
                if name:
                    model_names.append(name)

            return model_names

        except requests.ConnectionError:
            logger.debug(f"Cannot connect to Ollama at {self._host}")
            return []
        except requests.Timeout:
            logger.debug(f"Timeout listing models from {self._host}")
            return []
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to parse models response: {e}")
            return []
        except Exception as e:
            logger.warning(f"Unexpected error listing models: {e}")
            return []

    def list_models_with_size(self) -> list[dict]:
        """List available models from Ollama with their sizes.

        Returns:
            List of dicts with {"name": str, "size": int}.
            Returns empty list if Ollama is unreachable or response is malformed.
        """
        try:
            response = requests.get(self._tags_url, timeout=5)

            if response.status_code != 200:
                logger.warning(
                    f"Failed to list models: HTTP {response.status_code}"
                )
                return []

            data = response.json()
            models = data.get("models", [])

            if not isinstance(models, list):
                logger.warning("Malformed response: 'models' is not a list")
                return []

            # Extract model names and sizes
            models_with_size = []
            for model in models:
                if not isinstance(model, dict):
                    continue
                name = model.get("name") or model.get("model")
                size = model.get("size", 0)
                if name:
                    models_with_size.append({"name": name, "size": size})

            return models_with_size

        except requests.ConnectionError:
            logger.debug(f"Cannot connect to Ollama at {self._host}")
            return []
        except requests.Timeout:
            logger.debug(f"Timeout listing models from {self._host}")
            return []
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to parse models response: {e}")
            return []
        except Exception as e:
            logger.warning(f"Unexpected error listing models: {e}")
            return []

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
        self._used_models.add(model)

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

    def chat(
        self,
        messages: list[dict],
        model: str,
        num_ctx: int = 8192,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        stream: bool = True,
    ) -> Iterator[str]:
        """Chat completion via Ollama /api/chat endpoint.

        Uses message history for multi-turn conversations.
        Streaming response format: {"message": {"role": "assistant", "content": "token"}, "done": false}
        """
        self._used_models.add(model)

        payload = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "options": {
                "num_ctx": num_ctx,
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        try:
            response = requests.post(
                self._chat_url,
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

        if response.status_code == 404:
            raise ModelNotFoundError(f"Model not found: {model}")
        if response.status_code != 200:
            try:
                error_data = response.json()
                error_msg = error_data.get("error", response.text)
            except (json.JSONDecodeError, ValueError):
                error_msg = response.text

            if "not found" in error_msg.lower():
                raise ModelNotFoundError(f"Model not found: {model}")
            raise OllamaNotRunningError(f"Ollama API error ({response.status_code}): {error_msg}")

        if stream:
            return self._stream_chat_response(response)
        else:
            return self._non_stream_chat_response(response)

    def _stream_chat_response(self, response: requests.Response) -> Iterator[str]:
        """Parse streaming chat response. Format: {"message": {"content": "token"}, "done": false}"""
        try:
            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse chat streaming line: {line}")
                    continue

                message = data.get("message", {})
                token = message.get("content", "")
                if token:
                    yield token

                if data.get("done", False):
                    break
        except requests.exceptions.ChunkedEncodingError as e:
            logger.error(f"Chat stream interrupted: {e}")
            raise LLMTimeoutError(f"Stream interrupted: {e}")
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection lost during chat streaming: {e}")
            raise OllamaNotRunningError(f"Connection lost: {e}")

    def _non_stream_chat_response(self, response: requests.Response) -> Iterator[str]:
        """Parse non-streaming chat response."""
        try:
            data = response.json()
            message = data.get("message", {})
            text = message.get("content", "")
            if text:
                yield text
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Failed to parse non-streaming chat response: {e}")
            raise OllamaNotRunningError(f"Invalid response from Ollama: {e}")

    def unload_models(self):
        """Unload all models used during this session from VRAM."""
        for model in self._used_models:
            try:
                requests.post(
                    self._generate_url,
                    json={"model": model, "keep_alive": 0},
                    timeout=5,
                )
                logger.info(f"Unloaded model: {model}")
            except Exception as e:
                logger.debug(f"Failed to unload {model}: {e}")
        self._used_models.clear()

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
