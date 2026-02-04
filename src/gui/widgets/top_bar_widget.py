"""Global top bar with subreddit selector and activity indicator."""

import logging

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton, QComboBox,
    QLabel, QInputDialog, QMessageBox,
)
from PyQt6.QtCore import pyqtSignal, Qt, QTimer

from src.core.config_manager import ConfigManager
from src.core.i18n_manager import I18nManager

logger = logging.getLogger("reddiscribe")


class TopBarWidget(QWidget):
    """Global top bar with subreddit dropdown and activity indicator."""

    subreddit_changed = pyqtSignal(str)
    subreddit_list_changed = pyqtSignal(list)

    def __init__(self, config: ConfigManager, reddit_adapter=None, parent=None):
        super().__init__(parent)
        self._config = config
        self._i18n = I18nManager()
        self._reddit_adapter = reddit_adapter
        self._validation_worker = None
        self._active_tasks = {}  # {task_name: elapsed_seconds}
        self._init_ui()
        self._load_subreddits()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        self.setFixedHeight(36)
        self.setStyleSheet(
            "TopBarWidget { background-color: #1e1e2e; border-bottom: 1px solid #3d3d3d; }"
        )

        # Subreddit dropdown
        self._sub_combo = QComboBox()
        self._sub_combo.setMinimumWidth(150)
        self._sub_combo.currentTextChanged.connect(self._on_subreddit_changed)
        layout.addWidget(self._sub_combo)

        # Add button
        self._add_btn = QPushButton("+")
        self._add_btn.setFixedWidth(30)
        self._add_btn.clicked.connect(self._on_add_subreddit)
        self._add_btn.setToolTip(self._i18n.get("topbar.add_subreddit"))
        layout.addWidget(self._add_btn)

        layout.addStretch()

        # Activity indicator (always present, just empty when idle)
        self._activity_label = QLabel("")
        self._activity_label.setStyleSheet(
            "QLabel { color: #4fc3f7; font-size: 12px; }"
        )
        self._activity_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        layout.addWidget(self._activity_label)

        # Activity animation timer
        self._activity_timer = QTimer(self)
        self._activity_timer.setInterval(500)
        self._activity_timer.timeout.connect(self._update_activity_animation)
        self._activity_dot_count = 0

        # Elapsed time timer
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._update_elapsed_time)

    def _load_subreddits(self, emit_change: bool = True):
        subs = self._config.get(
            "reddit.subreddits", ["AI_Application", "AiBuilders", "AIDevHub", "ClaudeCode"]
        )
        self._sub_combo.blockSignals(True)
        self._sub_combo.clear()
        self._sub_combo.addItem("---")  # placeholder: no auto-fetch
        for sub in subs:
            self._sub_combo.addItem(sub)
        self._sub_combo.blockSignals(False)

    def _save_subreddits(self):
        subs = [self._sub_combo.itemText(i) for i in range(self._sub_combo.count()) if self._sub_combo.itemText(i) != "---"]
        self._config.set("reddit.subreddits", subs)
        self._config.save()
        self.subreddit_list_changed.emit(subs)

    def _on_subreddit_changed(self, text: str):
        if text and text != "---":
            self.subreddit_changed.emit(text)

    def _on_add_subreddit(self):
        text, ok = QInputDialog.getText(
            self,
            self._i18n.get("topbar.add_subreddit"),
            self._i18n.get("topbar.add_prompt"),
        )
        if not ok or not text.strip():
            return

        name = text.strip().lower().removeprefix("r/")

        # Check duplicates
        for i in range(self._sub_combo.count()):
            if self._sub_combo.itemText(i) == name:
                QMessageBox.information(
                    self,
                    self._i18n.get("topbar.add_subreddit"),
                    self._i18n.get("topbar.duplicate"),
                )
                return

        # Validate via API if adapter available
        if self._reddit_adapter is not None:
            self._start_validation(name)
        else:
            # No adapter (e.g., mock mode) - just add
            self._add_subreddit_to_combo(name)

    def _start_validation(self, name: str):
        from src.gui.workers import SubredditValidationWorker

        # Clean up previous worker
        if self._validation_worker is not None:
            if self._validation_worker.isRunning():
                return  # Already validating
            self._validation_worker.deleteLater()

        self._add_btn.setEnabled(False)
        self._add_btn.setText(self._i18n.get("topbar.validating"))

        self._validation_worker = SubredditValidationWorker(
            self._reddit_adapter, name, parent=self
        )
        self._validation_worker.validation_success.connect(self._on_validation_success)
        self._validation_worker.validation_error.connect(self._on_validation_error)
        self._validation_worker.start()

    def _on_validation_success(self, name: str):
        self._add_btn.setEnabled(True)
        self._add_btn.setText("+")
        self._add_subreddit_to_combo(name)

    def _on_validation_error(self, name: str, error_key: str):
        self._add_btn.setEnabled(True)
        self._add_btn.setText("+")
        QMessageBox.warning(
            self,
            self._i18n.get("topbar.add_subreddit"),
            self._i18n.get(error_key),
        )

    def _add_subreddit_to_combo(self, name: str):
        self._sub_combo.addItem(name)
        self._sub_combo.setCurrentText(name)
        self._save_subreddits()

    # --- Activity indicator ---

    def on_activity_started(self, task_name: str):
        """Register an active task for the activity indicator."""
        self._active_tasks[task_name] = 0
        self._activity_dot_count = 0
        if not self._activity_timer.isActive():
            self._activity_timer.start()
            self._elapsed_timer.start()

    def on_activity_finished(self, task_name: str):
        """Remove a finished task from the activity indicator."""
        self._active_tasks.pop(task_name, None)
        if not self._active_tasks:
            self._activity_timer.stop()
            self._elapsed_timer.stop()
            self._activity_label.setText("")

    def _update_activity_animation(self):
        if not self._active_tasks:
            return
        self._activity_dot_count = (self._activity_dot_count + 1) % 4
        dots = "." * self._activity_dot_count
        parts = []
        for name, secs in self._active_tasks.items():
            elapsed_text = self._i18n.get("status.elapsed", seconds=str(secs))
            parts.append(f"{name} {elapsed_text}")
        self._activity_label.setText(f"{' | '.join(parts)}{dots}")

    def _update_elapsed_time(self):
        for name in self._active_tasks:
            self._active_tasks[name] += 1

    # --- Public API ---

    def reload_subreddits(self):
        """Reload subreddit list from config (for settings sync)."""
        current = self._sub_combo.currentText()
        self._load_subreddits(emit_change=False)
        idx = self._sub_combo.findText(current)
        if idx >= 0:
            self._sub_combo.blockSignals(True)
            self._sub_combo.setCurrentIndex(idx)
            self._sub_combo.blockSignals(False)

    def retranslate_ui(self):
        """Update all labels for locale change."""
        self._add_btn.setToolTip(self._i18n.get("topbar.add_subreddit"))
