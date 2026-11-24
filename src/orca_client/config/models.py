"""Configuration models for orca-client.

Uses Pydantic for validation of JSON configuration files.
"""

from pydantic import BaseModel, Field, PositiveFloat, field_validator
from typing import Optional, List
from urllib.parse import urlparse

from cheshire_drivers.device_setup import MachineSetup
from cheshire_drivers.gateway_protocol import HEARTBEAT_INTERVAL_SECONDS


LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")


class PlatformConfig(BaseModel):
    """Connection to the Orca gateway."""
    url: str = Field(..., description="WebSocket URL (wss://... or ws://localhost)")
    api_key: str = Field(..., description="API key for authentication")
    reconnect_backoff: List[PositiveFloat] = Field(
        default=[1, 2, 4, 8, 16, 30],
        min_length=1,
        description="Seconds to wait before each redial after a failure; the last value repeats",
    )
    heartbeat_interval: float = Field(
        default=HEARTBEAT_INTERVAL_SECONDS,
        le=HEARTBEAT_INTERVAL_SECONDS,
        description="Heartbeat interval in seconds, at most the default: the server counts a client gone after three default intervals",
    )

    @field_validator("url")
    @classmethod
    def _must_be_a_websocket_url(cls, url: str) -> str:
        """Refuse a URL no dial could ever accept, here rather than once per redial."""
        parsed = urlparse(url)
        if parsed.scheme not in ("ws", "wss"):
            raise ValueError(
                f"The platform url must start with ws:// or wss://. It is {url!r}. "
                f"A local Orca runtime listens at ws://127.0.0.1:<port>/ws/devices."
            )
        if not parsed.hostname:
            raise ValueError(
                f"The platform url names no host: {url!r}. "
                f"A local Orca runtime listens at ws://127.0.0.1:<port>/ws/devices."
            )
        return url

    @field_validator("url")
    @classmethod
    def _remote_hosts_need_wss(cls, url: str) -> str:
        """Refuse plain ws:// to another host here, so the agent never starts rather than redialing forever."""
        parsed = urlparse(url)
        if parsed.scheme == "ws" and parsed.hostname not in LOOPBACK_HOSTS:
            raise ValueError(
                f"Insecure WebSocket (ws://) not allowed for remote hosts. "
                f"Use wss:// for {parsed.hostname}"
            )
        return url


class ClientConfig(BaseModel):
    """Complete client configuration."""
    client_id: str = Field(..., description="Unique client identifier")
    site: str = Field(..., description="Geographic site location")
    lab: str = Field(..., description="Lab or workcell name")
    workcell: Optional[str] = Field(default=None, description="Optional workcell identifier")
    enable_plr_visualizer: bool = Field(
        default=False,
        description="When True, PLR liquid handlers open the PLR Visualizer at localhost:1337."
    )
    platform: PlatformConfig
    machine: MachineSetup = Field(
        default_factory=MachineSetup,
        description="Programs and folders on this PC. Never sent by the server.",
    )
