"""Settings widget with full i18n support and batch save."""

import logging

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QGroupBox,
    QPushButton, QComboBox, QSpinBox, QCheckBox,
    QLineEdit, QLabel, QTextEdit, QDoubleSpinBox,
    QScrollArea, QListWidget, QHBoxLayout,
)
from PyQt6.QtCore import pyqtSignal, Qt

from src.core.config_manager import ConfigManager
from src.core.i18n_manager import I18nManager
from src.adapters.ollama_adapter import format_model_size

logger = logging.getLogger("reddiscribe")


class SettingsWidget(QWidget):
    """Settings tab with grouped config fields and batch save."""

    # Signal emitted when locale changes (MainWindow listens to retranslate)
    locale_changed = pyqtSignal(str)  # new locale string
    settings_saved = pyqtSignal()

    def __init__(self, config: ConfigManager, ollama_adapter=None, reddit_adapter=None, parent=None):
        super().__init__(parent)
        self._config = config
        self._i18n = I18nManager()
        self._ollama_adapter = ollama_adapter
        self._reddit_adapter = reddit_adapter
        self._model_fetch_worker = None
        self._sub_validation_worker = None
        self._init_ui()
        self._load_values()

    def _init_ui(self):
        outer_layout = QVBoxLayout(self)

        # Scroll area for all settings
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        container = QWidget()
        layout = QVBoxLayout(container)

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
        self._logic_combo = QComboBox()
        self._logic_combo.setEditable(True)
        llm_form.addRow(self._logic_label, self._logic_combo)

        self._persona_label = QLabel(self._i18n.get("settings.persona_label"))
        self._persona_combo = QComboBox()
        self._persona_combo.setEditable(True)
        llm_form.addRow(self._persona_label, self._persona_combo)

        self._refresh_models_btn = QPushButton(self._i18n.get("settings.refresh_models_btn"))
        self._refresh_models_btn.clicked.connect(self._on_refresh_models)
        llm_form.addRow("", self._refresh_models_btn)

        self._host_label = QLabel(self._i18n.get("settings.host_label"))
        self._host_input = QLineEdit()
        llm_form.addRow(self._host_label, self._host_input)

        self._timeout_label = QLabel(self._i18n.get("settings.timeout_label"))
        self._timeout_spin = QSpinBox()
        self._timeout_spin.setRange(30, 600)
        llm_form.addRow(self._timeout_label, self._timeout_spin)

        self._llm_group.setLayout(llm_form)
        layout.addWidget(self._llm_group)

        # === Persona group ===
        self._persona_group = QGroupBox(self._i18n.get("settings.persona_group"))
        persona_form = QFormLayout()

        self._persona_temp_label = QLabel(self._i18n.get("settings.persona_temp_label"))
        self._persona_temp_spin = QDoubleSpinBox()
        self._persona_temp_spin.setRange(0.0, 2.0)
        self._persona_temp_spin.setSingleStep(0.1)
        self._persona_temp_spin.setDecimals(1)
        persona_form.addRow(self._persona_temp_label, self._persona_temp_spin)

        self._persona_prompt_label = QLabel(self._i18n.get("settings.persona_prompt_label"))
        self._persona_prompt_input = QTextEdit()
        self._persona_prompt_input.setMinimumHeight(120)
        self._persona_prompt_input.setMaximumHeight(200)
        persona_form.addRow(self._persona_prompt_label, self._persona_prompt_input)

        self._persona_group.setLayout(persona_form)
        layout.addWidget(self._persona_group)

        # === Reddit group ===
        self._reddit_group = QGroupBox(self._i18n.get("settings.reddit_group"))
        reddit_form = QFormLayout()

        # Subreddit list management
        self._subreddit_list_label = QLabel(self._i18n.get("settings.subreddit_list_label"))
        self._subreddit_list = QListWidget()
        self._subreddit_list.setMaximumHeight(120)
        reddit_form.addRow(self._subreddit_list_label, self._subreddit_list)

        sub_btn_layout = QHBoxLayout()
        self._add_sub_btn = QPushButton(self._i18n.get("settings.add_subreddit_btn"))
        self._add_sub_btn.clicked.connect(self._on_add_subreddit)
        sub_btn_layout.addWidget(self._add_sub_btn)

        self._remove_sub_btn = QPushButton(self._i18n.get("settings.remove_subreddit_btn"))
        self._remove_sub_btn.clicked.connect(self._on_remove_subreddit)
        sub_btn_layout.addWidget(self._remove_sub_btn)
        sub_btn_layout.addStretch()
        reddit_form.addRow("", sub_btn_layout)

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

        scroll.setWidget(container)
        outer_layout.addWidget(scroll)

    def _load_values(self):
        """Load current config values into widgets."""
        self._lang_combo.setCurrentText(self._config.get("app.locale", "ko_KR"))
        self._theme_combo.setCurrentText(self._config.get("app.theme", "dark"))
        self._logic_combo.setCurrentText(self._config.get("llm.models.logic.name", ""))
        self._persona_combo.setCurrentText(self._config.get("llm.models.persona.name", ""))
        self._host_input.setText(self._config.get("llm.providers.ollama.host", "http://localhost:11434"))
        self._timeout_spin.setValue(self._config.get("llm.providers.ollama.timeout", 120))
        self._persona_temp_spin.setValue(self._config.get("llm.models.persona.temperature", 0.7))
        self._persona_prompt_input.setPlainText(self._config.get("llm.models.persona.prompt", ""))
        # Load subreddit list
        subreddits = self._config.get("reddit.subreddits", [])
        self._subreddit_list.clear()
        self._subreddit_list.addItems(subreddits)
        self._interval_spin.setValue(self._config.get("reddit.request_interval_sec", 6))
        self._mock_check.setChecked(self._config.get("reddit.mock_mode", False))
        self._log_combo.setCurrentText(self._config.get("app.log_level", "INFO"))
        self._fetch_models()

    def _on_save(self):
        """Collect changes and batch update config."""
        old_locale = self._config.get("app.locale", "ko_KR")

        changes = {
            "app.locale": self._lang_combo.currentText(),
            "app.log_level": self._log_combo.currentText(),
            "llm.models.logic.name": self._get_combo_model_name(self._logic_combo),
            "llm.models.persona.name": self._get_combo_model_name(self._persona_combo),
            "llm.models.persona.temperature": self._persona_temp_spin.value(),
            "llm.models.persona.prompt": self._persona_prompt_input.toPlainText(),
            "llm.providers.ollama.host": self._host_input.text(),
            "llm.providers.ollama.timeout": self._timeout_spin.value(),
            "reddit.subreddits": [self._subreddit_list.item(i).text() for i in range(self._subreddit_list.count())],
            "reddit.request_interval_sec": self._interval_spin.value(),
            "reddit.mock_mode": self._mock_check.isChecked(),
        }

        self._config.update(changes)
        self.settings_saved.emit()

        new_locale = self._config.get("app.locale", "ko_KR")
        if new_locale != old_locale:
            self.locale_changed.emit(new_locale)

        logger.info("Settings saved")

    @staticmethod
    def _get_combo_model_name(combo) -> str:
        """Extract model name from combo box, respecting user edits.

        If the user cleared the text, returns empty string even if
        currentData() still holds the previous selection's userData.
        """
        text = combo.currentText().strip()
        if not text:
            return ""
        data = combo.currentData()
        return data if data else text

    def retranslate_ui(self):
        """Update all labels for locale change."""
        self._header.setText(self._i18n.get("settings.header"))
        self._app_group.setTitle(self._i18n.get("settings.app_group"))
        self._lang_label.setText(self._i18n.get("settings.lang_label"))
        self._theme_label.setText(self._i18n.get("settings.theme_label"))
        self._llm_group.setTitle(self._i18n.get("settings.llm_group"))
        self._logic_label.setText(self._i18n.get("settings.logic_label"))
        self._persona_label.setText(self._i18n.get("settings.persona_label"))
        self._host_label.setText(self._i18n.get("settings.host_label"))
        self._timeout_label.setText(self._i18n.get("settings.timeout_label"))
        self._persona_group.setTitle(self._i18n.get("settings.persona_group"))
        self._persona_temp_label.setText(self._i18n.get("settings.persona_temp_label"))
        self._persona_prompt_label.setText(self._i18n.get("settings.persona_prompt_label"))
        self._reddit_group.setTitle(self._i18n.get("settings.reddit_group"))
        self._subreddit_list_label.setText(self._i18n.get("settings.subreddit_list_label"))
        self._add_sub_btn.setText(self._i18n.get("settings.add_subreddit_btn"))
        self._remove_sub_btn.setText(self._i18n.get("settings.remove_subreddit_btn"))
        self._interval_label.setText(self._i18n.get("settings.interval_label"))
        self._mock_label.setText(self._i18n.get("settings.mock_label"))
        self._advanced_group.setTitle(self._i18n.get("settings.advanced_group"))
        self._log_label.setText(self._i18n.get("settings.log_level_label"))
        if self._refresh_models_btn.isEnabled():
            self._refresh_models_btn.setText(self._i18n.get("settings.refresh_models_btn"))
        self._save_btn.setText(self._i18n.get("settings.save_btn"))

    def _fetch_models(self):
        """Fetch available models from Ollama in background."""
        if self._ollama_adapter is None:
            return

        from src.gui.workers import ModelFetchWorker

        # Prevent concurrent fetches
        if self._model_fetch_worker is not None and self._model_fetch_worker.isRunning():
            return

        # Clean up old finished worker
        if self._model_fetch_worker is not None:
            self._model_fetch_worker.models_ready.disconnect()
            self._model_fetch_worker.error_occurred.disconnect()
            self._model_fetch_worker.deleteLater()

        self._refresh_models_btn.setEnabled(False)
        self._refresh_models_btn.setText(self._i18n.get("settings.models_loading"))

        self._model_fetch_worker = ModelFetchWorker(self._ollama_adapter, parent=self)
        self._model_fetch_worker.models_ready.connect(self._on_models_fetched)
        self._model_fetch_worker.error_occurred.connect(self._on_models_error)
        self._model_fetch_worker.start()

    def _on_models_fetched(self, models: list):
        """Populate model combo boxes with fetched models."""
        self._refresh_models_btn.setEnabled(True)
        self._refresh_models_btn.setText(self._i18n.get("settings.refresh_models_btn"))

        sorted_models = sorted(models, key=lambda m: m.get("name", ""))

        for combo in (self._logic_combo, self._persona_combo):
            current_text = combo.currentText()
            # Check if current text has size suffix and extract name
            current_data = combo.currentData()
            current_name = current_data if current_data else current_text
            combo.clear()
            for m in sorted_models:
                name = m.get("name", "")
                size = m.get("size", 0)
                size_str = format_model_size(size)
                display = f"{name} ({size_str})" if size_str else name
                combo.addItem(display, userData=name)
            # Restore selection by matching stored name
            idx = combo.findData(current_name)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            elif current_name:
                combo.setCurrentText(current_name)

    def _on_models_error(self, error_msg: str):
        """Handle model fetch error."""
        self._refresh_models_btn.setEnabled(True)
        self._refresh_models_btn.setText(self._i18n.get("settings.models_fetch_error"))
        logger.warning(f"Model fetch failed: {error_msg}")

    def _on_refresh_models(self):
        """Handle refresh button click."""
        self._fetch_models()

    def _on_add_subreddit(self):
        """Add a subreddit to the list with optional API validation."""
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(
            self,
            self._i18n.get("topbar.add_subreddit"),
            self._i18n.get("topbar.add_prompt"),
        )
        if ok and name.strip():
            name = name.strip().lower().removeprefix("r/")
            # Check for duplicates
            existing = [self._subreddit_list.item(i).text() for i in range(self._subreddit_list.count())]
            if name in existing:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.information(self, self._i18n.get("topbar.add_subreddit"), self._i18n.get("topbar.duplicate"))
                return
            # Validate via API if adapter available
            if self._reddit_adapter is not None:
                self._start_sub_validation(name)
            else:
                self._subreddit_list.addItem(name)

    def _start_sub_validation(self, name: str):
        """Validate subreddit via API before adding."""
        from src.gui.workers import SubredditValidationWorker
        from PyQt6.QtWidgets import QMessageBox

        if self._sub_validation_worker is not None:
            if self._sub_validation_worker.isRunning():
                return
            self._sub_validation_worker.deleteLater()

        self._add_sub_btn.setEnabled(False)
        self._add_sub_btn.setText(self._i18n.get("topbar.validating"))

        self._sub_validation_worker = SubredditValidationWorker(
            self._reddit_adapter, name, parent=self
        )
        self._sub_validation_worker.validation_success.connect(self._on_sub_validation_success)
        self._sub_validation_worker.validation_error.connect(self._on_sub_validation_error)
        self._sub_validation_worker.start()

    def _on_sub_validation_success(self, name: str):
        """Handle successful subreddit validation."""
        self._add_sub_btn.setEnabled(True)
        self._add_sub_btn.setText(self._i18n.get("settings.add_subreddit_btn"))
        self._subreddit_list.addItem(name)

    def _on_sub_validation_error(self, name: str, error_key: str):
        """Handle failed subreddit validation."""
        from PyQt6.QtWidgets import QMessageBox
        self._add_sub_btn.setEnabled(True)
        self._add_sub_btn.setText(self._i18n.get("settings.add_subreddit_btn"))
        QMessageBox.warning(
            self,
            self._i18n.get("topbar.add_subreddit"),
            self._i18n.get(error_key),
        )

    def _on_remove_subreddit(self):
        """Remove the selected subreddit from the list."""
        current = self._subreddit_list.currentRow()
        if current >= 0:
            self._subreddit_list.takeItem(current)
