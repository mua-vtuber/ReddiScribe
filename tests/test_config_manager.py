"""Tests for ConfigManager."""

import yaml
import pytest
from pathlib import Path
from unittest.mock import patch

from src.core.config_manager import ConfigManager, DEFAULT_CONFIG
from src.core.exceptions import ConfigError


class TestConfigManagerInit:
    """Test configuration loading and creation."""

    def test_creates_default_config_when_missing(self, tmp_dir):
        """When no settings.yaml exists, should create one with defaults."""
        config_path = tmp_dir / "config" / "settings.yaml"

        # Create a fresh instance with patched paths
        ConfigManager.reset()
        cm = ConfigManager.__new__(ConfigManager)
        cm._initialized = False
        cm.PROJECT_ROOT = tmp_dir
        cm.CONFIG_PATH = config_path
        cm._config = {}
        cm._instance_lock = __import__('threading').RLock()
        cm._load_or_create_config()

        assert config_path.exists()
        with open(config_path, 'r') as f:
            saved = yaml.safe_load(f)
        assert saved["app"]["locale"] == "ko_KR"

    def test_loads_existing_config(self, tmp_dir):
        """Should load values from existing settings.yaml."""
        config_path = tmp_dir / "config" / "settings.yaml"
        config_path.parent.mkdir(parents=True)

        custom_config = {"app": {"locale": "en_US", "theme": "light"}}
        with open(config_path, 'w') as f:
            yaml.safe_dump(custom_config, f)

        ConfigManager.reset()
        cm = ConfigManager.__new__(ConfigManager)
        cm._initialized = False
        cm.PROJECT_ROOT = tmp_dir
        cm.CONFIG_PATH = config_path
        cm._config = {}
        cm._instance_lock = __import__('threading').RLock()
        cm._load_or_create_config()

        assert cm.get("app.locale") == "en_US"
        assert cm.get("app.theme") == "light"

    def test_uses_defaults_on_invalid_yaml(self, tmp_dir):
        """Should fall back to defaults when YAML is invalid."""
        config_path = tmp_dir / "config" / "settings.yaml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text("{{invalid yaml: [")

        ConfigManager.reset()
        cm = ConfigManager.__new__(ConfigManager)
        cm._initialized = False
        cm.PROJECT_ROOT = tmp_dir
        cm.CONFIG_PATH = config_path
        cm._config = {}
        cm._instance_lock = __import__('threading').RLock()
        cm._load_or_create_config()

        assert cm.get("app.locale") == "ko_KR"


class TestConfigManagerGetSet:
    """Test get/set with dot notation."""

    def _make_cm(self):
        ConfigManager.reset()
        cm = ConfigManager.__new__(ConfigManager)
        cm._initialized = True
        cm._config = ConfigManager._deep_copy(DEFAULT_CONFIG)
        cm._instance_lock = __import__('threading').RLock()
        cm.PROJECT_ROOT = Path(".")
        cm.CONFIG_PATH = Path("./config/settings.yaml")
        ConfigManager._instance = cm
        return cm

    def test_get_simple_key(self):
        cm = self._make_cm()
        assert cm.get("app.locale") == "ko_KR"

    def test_get_nested_key(self):
        cm = self._make_cm()
        assert cm.get("llm.models.logic.name") == "qwen2.5-coder:32b"

    def test_get_missing_key_returns_default(self):
        cm = self._make_cm()
        assert cm.get("nonexistent.key") is None
        assert cm.get("nonexistent.key", "fallback") == "fallback"

    def test_set_updates_value(self):
        cm = self._make_cm()
        cm.set("app.locale", "en_US")
        assert cm.get("app.locale") == "en_US"

    def test_set_creates_nested_path(self):
        cm = self._make_cm()
        cm.set("new.nested.key", "value")
        assert cm.get("new.nested.key") == "value"


class TestConfigManagerValidation:
    """Test validation rules in update()."""

    def _make_cm(self, tmp_dir):
        config_path = tmp_dir / "config" / "settings.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)

        ConfigManager.reset()
        cm = ConfigManager.__new__(ConfigManager)
        cm._initialized = True
        cm._config = ConfigManager._deep_copy(DEFAULT_CONFIG)
        cm._instance_lock = __import__('threading').RLock()
        cm.PROJECT_ROOT = tmp_dir
        cm.CONFIG_PATH = config_path
        ConfigManager._instance = cm
        return cm

    def test_invalid_locale_ignored(self, tmp_dir):
        cm = self._make_cm(tmp_dir)
        cm.update({"app.locale": "fr_FR"})
        assert cm.get("app.locale") == "ko_KR"  # unchanged

    def test_valid_locale_accepted(self, tmp_dir):
        cm = self._make_cm(tmp_dir)
        cm.update({"app.locale": "en_US"})
        assert cm.get("app.locale") == "en_US"

    def test_interval_below_min_forced_to_3(self, tmp_dir):
        cm = self._make_cm(tmp_dir)
        cm.update({"reddit.request_interval_sec": 1})
        assert cm.get("reddit.request_interval_sec") == 3

    def test_temperature_out_of_range_forced_to_default(self, tmp_dir):
        cm = self._make_cm(tmp_dir)
        cm.update({"llm.generation.temperature": 5.0})
        assert cm.get("llm.generation.temperature") == 0.7

    def test_temperature_in_range_accepted(self, tmp_dir):
        cm = self._make_cm(tmp_dir)
        cm.update({"llm.generation.temperature": 1.5})
        assert cm.get("llm.generation.temperature") == 1.5

    def test_timeout_below_min_forced_to_30(self, tmp_dir):
        cm = self._make_cm(tmp_dir)
        cm.update({"llm.providers.ollama.timeout": 10})
        assert cm.get("llm.providers.ollama.timeout") == 30


class TestConfigManagerDbPath:
    """Test database path resolution."""

    def test_get_db_path_resolves_absolute(self):
        ConfigManager.reset()
        cm = ConfigManager.__new__(ConfigManager)
        cm._initialized = True
        cm._config = {"data": {"db_path": "db/history.db"}}
        cm._instance_lock = __import__('threading').RLock()
        cm.PROJECT_ROOT = Path("/fake/project")
        cm.CONFIG_PATH = Path("/fake/project/config/settings.yaml")
        ConfigManager._instance = cm

        assert cm.get_db_path() == Path("/fake/project/db/history.db")
