"""A simulated command takes the time the engine budgets for it, at the speed-up the wire dispatched it with."""

import time

import pytest

from cheshire_drivers import SimShakerDriver
from cheshire_drivers.gateway_protocol import AssignedDevice, CommandMessage, EffectiveMode
from cheshire_drivers.sims import SleepSim
from cheshire_drivers.command_executor import DeviceRegistry
from orca_client.executor import ClientCommandExecutor

# A two-second shake at ten times real speed: a fifth of a second on the wall.
NOMINAL_SECONDS = 2.0
SPEEDUP = 10.0
WAIT = NOMINAL_SECONDS / SPEEDUP


def _executor(sim_shaker_config: AssignedDevice) -> ClientCommandExecutor:
    registry = DeviceRegistry()
    registry.register(
        name=sim_shaker_config.name, device_type=sim_shaker_config.kind,
        # A stand-in live driver: a device built from this sim-only config has none.
        driver=SimShakerDriver(sim_shaker_config.name, SleepSim(0.0)),
        sim_driver=SimShakerDriver(sim_shaker_config.name, SleepSim(0.0)),
    )
    return ClientCommandExecutor(registry)


def _shake(mode: EffectiveMode, speedup: float | None, nominal: float | None = NOMINAL_SECONDS) -> CommandMessage:
    return CommandMessage(
        command_id="cmd_1", device_name="test_shaker", command="shake",
        params={"speed": 300.0, "duration": 5.0}, effective_mode=mode, sim_speedup=speedup, nominal_seconds=nominal,
    )


async def _seconds(executor: ClientCommandExecutor, command: CommandMessage) -> float:
    started = time.monotonic()
    response = await executor.execute(command)
    assert response.success, response.error
    return time.monotonic() - started


@pytest.mark.asyncio
async def test_a_sim_command_takes_its_budget_over_the_wire_speedup(sim_shaker_config: AssignedDevice) -> None:
    assert WAIT <= await _seconds(_executor(sim_shaker_config), _shake("SIM", SPEEDUP)) < WAIT + 2.0


@pytest.mark.asyncio
async def test_a_sim_command_does_not_wait_when_the_wire_carries_no_speedup(sim_shaker_config: AssignedDevice) -> None:
    assert await _seconds(_executor(sim_shaker_config), _shake("SIM", None)) < WAIT


@pytest.mark.asyncio
async def test_a_sim_command_the_engine_budgets_no_time_for_does_not_wait(sim_shaker_config: AssignedDevice) -> None:
    assert await _seconds(_executor(sim_shaker_config), _shake("SIM", SPEEDUP, nominal=None)) < WAIT


@pytest.mark.asyncio
async def test_a_live_command_is_never_paced(sim_shaker_config: AssignedDevice) -> None:
    assert await _seconds(_executor(sim_shaker_config), _shake("LIVE", SPEEDUP)) < WAIT


@pytest.mark.asyncio
async def test_time_the_simulator_already_spent_counts_toward_the_budget(sim_shaker_config: AssignedDevice) -> None:
    """A simulator that takes its own time is not made slower by the wait."""
    registry = DeviceRegistry()
    slow = SimShakerDriver(sim_shaker_config.name, SleepSim(WAIT))
    registry.register(name=sim_shaker_config.name, device_type=sim_shaker_config.kind, sim_driver=slow)

    assert WAIT <= await _seconds(ClientCommandExecutor(registry), _shake("SIM", SPEEDUP)) < 2 * WAIT
