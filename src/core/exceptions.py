"""Custom exception hierarchy for ReddiScribe."""


class ReddiScribeError(Exception):
    """Base exception for all ReddiScribe errors."""

    def __init__(self, message: str = "An error occurred in ReddiScribe"):
        self.message = message
        super().__init__(self.message)


class NetworkError(ReddiScribeError):
    """Base exception for network-related errors."""

    def __init__(self, message: str = "A network error occurred"):
        super().__init__(message)


class RedditFetchError(NetworkError):
    """Error fetching data from Reddit."""

    def __init__(self, message: str = "Failed to fetch data from Reddit"):
        super().__init__(message)


class RateLimitError(NetworkError):
    """HTTP 429 - Rate limit exceeded."""

    def __init__(self, message: str = "Reddit API rate limit exceeded"):
        super().__init__(message)


class SubredditNotFoundError(NetworkError):
    """HTTP 404 - Subreddit does not exist."""

    def __init__(self, message: str = "Subreddit not found"):
        super().__init__(message)


class SubredditPrivateError(NetworkError):
    """HTTP 403 - Subreddit is private or restricted."""

    def __init__(self, message: str = "Subreddit is private or restricted"):
        super().__init__(message)


class LLMError(ReddiScribeError):
    """Base exception for LLM-related errors."""

    def __init__(self, message: str = "An LLM error occurred"):
        super().__init__(message)


class OllamaNotRunningError(LLMError):
    """Ollama service is not running."""

    def __init__(self, message: str = "Ollama service is not running"):
        super().__init__(message)


class ModelNotFoundError(LLMError):
    """Requested model is not available."""

    def __init__(self, message: str = "Requested model not found"):
        super().__init__(message)


class LLMTimeoutError(LLMError):
    """LLM request timed out."""

    def __init__(self, message: str = "LLM request timed out"):
        super().__init__(message)


class DataError(ReddiScribeError):
    """Base exception for data-related errors."""

    def __init__(self, message: str = "A data error occurred"):
        super().__init__(message)


class DatabaseError(DataError):
    """Database operation failed."""

    def __init__(self, message: str = "Database operation failed"):
        super().__init__(message)


class ConfigError(DataError):
    """Configuration is invalid or missing."""

    def __init__(self, message: str = "Configuration error"):
        super().__init__(message)
