"""Shared test fixtures for ReddiScribe tests."""

import json
import shutil
import tempfile
from pathlib import Path

import pytest
import yaml

from src.core.config_manager import ConfigManager, DEFAULT_CONFIG
from src.core.database import DatabaseManager
from src.core.i18n_manager import I18nManager


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset all singletons before each test."""
    yield
    ConfigManager.reset()
    DatabaseManager.reset()
    I18nManager.reset()


@pytest.fixture
def tmp_dir():
    """Provide a temporary directory that is cleaned up after test."""
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def config_file(tmp_dir):
    """Create a temporary settings.yaml and return its path."""
    config_dir = tmp_dir / "config"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "settings.yaml"

    # Write default config
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(dict(DEFAULT_CONFIG), f, default_flow_style=False, sort_keys=False)

    return config_path


@pytest.fixture
def tmp_db_path(tmp_dir):
    """Provide a temporary database path."""
    return tmp_dir / "test.db"


@pytest.fixture
def locale_dir(tmp_dir):
    """Create temporary locale directory with test JSON files."""
    loc_dir = tmp_dir / "locales"
    loc_dir.mkdir(parents=True)

    ko_data = {
        "app": {"title": "ReddiScribe"},
        "nav": {"write": "작성", "read": "읽기"},
        "errors": {"model_not_found": "모델을 찾을 수 없습니다: {model}"},
    }
    en_data = {
        "app": {"title": "ReddiScribe"},
        "nav": {"write": "Write", "read": "Read"},
        "errors": {"model_not_found": "Model not found: {model}"},
    }

    with open(loc_dir / "ko_KR.json", "w", encoding="utf-8") as f:
        json.dump(ko_data, f, ensure_ascii=False)
    with open(loc_dir / "en_US.json", "w", encoding="utf-8") as f:
        json.dump(en_data, f, ensure_ascii=False)

    return loc_dir
