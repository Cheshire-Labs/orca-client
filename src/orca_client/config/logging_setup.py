"""Logging configuration for orca-client.

Sets up file and console logging with platform-specific log directories.
"""

import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def get_log_directory() -> Path:
    """Get platform-specific log directory.

    Returns:
        Windows: %LOCALAPPDATA%\\cheshire-labs\\orca-client\\logs
        macOS: ~/Library/Logs/orca-client
        Linux: ~/.local/share/orca-client/logs
    """
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", "C:\\ProgramData"))
        log_dir = base / "cheshire-labs" / "orca-client" / "logs"
    elif sys.platform == "darwin":
        log_dir = Path.home() / "Library" / "Logs" / "orca-client"
    else:
        base = Path.home() / ".local" / "share"
        log_dir = base / "orca-client" / "logs"

    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def setup_logging(verbose: bool = False) -> logging.Logger:
    """Setup logging with rotating file handler.

    Args:
        verbose: If True, set console to DEBUG level

    Returns:
        Configured logger instance
    """
    log_dir = get_log_directory()
    log_file = log_dir / "client.log"

    # Root logger for orca-client
    logger = logging.getLogger("orca_client")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)

    # Clear any existing handlers
    logger.handlers.clear()

    # File handler - rotating, 10MB max, keep 5 files
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5
    )
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    file_handler.setFormatter(file_formatter)

    # Console handler - less verbose
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter(
        '%(levelname)s: %(message)s'
    )
    console_handler.setFormatter(console_formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    logger.info(f"Logging to: {log_file}")

    return logger
