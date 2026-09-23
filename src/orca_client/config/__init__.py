"""Configuration package for orca-client."""

from .models import (
    ConnectionConfig,
    DriverConfig,
    DeviceConfig,
    PlatformConfig,
    ClientConfig,
)
from .loader import load_client_config
from .logging_setup import get_log_directory, setup_logging

# Alias for simpler import
load_config = load_client_config

__all__ = [
    "ConnectionConfig",
    "DriverConfig",
    "DeviceConfig",
    "PlatformConfig",
    "ClientConfig",
    "load_client_config",
    "load_config",
    "get_log_directory",
    "setup_logging",
]
