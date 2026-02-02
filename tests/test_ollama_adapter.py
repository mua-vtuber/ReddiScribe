"""Tests for OllamaAdapter."""

import json
import pytest
from unittest.mock import patch, MagicMock

import requests

from src.adapters.ollama_adapter import OllamaAdapter
from src.core.exceptions import (
    OllamaNotRunningError,
    ModelNotFoundError,
    LLMTimeoutError,
)


def mock_streaming_response(tokens: list[str], status_code: int = 200):
    """Create a mock response that simulates Ollama streaming.

    Each token becomes a JSON line: {"response": "token", "done": false}
    Last line has done=true.
    """
    lines = []
    for i, token in enumerate(tokens):
        is_last = (i == len(tokens) - 1)
        lines.append(json.dumps({"response": token, "done": is_last}))

    resp = MagicMock()
    resp.status_code = status_code
    resp.iter_lines.return_value = iter(lines)
    resp.headers = {"Content-Type": "application/json"}
    return resp


def mock_non_stream_response(text: str, status_code: int = 200):
    """Create a mock non-streaming response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {"response": text, "done": True}
    resp.headers = {"Content-Type": "application/json"}
    return resp


def mock_error_response(status_code: int, error_msg: str = "error"):
    """Create a mock error response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {"error": error_msg}
    resp.text = error_msg
    resp.headers = {"Content-Type": "application/json"}
    return resp


class TestOllamaAdapterInit:
    """Test adapter initialization."""

    def test_default_host(self):
        adapter = OllamaAdapter()
        assert adapter._host == "http://localhost:11434"
        assert adapter._generate_url == "http://localhost:11434/api/generate"

    def test_custom_host_strips_trailing_slash(self):
        adapter = OllamaAdapter(host="http://myhost:11434/")
        assert adapter._host == "http://myhost:11434"

    def test_custom_timeout(self):
        adapter = OllamaAdapter(timeout=300)
        assert adapter._timeout == 300


class TestOllamaAdapterStreaming:
    """Test streaming generation."""

    @patch("requests.post")
    def test_streaming_yields_tokens(self, mock_post):
        mock_post.return_value = mock_streaming_response(["Hello", " ", "world", "!"])

        adapter = OllamaAdapter()
        tokens = list(adapter.generate("test prompt", "llama3.1:8b"))

        assert tokens == ["Hello", " ", "world", "!"]

    @patch("requests.post")
    def test_streaming_sends_correct_payload(self, mock_post):
        mock_post.return_value = mock_streaming_response(["ok"])

        adapter = OllamaAdapter(host="http://localhost:11434")
        list(adapter.generate(
            prompt="test",
            model="llama3.1:8b",
            num_ctx=4096,
            temperature=0.5,
            max_tokens=2048,
            stream=True,
        ))

        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert payload["model"] == "llama3.1:8b"
        assert payload["prompt"] == "test"
        assert payload["stream"] is True
        assert payload["options"]["num_ctx"] == 4096
        assert payload["options"]["temperature"] == 0.5
        assert payload["options"]["num_predict"] == 2048  # max_tokens -> num_predict

    @patch("requests.post")
    def test_streaming_skips_empty_lines(self, mock_post):
        """Test that empty lines in streaming response are skipped."""
        lines = [
            json.dumps({"response": "Hello", "done": False}),
            "",  # empty line should be skipped
            json.dumps({"response": "World", "done": True}),
        ]

        resp = MagicMock()
        resp.status_code = 200
        resp.iter_lines.return_value = iter(lines)
        mock_post.return_value = resp

        adapter = OllamaAdapter()
        tokens = list(adapter.generate("test", "llama3.1:8b"))

        assert tokens == ["Hello", "World"]

    @patch("requests.post")
    def test_streaming_handles_malformed_json(self, mock_post):
        """Test that malformed JSON lines are logged and skipped."""
        lines = [
            json.dumps({"response": "Good", "done": False}),
            "{bad json",  # malformed
            json.dumps({"response": "End", "done": True}),
        ]

        resp = MagicMock()
        resp.status_code = 200
        resp.iter_lines.return_value = iter(lines)
        mock_post.return_value = resp

        adapter = OllamaAdapter()
        tokens = list(adapter.generate("test", "llama3.1:8b"))

        # Only valid tokens should be returned
        assert tokens == ["Good", "End"]

    @patch("requests.post")
    def test_streaming_stops_on_done_flag(self, mock_post):
        """Test that streaming stops when done=true is received."""
        lines = [
            json.dumps({"response": "First", "done": False}),
            json.dumps({"response": "Last", "done": True}),
            json.dumps({"response": "ShouldNotAppear", "done": False}),
        ]

        resp = MagicMock()
        resp.status_code = 200
        resp.iter_lines.return_value = iter(lines)
        mock_post.return_value = resp

        adapter = OllamaAdapter()
        tokens = list(adapter.generate("test", "llama3.1:8b"))

        assert tokens == ["First", "Last"]

    @patch("requests.post")
    def test_streaming_chunked_encoding_error(self, mock_post):
        """Test that ChunkedEncodingError during streaming raises LLMTimeoutError."""
        resp = MagicMock()
        resp.status_code = 200
        resp.iter_lines.side_effect = requests.exceptions.ChunkedEncodingError("Connection broken")
        mock_post.return_value = resp

        adapter = OllamaAdapter()
        with pytest.raises(LLMTimeoutError, match="Stream interrupted"):
            list(adapter.generate("test", "llama3.1:8b"))

    @patch("requests.post")
    def test_streaming_connection_error_during_stream(self, mock_post):
        """Test that ConnectionError during streaming raises OllamaNotRunningError."""
        resp = MagicMock()
        resp.status_code = 200
        resp.iter_lines.side_effect = requests.exceptions.ConnectionError("Connection lost")
        mock_post.return_value = resp

        adapter = OllamaAdapter()
        with pytest.raises(OllamaNotRunningError, match="Connection lost"):
            list(adapter.generate("test", "llama3.1:8b"))


