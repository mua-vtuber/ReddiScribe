"""Main application window with sidebar navigation."""

import logging

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QPushButton, QStackedWidget, QStatusBar, QLabel,
)
from PyQt6.QtCore import Qt

from src.core.config_manager import ConfigManager
from src.core.i18n_manager import I18nManager
from src.gui.widgets.reader_widget import ReaderWidget
from src.gui.widgets.writer_widget import WriterWidget
from src.gui.widgets.settings_widget import SettingsWidget
from src.services.reader_service import ReaderService
from src.services.writer_service import WriterService

logger = logging.getLogger("reddiscribe")


class MainWindow(QMainWindow):
    """Main application window with sidebar + stacked views."""

    def __init__(
        self,
        reader_service: ReaderService,
        writer_service: WriterService,
        config: ConfigManager,
    ):
        super().__init__()
        self._config = config
        self._i18n = I18nManager()
        self._reader_service = reader_service
        self._writer_service = writer_service

        self.setWindowTitle(self._i18n.get("app.title"))
        self.setMinimumSize(900, 600)

        self._init_ui()

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # === Sidebar ===
        sidebar = QWidget()
        sidebar.setFixedWidth(120)
        sidebar.setStyleSheet("background-color: #2b2b2b;")
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(8, 16, 8, 16)

        self._nav_buttons = []

        self._read_btn = QPushButton(self._i18n.get("nav.read"))
        self._read_btn.clicked.connect(lambda: self._switch_view(0))
        self._read_btn.setStyleSheet(self._nav_btn_style(True))
        sidebar_layout.addWidget(self._read_btn)
        self._nav_buttons.append(self._read_btn)

        self._write_btn = QPushButton(self._i18n.get("nav.write"))
        self._write_btn.clicked.connect(lambda: self._switch_view(1))
        self._write_btn.setStyleSheet(self._nav_btn_style(False))
        sidebar_layout.addWidget(self._write_btn)
        self._nav_buttons.append(self._write_btn)

        self._settings_btn = QPushButton(self._i18n.get("nav.settings"))
        self._settings_btn.clicked.connect(lambda: self._switch_view(2))
        self._settings_btn.setStyleSheet(self._nav_btn_style(False))
        sidebar_layout.addWidget(self._settings_btn)
        self._nav_buttons.append(self._settings_btn)

        sidebar_layout.addStretch()
        main_layout.addWidget(sidebar)

        # === Content area (stacked views) ===
        self._stack = QStackedWidget()

        self._reader_widget = ReaderWidget(self._reader_service, self._config)
        self._writer_widget = WriterWidget(self._writer_service)
        self._settings_widget = SettingsWidget(self._config)

        # Connect settings signals
        self._settings_widget.locale_changed.connect(self._on_locale_changed)
        self._settings_widget.settings_saved.connect(self._on_settings_saved)

        self._stack.addWidget(self._reader_widget)   # index 0
        self._stack.addWidget(self._writer_widget)    # index 1
        self._stack.addWidget(self._settings_widget)  # index 2

        main_layout.addWidget(self._stack)

        # === Status bar ===
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)

    def _switch_view(self, index: int):
        """Switch the stacked widget and update nav button styles."""
        self._stack.setCurrentIndex(index)
        for i, btn in enumerate(self._nav_buttons):
            btn.setStyleSheet(self._nav_btn_style(i == index))

    def _on_locale_changed(self, new_locale: str):
        """Handle locale change from settings."""
        self._i18n.load_locale(new_locale)
        self.retranslate_ui()
        self._status_bar.showMessage(self._i18n.get("status.language_changed"), 3000)

    def _on_settings_saved(self):
        self._status_bar.showMessage(self._i18n.get("status.settings_saved"), 3000)

    def retranslate_ui(self):
        """Update all UI text across the entire application."""
        self.setWindowTitle(self._i18n.get("app.title"))
        self._read_btn.setText(self._i18n.get("nav.read"))
        self._write_btn.setText(self._i18n.get("nav.write"))
        self._settings_btn.setText(self._i18n.get("nav.settings"))

        # Retranslate child widgets
        self._reader_widget.retranslate_ui()
        self._writer_widget.retranslate_ui()
        self._settings_widget.retranslate_ui()

    @staticmethod
    def _nav_btn_style(active: bool) -> str:
        """Return stylesheet for nav button (active/inactive)."""
        if active:
            return (
                "QPushButton {"
                "  background-color: #3d3d3d;"
                "  color: white;"
                "  border: none;"
                "  padding: 10px;"
                "  font-weight: bold;"
                "  text-align: left;"
                "}"
            )
        return (
            "QPushButton {"
            "  background-color: transparent;"
            "  color: #aaaaaa;"
            "  border: none;"
            "  padding: 10px;"
            "  text-align: left;"
            "}"
            "QPushButton:hover {"
            "  background-color: #333333;"
            "  color: white;"
            "}"
        )
