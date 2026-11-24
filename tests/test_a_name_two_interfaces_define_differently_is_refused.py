"""A command name two of a driver's interfaces define differently is refused before anything runs."""

import pytest

from cheshire_drivers.driver_hooks import DriverHooks
from cheshire_drivers.gateway_protocol import CommandMessage
from cheshire_drivers.command_executor import DeviceRegistry
from orca_client.executor import ClientCommandExecutor


class _HandlerThatIsAlsoWaste(DriverHooks):
    """`dispense` aspirates nothing on a waste bin and moves liquid on a handler."""

    interfaces = frozenset({"ILiquidHandler", "IWaste"})

    def __init__(self) -> None:
        self.dispensed = False

    async def dispense(self) -> None:
        self.dispensed = True


@pytest.mark.asyncio
async def test_the_refusal_names_both_interfaces_and_the_driver_is_not_called() -> None:
    driver = _HandlerThatIsAlsoWaste()
    registry = DeviceRegistry()
    registry.register(name="lh_1", device_type="liquid_handler", driver=driver)

    response = await ClientCommandExecutor(registry).execute(
        CommandMessage(command_id="c1", device_name="lh_1", command="dispense", params={}, effective_mode="LIVE")
    )

    assert response.success is False
    assert response.error_type == "AmbiguousCommandError"
    assert response.error is not None and "ILiquidHandler" in response.error and "IWaste" in response.error
    assert driver.dispensed is False
