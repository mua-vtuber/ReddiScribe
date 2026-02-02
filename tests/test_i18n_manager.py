"""Tests for I18nManager."""

import json
from pathlib import Path
from unittest.mock import patch

from src.core.i18n_manager import I18nManager, LOCALE_DIR


class TestI18nManagerInit:
    """Test singleton behavior."""

    def test_singleton_returns_same_instance(self):
        a = I18nManager()
        b = I18nManager()
        assert a is b

    def test_reset_allows_new_instance(self):
        a = I18nManager()
        I18nManager.reset()
        b = I18nManager()
        assert a is not b


class TestI18nManagerLoadLocale:
    """Test locale loading."""

    def test_load_valid_locale(self, locale_dir):
        """Load a valid locale JSON file."""
        mgr = I18nManager()
        with patch("src.core.i18n_manager.LOCALE_DIR", locale_dir):
            mgr.load_locale("ko_KR")
        assert mgr.locale == "ko_KR"
        assert mgr.get("app.title") == "ReddiScribe"

    def test_load_missing_locale_keeps_current(self, locale_dir):
        """Loading non-existent locale should not crash, keeps current data."""
        mgr = I18nManager()
        with patch("src.core.i18n_manager.LOCALE_DIR", locale_dir):
            mgr.load_locale("ko_KR")
            mgr.load_locale("zh_CN")  # doesn't exist
        assert mgr.locale == "ko_KR"  # unchanged
        assert mgr.get("app.title") == "ReddiScribe"  # data preserved

    def test_load_invalid_json_keeps_current(self, locale_dir):
        """Loading invalid JSON should not crash, keeps current data."""
        (locale_dir / "bad.json").write_text("{{{invalid", encoding="utf-8")
        mgr = I18nManager()
        with patch("src.core.i18n_manager.LOCALE_DIR", locale_dir):
            mgr.load_locale("ko_KR")
            mgr.load_locale("bad")
        assert mgr.locale == "ko_KR"

    def test_switch_locale(self, locale_dir):
        """Switch from one locale to another."""
        mgr = I18nManager()
        with patch("src.core.i18n_manager.LOCALE_DIR", locale_dir):
            mgr.load_locale("ko_KR")
            assert mgr.get("nav.write") == "작성"
            mgr.load_locale("en_US")
            assert mgr.get("nav.write") == "Write"
            assert mgr.locale == "en_US"


class TestI18nManagerGet:
    """Test key resolution and formatting."""

    def test_get_simple_key(self, locale_dir):
        mgr = I18nManager()
        with patch("src.core.i18n_manager.LOCALE_DIR", locale_dir):
            mgr.load_locale("ko_KR")
        assert mgr.get("nav.read") == "읽기"

    def test_get_missing_key_returns_key(self, locale_dir):
        mgr = I18nManager()
        with patch("src.core.i18n_manager.LOCALE_DIR", locale_dir):
            mgr.load_locale("ko_KR")
        assert mgr.get("nonexistent.key") == "nonexistent.key"

    def test_get_with_placeholder(self, locale_dir):
        mgr = I18nManager()
        with patch("src.core.i18n_manager.LOCALE_DIR", locale_dir):
            mgr.load_locale("ko_KR")
        result = mgr.get("errors.model_not_found", model="llama3")
        assert result == "모델을 찾을 수 없습니다: llama3"

    def test_get_with_missing_placeholder_returns_template(self, locale_dir):
        """If placeholder kwargs don't match, return template as-is."""
        mgr = I18nManager()
        with patch("src.core.i18n_manager.LOCALE_DIR", locale_dir):
            mgr.load_locale("ko_KR")
        result = mgr.get("errors.model_not_found", wrong_key="test")
        assert "{model}" in result

    def test_get_non_string_node_returns_key(self, locale_dir):
        """If key points to a dict (not leaf string), return the key itself."""
        mgr = I18nManager()
        with patch("src.core.i18n_manager.LOCALE_DIR", locale_dir):
            mgr.load_locale("ko_KR")
        assert mgr.get("app") == "app"  # "app" is a dict, not a string
