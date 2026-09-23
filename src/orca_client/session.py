"""One run of the client: drivers, executor, and the platform connection.

Anything that runs the client goes through this, so every exit releases the
device drivers. Drivers hold serial ports and vendor simulator subprocesses;
an exit that skips cleanup leaves them behind.
"""

import logging
from pathlib import Path

from .client import WebSocketClient
from .config import ClientConfig, load_config
from .devices import DeviceFactory, DeviceRegistry
from .executor import CommandExecutor

logger = logging.getLogger("orca_client.session")


class ClientSession:
    """Everything one connection to the platform needs, built from a config."""

    def __init__(self, config: ClientConfig) -> None:
        self.config = config
        self.registry = DeviceRegistry()

        factory = DeviceFactory(enable_plr_visualizer=config.enable_plr_visualizer)
        logger.info("Creating device drivers...")
        for device_config in config.devices:
            self.registry.register(
                name=device_config.name,
                device_type=device_config.type,
                driver=factory.create_driver(device_config),
                sim_driver=factory.create_sim_driver(device_config),
            )
            logger.info(f"  • {device_config.name} ({device_config.type})")

        # Devices stay uninitialized here: the platform calls initialize() when
        # it is ready, so it controls the timing and sees the status updates.
        logger.info("Devices registered (not yet initialized - platform will call initialize)")

        # No client-side command timeout: the engine owns command duration
        # bounds and cancels via the wire.
        self.executor = CommandExecutor(registry=self.registry)
        self.client = WebSocketClient(config=config, executor=self.executor)

    @classmethod
    def from_config_path(cls, config_path: Path) -> "ClientSession":
        """Load a config file and build a session from it."""
        resolved = config_path.resolve()
        logger.info(f"Loading configuration from {resolved}...")
        config = load_config(str(resolved))
        logger.info(f"Loaded configuration for client: {config.client_id}")
        logger.info(f"Registered {len(config.devices)} device(s)")
        return cls(config)

    async def run(self) -> None:
        """Connect and stay connected until stopped, then release every driver."""
        try:
            await self.client.start()
        finally:
            await self.registry.cleanup_all()

    async def stop(self) -> None:
        """Ask the client to disconnect; ``run`` then finishes and cleans up."""
        await self.client.stop()
