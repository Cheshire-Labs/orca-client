"""Configuration loader for orca-client.

Loads configuration from a single config.json file.
Environment variables can provide fallback values for platform settings.

Note: .env file loading is handled by __main__.py via --env flag.
If no --env flag specified, uses OS environment variables directly.
"""

import os
import json
import re
from pathlib import Path

from .models import ClientConfig, PlatformConfig


def _expand_env_vars(value: str, config_path: str) -> str:
    """Expand ${VAR} syntax from environment variables.

    An unset variable is refused: left as text, `${VAR}` would be sent as the
    value itself, and an API key sent that way is rejected with no hint why.
    There is no escape for a literal `${`; issued API keys never contain one.
    """
    if not isinstance(value, str):
        return value
    pattern = r'\$\{([^}]+)\}'
    def replacer(match):
        var_name = match.group(1)
        found = os.getenv(var_name)
        if found is None:
            raise ValueError(
                f"{config_path} uses ${{{var_name}}}, but the environment variable "
                f"{var_name} is not set."
            )
        return found
    return re.sub(pattern, replacer, value)


def load_client_config(config_path: str) -> ClientConfig:
    """Load client configuration from config.json.

    Configuration priority (highest to lowest):
    1. Config file values (config.json)
    2. Environment variables (ORCA_CLIENT_URL, ORCA_CLIENT_API_KEY, etc.)
    3. Model defaults

    Args:
        config_path: Path to config.json file.

    Returns:
        Validated ClientConfig instance

    Raises:
        FileNotFoundError: If config file doesn't exist
        ValueError: If required fields (url, api_key) are missing
    """
    if not Path(config_path).exists():
        raise FileNotFoundError(
            f"Configuration file not found: {config_path}\n"
            f"Create a config.json file in your working directory.\n"
            f"See examples/config.example.json for the expected format."
        )

    with open(config_path, 'r') as f:
        config_data = json.load(f)

    # Get platform config from file (or empty dict if not present)
    platform_data = config_data.get("platform", {})

    # Build platform config: file values override env vars, model provides defaults
    # Priority: config file (with ${VAR} expansion) > env var > model default
    url = _expand_env_vars(platform_data.get("url", ""), config_path) or os.getenv("ORCA_CLIENT_URL", "")
    api_key = _expand_env_vars(platform_data.get("api_key", ""), config_path) or os.getenv("ORCA_CLIENT_API_KEY", "")

    platform = PlatformConfig(
        url=url,
        api_key=api_key,
        # Optional fields - only pass if explicitly set, otherwise use model defaults
        **_optional_platform_fields(platform_data)
    )

    # Validate required fields
    if not platform.url:
        raise ValueError(
            "url is required.\n"
            f"Add 'url' to the 'platform' section in {config_path}:\n"
            '  "platform": { "url": "ws://127.0.0.1:8765/ws/devices", "api_key": "..." }\n'
            "Or set the ORCA_CLIENT_URL environment variable."
        )

    if not platform.api_key:
        raise ValueError(
            "api_key is required.\n"
            f"Add 'api_key' to the 'platform' section in {config_path}:\n"
            '  "platform": { "url": "...", "api_key": "your-api-key" }\n'
            "Or set the ORCA_CLIENT_API_KEY environment variable."
        )

    # Build full config - let Pydantic validate the rest
    return ClientConfig(
        client_id=config_data.get("client_id", ""),
        site=config_data.get("site", ""),
        lab=config_data.get("lab", ""),
        workcell=config_data.get("workcell"),
        enable_plr_visualizer=config_data.get("enable_plr_visualizer", False),
        platform=platform,
        devices=config_data.get("devices", [])
    )


def _optional_platform_fields(platform_data: dict) -> dict:
    """Extract optional platform fields, only including those explicitly set.

    This allows the PlatformConfig model defaults to be used when fields aren't specified.
    """
    fields = {}

    if "reconnect_backoff" in platform_data:
        fields["reconnect_backoff"] = platform_data["reconnect_backoff"]
    elif (reconnect_backoff := os.getenv("ORCA_CLIENT_RECONNECT_BACKOFF")):
        fields["reconnect_backoff"] = _env_seconds_list("ORCA_CLIENT_RECONNECT_BACKOFF", reconnect_backoff)

    if "heartbeat_interval" in platform_data:
        fields["heartbeat_interval"] = platform_data["heartbeat_interval"]
    elif (heartbeat_interval := os.getenv("ORCA_CLIENT_HEARTBEAT_INTERVAL")):
        fields["heartbeat_interval"] = _env_seconds("ORCA_CLIENT_HEARTBEAT_INTERVAL", heartbeat_interval)

    return fields


def _env_seconds(name: str, text: str) -> float:
    """A number of seconds read from environment variable `name`, refused by name if it is not one."""
    try:
        return float(text)
    except ValueError:
        raise ValueError(
            f"The environment variable {name} must be a number of seconds, such as 30. It is {text!r}."
        ) from None


def _env_seconds_list(name: str, text: str) -> list[float]:
    """Comma-separated seconds read from environment variable `name`, refused by name if one is not a number."""
    try:
        return [float(part) for part in text.split(",")]
    except ValueError:
        raise ValueError(
            f"The environment variable {name} must be comma-separated seconds, such as 1,2,5. It is {text!r}."
        ) from None
