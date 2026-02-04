"""ReddiScribe application entry point."""

import sys
import logging

from PyQt6.QtWidgets import QApplication

from src.core.config_manager import ConfigManager
from src.core.logger import setup_logger
from src.core.i18n_manager import I18nManager
from src.core.database import DatabaseManager
from src.adapters.public_json_adapter import PublicJSONAdapter
from src.adapters.ollama_adapter import OllamaAdapter
from src.services.reader_service import ReaderService
from src.services.writer_service import WriterService
from src.gui.main_window import MainWindow


def main():
    """Main entry point for ReddiScribe application.

    Startup sequence (per spec Section 13):
    1. ConfigManager init (loads or creates settings.yaml)
    2. Logger init (reads log_level from config)
    3. I18nManager init (reads locale from config)
    4. DatabaseManager init (creates tables if needed)
    5. Adapter creation (PublicJSONAdapter, OllamaAdapter)
    6. Service creation (ReaderService, WriterService)
    7. QApplication creation
    8. MainWindow creation
    9. window.show()
    10. Event loop
    """
    # 1. ConfigManager (loads or creates settings.yaml)
    config = ConfigManager()

    # 2. Logger (reads log_level from config)
    log_level = config.get("app.log_level", "INFO")
    mask_logs = config.get("security.mask_logs", True)
    logger = setup_logger(log_level=log_level, mask_logs=mask_logs)
    logger.info("ReddiScribe starting...")

    # 3. I18nManager (reads locale from config)
    i18n = I18nManager()
    locale = config.get("app.locale", "ko_KR")
    i18n.load_locale(locale)
    logger.info(f"Locale loaded: {locale}")

    # 4. DatabaseManager (creates tables if needed)
    db_path = config.get_db_path()
    db = DatabaseManager(db_path)
    logger.info(f"Database initialized: {db_path}")

    # 5. Create adapters
    reddit_adapter = PublicJSONAdapter(
        request_interval_sec=config.get("reddit.request_interval_sec", 6),
        max_retries=config.get("reddit.max_retries", 3),
        mock_mode=config.get("reddit.mock_mode", False),
    )

    ollama_adapter = OllamaAdapter(
        host=config.get("llm.providers.ollama.host", "http://localhost:11434"),
        timeout=config.get("llm.providers.ollama.timeout", 120),
    )

    # 6. Create services (inject adapters)
    reader_service = ReaderService(reddit_adapter, ollama_adapter, db, config)
    writer_service = WriterService(ollama_adapter, config)

    # 7. Create QApplication
    app = QApplication(sys.argv)

    # 8. Create MainWindow (inject services)
    window = MainWindow(reader_service, writer_service, config, ollama_adapter, reddit_adapter)

    # 9. Show window
    window.show()
    logger.info("ReddiScribe UI ready")

    # 10. Enter event loop
    exit_code = app.exec()

    # Cleanup
    ollama_adapter.unload_models()
    db.close()
    logger.info("ReddiScribe shutting down")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
