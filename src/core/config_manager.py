"""Thread-safe singleton configuration manager for ReddiScribe."""

import logging
import threading
from pathlib import Path
from typing import Any

import yaml

from src.core.exceptions import ConfigError

logger = logging.getLogger(__name__)


# Default configuration template
DEFAULT_CONFIG = {
    "app": {
        "locale": "ko_KR",
        "theme": "dark",
        "version": "1.0.0",
        "log_level": "INFO",
    },
    "llm": {
        "default_provider": "ollama",
        "providers": {
            "ollama": {
                "host": "http://localhost:11434",
                "timeout": 120,
            }
        },
        "models": {
            "logic": {"name": "gemma2:9b", "num_ctx": 8192},
            "persona": {"name": "llama3.1:70b", "num_ctx": 8192},
            "summary": {"name": "llama3.1:8b", "num_ctx": 8192},
        },
        "generation": {
            "temperature": 0.7,
            "max_tokens": 4096,
        },
    },
    "reddit": {
        "subreddits": ["python", "programming", "learnpython"],
        "request_interval_sec": 6,
        "max_retries": 3,
        "mock_mode": False,
    },
    "data": {
        "db_path": "db/history.db",
    },
    "security": {
        "mask_logs": True,
    },
}


class ConfigManager:
    """Thread-safe singleton configuration manager.

    Manages application configuration with:
    - Singleton pattern ensuring only one instance exists
    - Thread-safe operations using RLock
    - Automatic settings.yaml creation if missing
    - Dot-notation key access (e.g., "app.locale")
    - Validation rules for critical settings
    """

    _instance = None
    _lock = threading.RLock()

    def __new__(cls):
        """Ensure singleton instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize configuration manager."""
        # Prevent re-initialization
        if hasattr(self, '_initialized'):
            return

        with self._lock:
            if hasattr(self, '_initialized'):
                return

            # Path resolution
            self.PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
            self.CONFIG_PATH = self.PROJECT_ROOT / "config" / "settings.yaml"

            # Internal state
            self._config = {}
            self._instance_lock = threading.RLock()

            # Load or create configuration
            self._load_or_create_config()

            self._initialized = True

    def _load_or_create_config(self):
        """Load settings.yaml or create it from defaults."""
        if self.CONFIG_PATH.exists():
            try:
                with open(self.CONFIG_PATH, 'r', encoding='utf-8') as f:
                    self._config = yaml.safe_load(f) or {}
                logger.info(f"Loaded configuration from {self.CONFIG_PATH}")
            except yaml.YAMLError as e:
                logger.error(f"Failed to parse YAML at {self.CONFIG_PATH}: {e}")
                logger.warning("Using DEFAULT_CONFIG due to parse error")
                self._config = self._deep_copy(DEFAULT_CONFIG)
            except Exception as e:
                logger.error(f"Unexpected error loading config: {e}")
                logger.warning("Using DEFAULT_CONFIG")
                self._config = self._deep_copy(DEFAULT_CONFIG)
        else:
            # Create config directory and write default config
            logger.info(f"Config file not found at {self.CONFIG_PATH}")
            self.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            self._config = self._deep_copy(DEFAULT_CONFIG)
            self.save()
            logger.info(f"Created default configuration at {self.CONFIG_PATH}")

    def get(self, key: str, default=None) -> Any:
        """Get configuration value using dot-notation key.

        Args:
            key: Dot-separated key path (e.g., "app.locale")
            default: Value to return if key not found

        Returns:
            Configuration value or default

        Example:
            >>> config.get("app.locale")
            'ko_KR'
            >>> config.get("llm.models.logic.name")
            'gemma2:9b'
        """
        with self._instance_lock:
            parts = key.split('.')
            value = self._config

            for part in parts:
                if isinstance(value, dict) and part in value:
                    value = value[part]
                else:
                    return default

            return value

    def set(self, key: str, value: Any) -> None:
        """Set configuration value using dot-notation key.

        Note: This does NOT save to disk. Use save() to persist changes.

        Args:
            key: Dot-separated key path (e.g., "app.locale")
            value: Value to set

        Example:
            >>> config.set("app.locale", "en_US")
            >>> config.save()
        """
        with self._instance_lock:
            parts = key.split('.')
            target = self._config

            # Navigate to parent
            for part in parts[:-1]:
                if part not in target:
                    target[part] = {}
                target = target[part]

            # Set final value
            target[parts[-1]] = value

    def update(self, changes: dict) -> None:
        """Batch update configuration from flat dict of dot-notation keys.

        Applies validation rules and saves to disk once after all updates.

        Args:
            changes: Dict with dot-notation keys as keys

        Example:
            >>> config.update({
            ...     "app.locale": "en_US",
            ...     "llm.generation.temperature": 0.5
            ... })

        Validation Rules:
            - app.locale: must be "ko_KR" or "en_US"
            - reddit.request_interval_sec: minimum 3
            - llm.generation.temperature: 0.0-2.0
            - llm.providers.ollama.timeout: minimum 30
        """
        with self._instance_lock:
            validated_changes = {}

            for key, value in changes.items():
                validated_value = self._validate_key_value(key, value)
                if validated_value is not None:
                    validated_changes[key] = validated_value

            # Apply all validated changes
            for key, value in validated_changes.items():
                self.set(key, value)

            # Save to disk once
            self.save()

    def _validate_key_value(self, key: str, value: Any) -> Any:
        """Apply validation rules to key-value pair.

        Args:
            key: Dot-notation key
            value: Value to validate

        Returns:
            Validated value or None if invalid (will be ignored)
        """
        # app.locale validation
        if key == "app.locale":
            if value not in ["ko_KR", "en_US"]:
                logger.warning(f"Invalid locale '{value}'. Must be 'ko_KR' or 'en_US'. Ignoring.")
                return None
            return value

        # reddit.request_interval_sec validation
        if key == "reddit.request_interval_sec":
            try:
                interval = int(value)
                if interval < 3:
                    logger.warning(f"request_interval_sec {interval} < 3. Forcing to 3.")
                    return 3
                return interval
            except (TypeError, ValueError):
                logger.warning(f"Invalid request_interval_sec '{value}'. Must be int. Ignoring.")
                return None

        # llm.generation.temperature validation
        if key == "llm.generation.temperature":
            try:
                temp = float(value)
                if not (0.0 <= temp <= 2.0):
                    logger.warning(f"temperature {temp} out of range [0.0, 2.0]. Forcing to 0.7.")
                    return 0.7
                return temp
            except (TypeError, ValueError):
                logger.warning(f"Invalid temperature '{value}'. Must be float. Ignoring.")
                return None

        # llm.providers.ollama.timeout validation
        if key == "llm.providers.ollama.timeout":
            try:
                timeout = int(value)
                if timeout < 30:
                    logger.warning(f"ollama timeout {timeout} < 30. Forcing to 30.")
                    return 30
                return timeout
            except (TypeError, ValueError):
                logger.warning(f"Invalid timeout '{value}'. Must be int. Ignoring.")
                return None

        # No validation needed for this key
        return value

    def save(self) -> None:
        """Write current configuration to settings.yaml.

        Thread-safe operation that persists in-memory config to disk.
        """
        with self._instance_lock:
            try:
                self.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
                with open(self.CONFIG_PATH, 'w', encoding='utf-8') as f:
                    yaml.safe_dump(self._config, f, default_flow_style=False, sort_keys=False)
                logger.debug(f"Saved configuration to {self.CONFIG_PATH}")
            except Exception as e:
                logger.error(f"Failed to save configuration: {e}")
                raise ConfigError(f"Failed to save configuration: {e}")

    def get_db_path(self) -> Path:
        """Get absolute database path.

        Returns:
            Absolute path: PROJECT_ROOT / data.db_path

        Example:
            >>> config.get_db_path()
            PosixPath('/path/to/ReddiScribe/db/history.db')
        """
        with self._instance_lock:
            relative_db_path = self.get("data.db_path", "db/history.db")
            return self.PROJECT_ROOT / relative_db_path

    @classmethod
    def reset(cls) -> None:
        """Reset singleton instance (for testing)."""
        with cls._lock:
            cls._instance = None

    @staticmethod
    def _deep_copy(obj):
        """Create a deep copy of nested dict/list structures.

        Args:
            obj: Object to copy (dict, list, or primitive)

        Returns:
            Deep copy of the object
        """
        if isinstance(obj, dict):
            return {k: ConfigManager._deep_copy(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [ConfigManager._deep_copy(item) for item in obj]
        else:
            return obj
