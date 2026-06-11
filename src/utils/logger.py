"""
Centralised logging configuration for the entire application.
Call get_logger(__name__) in every module instead of using print().
"""

import logging
import sys
from typing import Optional


def configure_logging(level: str = "INFO", log_file: Optional[str] = None) -> None:
    """
    Set up root logger with console (and optional file) handlers.

    Should be called once at application startup (in main.py).

    Args:
        level: Logging level string, e.g. "DEBUG", "INFO", "WARNING".
        log_file: Optional path for a rotating file handler.
    """
    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    if log_file:
        from logging.handlers import RotatingFileHandler
        handlers.append(RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=3))

    logging.basicConfig(level=level.upper(), format=fmt, handlers=handlers)


def get_logger(name: str) -> logging.Logger:
    """
    Return a named logger.

    Args:
        name: Typically __name__ of the calling module.

    Returns:
        Configured Logger instance.
    """
    return logging.getLogger(name)
