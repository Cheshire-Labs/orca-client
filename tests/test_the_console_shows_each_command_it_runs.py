"""The command executor lives in cheshire-drivers; its log is still orca-client's log."""

from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest
from cheshire_drivers.command_executor.executor import logger as command_executor_logger

from orca_client.config.logging_setup import setup_logging


def test_the_executors_executing_line_reaches_the_console_and_the_file(capsys: pytest.CaptureFixture[str]) -> None:
    orca_client_logger = setup_logging(log_name="executor-log-test.log")

    command_executor_logger.info("Executing: shake on shaker_1 (effective_mode=SIM)")

    assert "INFO: Executing: shake on shaker_1 (effective_mode=SIM)" in capsys.readouterr().err.splitlines()
    file_handler = next(h for h in orca_client_logger.handlers if isinstance(h, RotatingFileHandler))
    file_handler.flush()
    assert "Executing: shake on shaker_1" in Path(file_handler.baseFilename).read_text()
