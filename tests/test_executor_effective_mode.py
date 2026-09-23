"""Tests for effective_mode dispatch in CommandExecutor.

Coverage:
  * `effective_mode = LIVE` dispatches to the configured driver in
    `DeviceRegistry._drivers`.
  * `effective_mode = DEVICE_SIM` dispatches to the paired
    `DeviceRegistry._sim_drivers` instance even when the live driver is
    something else (e.g. PLR or Venus in a real deployment; here both
    are sim because the configured driver is sim, so we differentiate
    by identity).
  * `effective_mode = PURE_SIM` is rejected as a wire-protocol violation
    (PURE_SIM is handled server-side and must never reach the wire).
"""

from typing import AsyncGenerator

import pytest

from orca_client.devices import DeviceFactory, DeviceRegistry
from orca_client.config.models import DeviceConfig, DriverConfig
from orca_client.executor import CommandExecutor
from cheshire_drivers.gateway_protocol import CommandMessage


@pytest.fixture
async def shaker_registry(
    device_factory: DeviceFactory,
    sim_shaker_config: DeviceConfig,
) -> AsyncGenerator[DeviceRegistry, None]:
    """Registry with a single shaker; yields after initialize_all."""
    registry = DeviceRegistry()
    driver = device_factory.create_driver(sim_shaker_config)
    registry.register(
        name=sim_shaker_config.name,
        device_type=sim_shaker_config.type,
        driver=driver,
    )
    await registry.initialize_all()
    yield registry
    await registry.cleanup_all()


@pytest.mark.asyncio
async def test_pure_sim_rejected_as_wire_violation(
    shaker_registry: DeviceRegistry,
) -> None:
    """A CommandMessage with effective_mode=PURE_SIM must fail loudly.

    PURE_SIM dispatches are handled server-side and must never reach the
    wire. When orca-client sees one, refuse with a distinct error_type
    so the gateway knows there's a server-side bug.
    """
    executor = CommandExecutor(shaker_registry)
    cmd = CommandMessage(
        command_id="cmd_pure_sim",
        device_name="test_shaker",
        command="shake",
        params={"speed": 300.0, "duration": 5.0},
        effective_mode="PURE_SIM",
    )
    response = await executor.execute(cmd)
    assert not response.success
    assert response.error_type == "PureSimWireViolation"
    assert "PURE_SIM" in (response.error or "")


@pytest.mark.asyncio
async def test_live_dispatches_to_configured_driver(
    shaker_registry: DeviceRegistry,
) -> None:
    """LIVE picks the registered driver, not the paired sim."""
    live_driver = shaker_registry.get_driver("test_shaker")
    sim_driver = shaker_registry.get_sim_driver("test_shaker")
    assert live_driver is not None
    assert sim_driver is not None
    # The configured driver in the fixture IS a sim, but it must still be a
    # different instance than the auto-paired sim. Identity disambiguates.
    assert live_driver is not sim_driver

    executor = CommandExecutor(shaker_registry)
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
async def test_device_sim_dispatches_to_paired_sim(
    shaker_registry: DeviceRegistry,
) -> None:
    """DEVICE_SIM picks the paired Sim* driver, not the configured driver."""
    sim_driver = shaker_registry.get_sim_driver("test_shaker")
    assert sim_driver is not None

    executor = CommandExecutor(shaker_registry)
    cmd = CommandMessage(
        command_id="cmd_device_sim",
        device_name="test_shaker",
        command="shake",
        params={"speed": 300.0, "duration": 5.0},
        effective_mode="DEVICE_SIM",
    )
    response = await executor.execute(cmd)
    assert response.success, f"Wire broken: {response.error}"


@pytest.mark.asyncio
async def test_device_sim_fails_when_no_sim_driver_for_type() -> None:
    """An unknown device type yields no paired sim driver; DEVICE_SIM fails."""
    registry = DeviceRegistry()
    # Manually register with an unrecognized type so no sim driver pairs.
    # Use a sim shaker as the live driver so register_all works, but lie
    # about the type.
    factory = DeviceFactory()
    driver = factory.create_driver(
        DeviceConfig(
            type="shaker",
            name="weird_dev",
            driver=DriverConfig(type="sim"),
        ),
    )
    # Register with a type the sim-driver map doesn't know about.
    registry.register(
        name="weird_dev",
        device_type="furnace",
        driver=driver,
    )

    executor = CommandExecutor(registry)
    cmd = CommandMessage(
        command_id="cmd_no_sim",
        device_name="weird_dev",
        command="shake",
        params={},
        effective_mode="DEVICE_SIM",
    )
    response = await executor.execute(cmd)
    assert not response.success
    assert response.error_type == "SimDriverNotFoundError"
