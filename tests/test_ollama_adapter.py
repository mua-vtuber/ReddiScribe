"""Tests for OllamaAdapter."""

import json
import pytest
from unittest.mock import patch, MagicMock

import requests

from src.adapters.ollama_adapter import OllamaAdapter, format_model_size
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


class TestOllamaAdapterListModels:
    """Test model listing."""

    @patch("requests.get")
    def test_list_models_returns_model_names(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "models": [
                {"name": "llama3.1:8b", "size": 4700000000},
                {"name": "ko-gemma-2:q8", "size": 5400000000},
            ]
        }
        mock_get.return_value = resp

        adapter = OllamaAdapter()
        models = adapter.list_models()

        assert models == ["llama3.1:8b", "ko-gemma-2:q8"]
        mock_get.assert_called_once_with("http://localhost:11434/api/tags", timeout=5)

    @patch("requests.get")
    def test_list_models_handles_model_field(self, mock_get):
        """Test compatibility with 'model' field instead of 'name'."""
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "models": [
                {"model": "llama3.1:8b"},
            ]
        }
        mock_get.return_value = resp

        adapter = OllamaAdapter()
        models = adapter.list_models()

        assert models == ["llama3.1:8b"]

    @patch("requests.get")
    def test_list_models_empty_when_no_models(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"models": []}
        mock_get.return_value = resp

        adapter = OllamaAdapter()
        assert adapter.list_models() == []

    @patch("requests.get")
    def test_list_models_empty_on_connection_error(self, mock_get):
        mock_get.side_effect = requests.ConnectionError("Connection refused")

        adapter = OllamaAdapter()
        assert adapter.list_models() == []

    @patch("requests.get")
    def test_list_models_empty_on_timeout(self, mock_get):
        mock_get.side_effect = requests.Timeout("Timed out")

        adapter = OllamaAdapter()
        assert adapter.list_models() == []

    @patch("requests.get")
    def test_list_models_empty_on_http_error(self, mock_get):
        resp = MagicMock()
        resp.status_code = 500
        mock_get.return_value = resp

        adapter = OllamaAdapter()
        assert adapter.list_models() == []

    @patch("requests.get")
    def test_list_models_empty_on_malformed_json(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.side_effect = json.JSONDecodeError("Bad JSON", "", 0)
        mock_get.return_value = resp

        adapter = OllamaAdapter()
        assert adapter.list_models() == []

    @patch("requests.get")
    def test_list_models_uses_custom_host(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"models": [{"name": "test:latest"}]}
        mock_get.return_value = resp

        adapter = OllamaAdapter(host="http://myhost:8080")
        adapter.list_models()

        mock_get.assert_called_once_with("http://myhost:8080/api/tags", timeout=5)


def mock_chat_streaming_response(tokens: list[str], status_code: int = 200):
    """Create a mock response that simulates Ollama /api/chat streaming.

    Each token becomes: {"message": {"role": "assistant", "content": "token"}, "done": false}
    """
    lines = []
    for i, token in enumerate(tokens):
        is_last = (i == len(tokens) - 1)
        lines.append(json.dumps({
            "message": {"role": "assistant", "content": token},
            "done": is_last,
        }))

    resp = MagicMock()
    resp.status_code = status_code
    resp.iter_lines.return_value = iter(lines)
    resp.headers = {"Content-Type": "application/json"}
    return resp


class TestOllamaAdapterChat:
    """Test chat completion via /api/chat endpoint."""

    @patch("requests.post")
    def test_chat_streaming_yields_tokens(self, mock_post):
        mock_post.return_value = mock_chat_streaming_response(["Hello", " ", "world"])

        adapter = OllamaAdapter()
        tokens = list(adapter.chat(
            [{"role": "user", "content": "test"}],
            "llama3.1:8b",
        ))

        assert tokens == ["Hello", " ", "world"]

    @patch("requests.post")
    def test_chat_sends_correct_payload(self, mock_post):
        mock_post.return_value = mock_chat_streaming_response(["ok"])

        adapter = OllamaAdapter()
        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "hi"},
        ]
        list(adapter.chat(messages, "llama3.1:8b", num_ctx=4096, temperature=0.5))

        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert payload["model"] == "llama3.1:8b"
        assert payload["messages"] == messages
        assert payload["options"]["num_ctx"] == 4096
        assert payload["options"]["temperature"] == 0.5

    @patch("requests.post")
    def test_chat_posts_to_chat_url(self, mock_post):
        mock_post.return_value = mock_chat_streaming_response(["ok"])

        adapter = OllamaAdapter()
        list(adapter.chat([{"role": "user", "content": "test"}], "llama3.1:8b"))

        call_args = mock_post.call_args
        # URL is the first positional argument to requests.post()
        url = call_args[0][0]
        assert "api/chat" in url

    @patch("requests.post")
    def test_chat_connection_error(self, mock_post):
        mock_post.side_effect = requests.ConnectionError("refused")

        adapter = OllamaAdapter()
        with pytest.raises(OllamaNotRunningError):
            list(adapter.chat([{"role": "user", "content": "test"}], "llama3.1:8b"))

    @patch("requests.post")
    def test_chat_timeout(self, mock_post):
        mock_post.side_effect = requests.Timeout("timeout")

        adapter = OllamaAdapter()
        with pytest.raises(LLMTimeoutError):
            list(adapter.chat([{"role": "user", "content": "test"}], "llama3.1:8b"))

    @patch("requests.post")
    def test_chat_model_not_found(self, mock_post):
        mock_post.return_value = mock_error_response(404, "not found")

        adapter = OllamaAdapter()
        with pytest.raises(ModelNotFoundError):
            list(adapter.chat([{"role": "user", "content": "test"}], "bad:model"))

    @patch("requests.post")
    def test_chat_tracks_used_models(self, mock_post):
        mock_post.return_value = mock_chat_streaming_response(["ok"])

        adapter = OllamaAdapter()
        list(adapter.chat([{"role": "user", "content": "test"}], "llama3.1:70b"))

        assert "llama3.1:70b" in adapter._used_models

    @patch("requests.post")
    def test_chat_non_streaming(self, mock_post):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "message": {"role": "assistant", "content": "Full response"},
            "done": True,
        }
        mock_post.return_value = resp

        adapter = OllamaAdapter()
        result = list(adapter.chat(
            [{"role": "user", "content": "test"}],
            "llama3.1:8b",
            stream=False,
        ))

        assert result == ["Full response"]


