"""Settings widget with full i18n support and batch save."""

import logging

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QGroupBox,
    QPushButton, QComboBox, QSpinBox, QCheckBox,
    QLineEdit, QLabel,
)
from PyQt6.QtCore import pyqtSignal

from src.core.config_manager import ConfigManager
from src.core.i18n_manager import I18nManager

logger = logging.getLogger("reddiscribe")


class SettingsWidget(QWidget):
    """Settings tab with grouped config fields and batch save."""

    # Signal emitted when locale changes (MainWindow listens to retranslate)
    locale_changed = pyqtSignal(str)  # new locale string
    settings_saved = pyqtSignal()

    def __init__(self, config: ConfigManager, parent=None):
        super().__init__(parent)
        self._config = config
        self._i18n = I18nManager()
        self._init_ui()
        self._load_values()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        self._header = QLabel(self._i18n.get("settings.header"))
        self._header.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(self._header)

        # === Application group ===
        self._app_group = QGroupBox(self._i18n.get("settings.app_group"))
        app_form = QFormLayout()

        self._lang_label = QLabel(self._i18n.get("settings.lang_label"))
        self._lang_combo = QComboBox()
        self._lang_combo.addItems(["ko_KR", "en_US"])
        app_form.addRow(self._lang_label, self._lang_combo)

        self._theme_label = QLabel(self._i18n.get("settings.theme_label"))
        self._theme_combo = QComboBox()
        self._theme_combo.addItems(["dark", "light"])
        self._theme_combo.setEnabled(False)  # v1.0: read-only
        app_form.addRow(self._theme_label, self._theme_combo)

        self._app_group.setLayout(app_form)
        layout.addWidget(self._app_group)

        # === LLM group ===
        self._llm_group = QGroupBox(self._i18n.get("settings.llm_group"))
        llm_form = QFormLayout()

        self._logic_label = QLabel(self._i18n.get("settings.logic_label"))
        self._logic_input = QLineEdit()
        llm_form.addRow(self._logic_label, self._logic_input)

        self._persona_label = QLabel(self._i18n.get("settings.persona_label"))
        self._persona_input = QLineEdit()
        llm_form.addRow(self._persona_label, self._persona_input)

        self._summary_label = QLabel(self._i18n.get("settings.summary_label"))
        self._summary_input = QLineEdit()
        llm_form.addRow(self._summary_label, self._summary_input)

        self._host_label = QLabel(self._i18n.get("settings.host_label"))
        self._host_input = QLineEdit()
        llm_form.addRow(self._host_label, self._host_input)

        self._timeout_label = QLabel(self._i18n.get("settings.timeout_label"))
        self._timeout_spin = QSpinBox()
        self._timeout_spin.setRange(30, 600)
        llm_form.addRow(self._timeout_label, self._timeout_spin)

        self._llm_group.setLayout(llm_form)
        layout.addWidget(self._llm_group)

        # === Reddit group ===
        self._reddit_group = QGroupBox(self._i18n.get("settings.reddit_group"))
        reddit_form = QFormLayout()

        self._interval_label = QLabel(self._i18n.get("settings.interval_label"))
        self._interval_spin = QSpinBox()
        self._interval_spin.setRange(3, 60)
        reddit_form.addRow(self._interval_label, self._interval_spin)

        self._mock_label = QLabel(self._i18n.get("settings.mock_label"))
        self._mock_check = QCheckBox()
        reddit_form.addRow(self._mock_label, self._mock_check)

        self._reddit_group.setLayout(reddit_form)
        layout.addWidget(self._reddit_group)

        # === Advanced group ===
        self._advanced_group = QGroupBox(self._i18n.get("settings.advanced_group"))
        adv_form = QFormLayout()

        self._log_label = QLabel(self._i18n.get("settings.log_level_label"))
        self._log_combo = QComboBox()
        self._log_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        adv_form.addRow(self._log_label, self._log_combo)

        self._advanced_group.setLayout(adv_form)
        layout.addWidget(self._advanced_group)

        # Save button
        self._save_btn = QPushButton(self._i18n.get("settings.save_btn"))
        self._save_btn.clicked.connect(self._on_save)
        layout.addWidget(self._save_btn)

        layout.addStretch()

    def _load_values(self):
        """Load current config values into widgets."""
        self._lang_combo.setCurrentText(self._config.get("app.locale", "ko_KR"))
        self._theme_combo.setCurrentText(self._config.get("app.theme", "dark"))
        self._logic_input.setText(self._config.get("llm.models.logic.name", ""))
        self._persona_input.setText(self._config.get("llm.models.persona.name", ""))
        self._summary_input.setText(self._config.get("llm.models.summary.name", ""))
        self._host_input.setText(self._config.get("llm.providers.ollama.host", "http://localhost:11434"))
        self._timeout_spin.setValue(self._config.get("llm.providers.ollama.timeout", 120))
        self._interval_spin.setValue(self._config.get("reddit.request_interval_sec", 6))
        self._mock_check.setChecked(self._config.get("reddit.mock_mode", False))
        self._log_combo.setCurrentText(self._config.get("app.log_level", "INFO"))

    def _on_save(self):
        """Collect changes and batch update config."""
        old_locale = self._config.get("app.locale", "ko_KR")

        changes = {
            "app.locale": self._lang_combo.currentText(),
            "app.log_level": self._log_combo.currentText(),
            "llm.models.logic.name": self._logic_input.text(),
            "llm.models.persona.name": self._persona_input.text(),
            "llm.models.summary.name": self._summary_input.text(),
            "llm.providers.ollama.host": self._host_input.text(),
            "llm.providers.ollama.timeout": self._timeout_spin.value(),
            "reddit.request_interval_sec": self._interval_spin.value(),
            "reddit.mock_mode": self._mock_check.isChecked(),
        }

        self._config.update(changes)
        self.settings_saved.emit()

        new_locale = self._config.get("app.locale", "ko_KR")
        if new_locale != old_locale:
            self.locale_changed.emit(new_locale)

        logger.info("Settings saved")

    def retranslate_ui(self):
        """Update all labels for locale change."""
        self._header.setText(self._i18n.get("settings.header"))
        self._app_group.setTitle(self._i18n.get("settings.app_group"))
        self._lang_label.setText(self._i18n.get("settings.lang_label"))
        self._theme_label.setText(self._i18n.get("settings.theme_label"))
        self._llm_group.setTitle(self._i18n.get("settings.llm_group"))
        self._logic_label.setText(self._i18n.get("settings.logic_label"))
        self._persona_label.setText(self._i18n.get("settings.persona_label"))
        self._summary_label.setText(self._i18n.get("settings.summary_label"))
        self._host_label.setText(self._i18n.get("settings.host_label"))
        self._timeout_label.setText(self._i18n.get("settings.timeout_label"))
        self._reddit_group.setTitle(self._i18n.get("settings.reddit_group"))
        self._interval_label.setText(self._i18n.get("settings.interval_label"))
        self._mock_label.setText(self._i18n.get("settings.mock_label"))
        self._advanced_group.setTitle(self._i18n.get("settings.advanced_group"))
        self._log_label.setText(self._i18n.get("settings.log_level_label"))
        self._save_btn.setText(self._i18n.get("settings.save_btn"))
