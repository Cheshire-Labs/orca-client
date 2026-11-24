"""Call the executor's parameter step the way `execute` does: look the command up on the driver's interfaces first."""

from typing import Protocol

from cheshire_drivers.command_table import COMMANDS, DriverKwargs
from pydantic import JsonValue

from orca_client.executor import ClientCommandExecutor


class _DeclaresInterfaces(Protocol):
    interfaces: frozenset[str]


def deserialize(
    executor: ClientCommandExecutor, command: str, params: dict[str, JsonValue], driver: _DeclaresInterfaces,
) -> DriverKwargs:
    return executor._deserialize_params(params, COMMANDS.find(driver.interfaces, command))
