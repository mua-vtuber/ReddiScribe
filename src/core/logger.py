"""Logging setup for ReddiScribe with sensitive data masking."""

import logging
import logging.handlers
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LOG_DIR = PROJECT_ROOT / "logs"


class SensitiveDataFilter(logging.Filter):
    """Filter to mask sensitive data in log messages."""

    URL_PATTERN = re.compile(r'https?://[^\s]+')

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self.URL_PATTERN.sub('[URL_MASKED]', record.msg)
        return True


def setup_logger(log_level: str = "INFO", mask_logs: bool = True) -> logging.Logger:
    """Set up the application logger. Call once at startup.

    Creates LOG_DIR if needed. Adds console + rotating file handlers.
    If already set up (has handlers), returns existing logger.
    """
    logger = logging.getLogger("reddiscribe")

    if logger.handlers:
        return logger

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger.setLevel(log_level)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    file_handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "reddiscribe.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    if mask_logs:
        sensitive_filter = SensitiveDataFilter()
        console_handler.addFilter(sensitive_filter)
        file_handler.addFilter(sensitive_filter)

    return logger


def get_logger() -> logging.Logger:
    """Get the application logger."""
    return logging.getLogger("reddiscribe")