class TestFormatModelSize:
    """Test format_model_size utility."""

    def test_gb_format(self):
        assert format_model_size(5_400_000_000) == "5.0 GB"

    def test_gb_format_decimal(self):
        assert format_model_size(4_700_000_000) == "4.4 GB"

    def test_exact_1gb(self):
        assert format_model_size(1_073_741_824) == "1.0 GB"

    def test_mb_format(self):
        assert format_model_size(500_000_000) == "476 MB"

    def test_small_mb(self):
        result = format_model_size(50_000_000)
        assert "MB" in result

    def test_zero_returns_empty(self):
        assert format_model_size(0) == ""


class TestOllamaAdapterListModelsWithSize:
    """Test list_models_with_size method."""

    @patch("requests.get")
    def test_returns_name_and_size(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "models": [
                {"name": "gemma2:9b", "size": 5400000000},
                {"name": "llama3.1:8b", "size": 4700000000},
            ]
        }
        mock_get.return_value = resp

        adapter = OllamaAdapter()
        models = adapter.list_models_with_size()

        assert len(models) == 2
        assert models[0] == {"name": "gemma2:9b", "size": 5400000000}
        assert models[1] == {"name": "llama3.1:8b", "size": 4700000000}

    @patch("requests.get")
    def test_missing_size_defaults_to_zero(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "models": [
                {"name": "test:latest"},
            ]
        }
        mock_get.return_value = resp

        adapter = OllamaAdapter()
        models = adapter.list_models_with_size()

        assert models == [{"name": "test:latest", "size": 0}]

    @patch("requests.get")
    def test_empty_on_connection_error(self, mock_get):
        mock_get.side_effect = requests.ConnectionError("refused")

        adapter = OllamaAdapter()
        assert adapter.list_models_with_size() == []

    @patch("requests.get")
    def test_empty_on_http_error(self, mock_get):
        resp = MagicMock()
        resp.status_code = 500
        mock_get.return_value = resp

        adapter = OllamaAdapter()
        assert adapter.list_models_with_size() == []

    @patch("requests.get")
    def test_handles_model_field(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "models": [
                {"model": "test:latest", "size": 1000000000},
            ]
        }
        mock_get.return_value = resp

        adapter = OllamaAdapter()
        models = adapter.list_models_with_size()

        assert models == [{"name": "test:latest", "size": 1000000000}]
