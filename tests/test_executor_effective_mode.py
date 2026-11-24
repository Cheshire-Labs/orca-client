"""Tests for effective_mode dispatch in CommandExecutor.

Coverage:
  * `effective_mode = LIVE` dispatches to the configured driver in
    `DeviceRegistry._drivers`.
  * `effective_mode = SIM` dispatches to the paired
    `DeviceRegistry._sim_drivers` instance.

The test shaker has no real driver, so a stand-in live driver is registered
directly; both slots hold a sim class, told apart by identity.
  * `effective_mode = CHECK_RUN` is rejected as a wire-protocol violation
    (CHECK_RUN is handled server-side and must never reach the wire).
"""

from typing import AsyncGenerator

import pytest

from cheshire_drivers.driver_errors import InstrumentOutcome
from cheshire_drivers import SimShakerDriver
from cheshire_drivers.command_executor import DeviceRegistry
from orca_client.devices import DeviceFactory
from cheshire_drivers.gateway_protocol import AssignedDevice
from orca_client.executor import ClientCommandExecutor
from cheshire_drivers.gateway_protocol import CommandMessage


@pytest.fixture
async def shaker_registry(
    device_factory: DeviceFactory,
    sim_shaker_config: AssignedDevice,
) -> AsyncGenerator[DeviceRegistry, None]:
    """Registry with a single shaker and an initialized stand-in live driver."""
    registry = DeviceRegistry()
    live = SimShakerDriver(sim_shaker_config.name)
    registry.register(
        name=sim_shaker_config.name,
        device_type=sim_shaker_config.kind,
        driver=live,
        sim_driver=device_factory.create_sim_driver(sim_shaker_config),
    )
    await live.initialize()
    yield registry
    await registry.cleanup_all()


@pytest.mark.asyncio
async def test_check_run_rejected_as_wire_violation(
    shaker_registry: DeviceRegistry,
) -> None:
    """A CommandMessage with effective_mode=CHECK_RUN must fail loudly.

    CHECK_RUN dispatches are handled server-side and must never reach the
    wire. When orca-client sees one, refuse with a distinct error_type
    so the gateway knows there's a server-side bug.
    """
    executor = ClientCommandExecutor(shaker_registry)
    cmd = CommandMessage(
        command_id="cmd_check_run",
        device_name="test_shaker",
        command="shake",
        params={"speed": 300.0, "duration": 5.0},
        effective_mode="CHECK_RUN",
    )
    response = await executor.execute(cmd)
    assert not response.success
    assert response.error_type == "CheckRunWireViolation"
    assert "CHECK_RUN" in (response.error or "")
    # Nothing ran, so the device must not be faulted for it.
    assert response.instrument_outcome is InstrumentOutcome.REJECTED


@pytest.mark.asyncio
async def test_live_dispatches_to_configured_driver(
    shaker_registry: DeviceRegistry,
) -> None:
    """LIVE picks the registered driver, not the paired sim."""
    live_driver = shaker_registry.get_driver("test_shaker")
    sim_driver = shaker_registry.get_sim_driver("test_shaker")
    assert live_driver is not None
    assert sim_driver is not None
    assert live_driver is not sim_driver

    executor = ClientCommandExecutor(shaker_registry)
    cmd = CommandMessage(
        command_id="cmd_live",
        device_name="test_shaker",
        command="shake",
        params={"speed": 300.0, "duration": 5.0},
        effective_mode="LIVE",
    )
    response = await executor.execute(cmd)
    assert response.success, f"Wire broken: {response.error}"


@pytest.mark.asyncio
async def test_sim_dispatches_to_paired_sim(
    shaker_registry: DeviceRegistry,
) -> None:
    """SIM picks the paired Sim* driver, not the configured driver."""
    sim_driver = shaker_registry.get_sim_driver("test_shaker")
    assert sim_driver is not None

    executor = ClientCommandExecutor(shaker_registry)
    cmd = CommandMessage(
        command_id="cmd_sim",
        device_name="test_shaker",
        command="shake",
        params={"speed": 300.0, "duration": 5.0},
        effective_mode="SIM",
    )
    response = await executor.execute(cmd)
    assert response.success, f"Wire broken: {response.error}"


@pytest.mark.asyncio
async def test_sim_fails_when_no_sim_driver_for_type() -> None:
    """An unknown device type yields no paired sim driver; SIM fails."""
    registry = DeviceRegistry()
    registry.register(
        name="weird_dev",
        device_type="furnace",
        driver=SimShakerDriver("weird_dev"),
    )

    executor = ClientCommandExecutor(registry)
    cmd = CommandMessage(
        command_id="cmd_no_sim",
        device_name="weird_dev",
        command="shake",
        params={},
        effective_mode="SIM",
    )
    response = await executor.execute(cmd)
    assert not response.success
    assert response.error_type == "SimDriverNotFoundError"