class TestOllamaAdapterNonStreaming:
    """Test non-streaming generation."""

    @patch("requests.post")
    def test_non_streaming_returns_full_text(self, mock_post):
        mock_post.return_value = mock_non_stream_response("Complete response text")

        adapter = OllamaAdapter()
        result = list(adapter.generate("test", "llama3.1:8b", stream=False))

        assert result == ["Complete response text"]

    @patch("requests.post")
    def test_non_streaming_sends_correct_payload(self, mock_post):
        mock_post.return_value = mock_non_stream_response("ok")

        adapter = OllamaAdapter()
        list(adapter.generate("test", "llama3.1:8b", stream=False))

        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert payload["stream"] is False

    @patch("requests.post")
    def test_non_streaming_invalid_json_raises_error(self, mock_post):
        """Test that invalid JSON in non-streaming mode raises OllamaNotRunningError."""
        resp = MagicMock()
        resp.status_code = 200
        resp.json.side_effect = json.JSONDecodeError("Bad JSON", "", 0)
        mock_post.return_value = resp

        adapter = OllamaAdapter()
        with pytest.raises(OllamaNotRunningError, match="Invalid response from Ollama"):
            list(adapter.generate("test", "llama3.1:8b", stream=False))

    @patch("requests.post")
    def test_non_streaming_empty_response(self, mock_post):
        """Test that empty response in non-streaming mode returns empty list."""
        mock_post.return_value = mock_non_stream_response("")

        adapter = OllamaAdapter()
        result = list(adapter.generate("test", "llama3.1:8b", stream=False))

        assert result == []


class TestOllamaAdapterErrors:
    """Test error handling."""

    @patch("requests.post")
    def test_connection_error_raises_not_running(self, mock_post):
        mock_post.side_effect = requests.ConnectionError("Connection refused")

        adapter = OllamaAdapter()
        with pytest.raises(OllamaNotRunningError, match="Cannot connect to Ollama"):
            list(adapter.generate("test", "llama3.1:8b"))

    @patch("requests.post")
    def test_timeout_raises_llm_timeout(self, mock_post):
        mock_post.side_effect = requests.Timeout("Timed out")

        adapter = OllamaAdapter()
        with pytest.raises(LLMTimeoutError, match="timed out after"):
            list(adapter.generate("test", "llama3.1:8b"))

    @patch("requests.post")
    def test_404_raises_model_not_found(self, mock_post):
        mock_post.return_value = mock_error_response(404, "model not found")

        adapter = OllamaAdapter()
        with pytest.raises(ModelNotFoundError, match="Model not found"):
            list(adapter.generate("test", "nonexistent:model"))

    @patch("requests.post")
    def test_error_body_with_not_found_raises_model_not_found(self, mock_post):
        mock_post.return_value = mock_error_response(400, "model 'badmodel' not found")

        adapter = OllamaAdapter()
        with pytest.raises(ModelNotFoundError, match="Model not found"):
            list(adapter.generate("test", "badmodel"))

    @patch("requests.post")
    def test_generic_error_raises_not_running(self, mock_post):
        mock_post.return_value = mock_error_response(500, "internal server error")

        adapter = OllamaAdapter()
        with pytest.raises(OllamaNotRunningError, match="Ollama API error"):
            list(adapter.generate("test", "llama3.1:8b"))

    @patch("requests.post")
    def test_request_exception_raises_not_running(self, mock_post):
        mock_post.side_effect = requests.RequestException("Something went wrong")

        adapter = OllamaAdapter()
        with pytest.raises(OllamaNotRunningError, match="Failed to connect to Ollama"):
            list(adapter.generate("test", "llama3.1:8b"))

    @patch("requests.post")
    def test_error_response_with_invalid_json(self, mock_post):
        """Test error handling when error response has invalid JSON."""
        resp = MagicMock()
        resp.status_code = 500
        resp.json.side_effect = json.JSONDecodeError("Bad JSON", "", 0)
        resp.text = "Raw error text"
        mock_post.return_value = resp

        adapter = OllamaAdapter()
        with pytest.raises(OllamaNotRunningError, match="Raw error text"):
            list(adapter.generate("test", "llama3.1:8b"))

    @patch("requests.post")
    def test_error_response_case_insensitive_not_found(self, mock_post):
        """Test that 'not found' error detection is case-insensitive."""
        mock_post.return_value = mock_error_response(400, "Model 'test' NOT FOUND")

        adapter = OllamaAdapter()
        with pytest.raises(ModelNotFoundError):
            list(adapter.generate("test", "test"))
