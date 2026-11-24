import logging

from cheshire_drivers.command_executor import CommandExecutor, DeviceRegistry
from cheshire_drivers.gateway_protocol import CommandMessage, ResponseMessage

logger = logging.getLogger("orca_client.executor")


class ClientCommandExecutor(CommandExecutor):
    """The shared executor, refusing a check-run command: a check run runs inside Orca and never reaches the wire."""

    def __init__(self, registry: DeviceRegistry) -> None:
        super().__init__(registry)
        self.registry = registry

    async def execute(self, cmd: CommandMessage) -> ResponseMessage:
        if cmd.effective_mode == "CHECK_RUN":
            logger.error(
                "CHECK_RUN command reached orca-client (device=%r, command=%r). "
                "A check run runs inside Orca and never goes over the wire.",
                cmd.device_name, cmd.command,
            )
            return self._error_response(
                cmd.command_id,
                (
                    f"CHECK_RUN commands must not reach orca-client "
                    f"(device={cmd.device_name!r}, command={cmd.command!r})"
                ),
                "CheckRunWireViolation",
            )
        return await super().execute(cmd)
