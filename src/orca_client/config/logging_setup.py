"""Logging configuration for orca-client.

Sets up file and console logging with platform-specific log directories.
"""

import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from cheshire_drivers.command_executor.executor import logger as command_executor_logger


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


def log_known_failure(logger: logging.Logger, message: str) -> None:
    """Report a failure whose message already says what to fix, with no traceback.

    The trace for these is fifteen frames of asyncio and websockets internals,
    which buries the one line the operator has to read. ``--verbose`` keeps it.
    """
    if logger.isEnabledFor(logging.DEBUG):
        logger.error(message, exc_info=True)
    else:
        logger.error(message)


_CLIENT_LOGGERS = ("orca_client", command_executor_logger.name)


def setup_logging(verbose: bool = False, log_name: str = "client.log") -> logging.Logger:
    """Log to the console and a rotating file. Each process on a PC needs its own file: Windows will not rotate a shared one."""
    log_dir = get_log_directory()
    log_file = log_dir / log_name

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

    # The command executor that runs every device command lives in cheshire-drivers.
    for name in _CLIENT_LOGGERS:
        client_logger = logging.getLogger(name)
        client_logger.setLevel(logging.DEBUG if verbose else logging.INFO)
        client_logger.handlers.clear()
        client_logger.addHandler(file_handler)
        client_logger.addHandler(console_handler)

    logger = logging.getLogger("orca_client")
    logger.info(f"Logging to: {log_file}")

    return logger
