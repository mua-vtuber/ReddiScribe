"""Thread-safe singleton I18nManager for loading and accessing i18n locale JSON files."""

import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict

# Path resolution
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LOCALE_DIR = PROJECT_ROOT / "src" / "resources" / "locales"

# Logger
logger = logging.getLogger("reddiscribe")


class I18nManager:
    """Thread-safe singleton manager for internationalization.

    Loads locale JSON files and provides thread-safe access to translated strings.
    Uses dot-notation keys (e.g., "reader.summary") and supports placeholder substitution.
    """

    _instance = None
    _lock = threading.RLock()

    def __new__(cls):
        """Ensure only one instance exists (singleton pattern)."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        """Initialize the manager with default locale."""
        if self._initialized:
            return
        self._data: Dict[str, Any] = {}
        self._locale: str = "ko_KR"  # default
        self._initialized = True

    def load_locale(self, locale: str) -> None:
        """Load a locale JSON file.

        Args:
            locale: Locale identifier (e.g., "ko_KR") that maps to LOCALE_DIR/{locale}.json

        Thread-safe. If file not found or JSON parse error, logs warning and keeps current data.
        """
        with self._lock:
            locale_file = LOCALE_DIR / f"{locale}.json"

            if not locale_file.exists():
                logger.warning(f"Locale file not found: {locale_file}")
                return

            try:
                with open(locale_file, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                    self._locale = locale
                logger.info(f"Loaded locale: {locale}")
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse locale file {locale_file}: {e}")
            except Exception as e:
                logger.warning(f"Failed to load locale file {locale_file}: {e}")

    def get(self, key: str, **kwargs) -> str:
        """Get translated string by dot-notation key.

        Args:
            key: Dot-separated key path (e.g., "reader.summary")
            **kwargs: Placeholder values for {placeholder} substitution

        Returns:
            Translated string with placeholders substituted, or the key itself if not found.

        Thread-safe. Never raises exceptions.

        Examples:
            get("reader.summary") -> "AI 요약"
            get("errors.model_not_found", model="llama3") -> "모델을 찾을 수 없습니다: llama3"
        """
        with self._lock:
            template = self._resolve(key)

            if not kwargs:
                return template

            try:
                return template.format_map(kwargs)
            except (KeyError, ValueError) as e:
                logger.warning(f"Failed to format i18n string for key '{key}': {e}")
                return template

    @property
    def locale(self) -> str:
        """Get current locale string.

        Returns:
            Current locale identifier (e.g., "ko_KR")
        """
        with self._lock:
            return self._locale

    @classmethod
    def reset(cls) -> None:
        """Reset singleton instance (for testing)."""
        with cls._lock:
            cls._instance = None

    def _resolve(self, key: str) -> str:
        """Walk nested dict by dot-separated key.

        Args:
            key: Dot-separated key path

        Returns:
            Resolved string value, or the original key if not found.

        Internal helper method. Not thread-safe (caller must hold lock).
        """
        parts = key.split(".")
        node = self._data

        for part in parts:
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return key

        return node if isinstance(node, str) else key
