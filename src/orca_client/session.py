"""One run of the client: drivers, executor, and the platform connection.

Anything that runs the client goes through this, so every exit releases the
device drivers. Drivers hold serial ports and vendor simulator subprocesses;
an exit that skips cleanup leaves them behind.
"""

import logging
from pathlib import Path

from cheshire_drivers.command_executor import DeviceRegistry
from cheshire_drivers.gateway_protocol import AssignmentResult, DeviceAssignment
from typing_extensions import Self

from .client import WebSocketClient
from .config import ClientConfig, load_config
from .devices import DeviceFactory
from .executor import ClientCommandExecutor

logger = logging.getLogger("orca_client.session")


class ClientSession:
    """Everything one connection to the platform needs, built from a config."""

    def __init__(self, config: ClientConfig) -> None:
        self.config = config
        self.registry = DeviceRegistry()

        self._factory = DeviceFactory(machine=config.machine, enable_plr_visualizer=config.enable_plr_visualizer)

        # No client-side command timeout: the engine owns command duration
        # bounds and cancels via the wire.
        self.executor = ClientCommandExecutor(self.registry)
        self.client = WebSocketClient(config=config, executor=self.executor, apply_assignment=self.apply_assignment)

    async def apply_assignment(self, assignment: DeviceAssignment) -> AssignmentResult:
        """Run exactly the assigned devices, and report what was built and what was not."""
        errors = await self.registry.apply(assignment.devices, self._factory.create_drivers)
        return AssignmentResult(
            assignment_id=assignment.assignment_id, devices=self.registry.connect_info_list(), errors=errors,
        )

    @classmethod
    def from_config_path(cls, config_path: Path) -> Self:
        """Load a config file and build a session from it."""
        resolved = config_path.resolve()
        logger.info(f"Loading configuration from {resolved}...")
        config = load_config(str(resolved))
        logger.info(f"Loaded configuration for client: {config.client_id}")
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
