"""Thermocycler executor wire-shape tests.

Mirrors `test_executor_shaker.py` for the thermocycler category: dispatches
thermocycler commands via CommandExecutor against a real SimThermocyclerDriver
(which expects Pydantic Request models) with flat-dict params from the wire.
Proves the executor routes thermocycler commands through
`wrap_thermocycler_payload` only when the driver advertises `IThermocycler`,
and leaves the payload untouched otherwise.
"""

from typing import AsyncGenerator

import pytest

from cheshire_drivers import SimThermocyclerDriver
from cheshire_drivers.gateway_protocol import CommandMessage
from orca_client.devices import DeviceRegistry
from orca_client.executor import CommandExecutor


_PROTOCOL_PARAMS = {
    "protocol": {
        "stages": [
            {
                "steps": [
                    {"temperature": [95.0], "hold_seconds": 30.0},
                    {"temperature": [60.0], "hold_seconds": 45.0},
                ],
                "repeats": 2,
            }
        ]
    },
    "block_max_volume": 25.0,
}


@pytest.fixture
async def thermocycler_registry() -> AsyncGenerator[DeviceRegistry, None]:
    registry = DeviceRegistry()
    registry.register(
        name="test_thermocycler",
        device_type="thermocycler",
        driver=SimThermocyclerDriver("test_thermocycler"),
    )
    await registry.initialize_all()
    yield registry
    await registry.cleanup_all()


class TestThermocyclerExecutor:
    """Wire-shape regression: thermocycler commands dispatch with dict params."""

    @pytest.mark.asyncio
    async def test_run_protocol_with_dict_params_succeeds(
        self, thermocycler_registry: DeviceRegistry
    ) -> None:
        executor = CommandExecutor(thermocycler_registry)
        cmd = CommandMessage(
            command_id="tc1",
            device_name="test_thermocycler",
            command="run_protocol",
            params=_PROTOCOL_PARAMS,
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert response.success is True, response.error

    @pytest.mark.asyncio
    async def test_get_lid_open_returns_typed_response(
        self, thermocycler_registry: DeviceRegistry
    ) -> None:
        executor = CommandExecutor(thermocycler_registry)
        cmd = CommandMessage(
            command_id="tc2",
            device_name="test_thermocycler",
            command="get_lid_open",
            params={},
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert response.success is True, response.error
        assert isinstance(response.result, dict)
        assert "open" in response.result
        assert isinstance(response.result["open"], bool)

    @pytest.mark.asyncio
    async def test_run_protocol_with_unknown_field_fails(
        self, thermocycler_registry: DeviceRegistry
    ) -> None:
        executor = CommandExecutor(thermocycler_registry)
        cmd = CommandMessage(
            command_id="tc3",
            device_name="test_thermocycler",
            command="run_protocol",
            params={**_PROTOCOL_PARAMS, "bogus": True},
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert response.success is False
        assert response.error_type == "ParameterError"

    def test_thermocycler_command_wrapped_when_interface_advertised(
        self, thermocycler_registry: DeviceRegistry
    ) -> None:
        """A thermocycler command on an IThermocycler driver is wrapped under
        the Request kwarg, so the flat wire dict becomes a Pydantic Request."""
        executor = CommandExecutor(thermocycler_registry)
        driver = thermocycler_registry.get_driver("test_thermocycler")
        wrapped = executor._deserialize_params("get_lid_open", {}, driver)
        assert set(wrapped.keys()) == {"request"}

    @pytest.mark.asyncio
    async def test_thermocycler_command_passes_through_on_non_thermocycler(
        self, device_registry: DeviceRegistry
    ) -> None:
        """A thermocycler command name on a driver that does NOT advertise
        IThermocycler (the shaker) must not be wrapped as a thermocycler
        Request: `_deserialize_params` returns the params unchanged, and the
        executor rejects the command as unsupported rather than mis-wrapping."""
        executor = CommandExecutor(device_registry)
        shaker = device_registry.get_driver("test_shaker")
        params = {"unrelated": 1}
        assert executor._deserialize_params("get_lid_open", params, shaker) is params

        cmd = CommandMessage(
            command_id="tc4",
            device_name="test_shaker",
            command="get_lid_open",
            params={},
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert response.success is False
        assert response.error_type == "UnsupportedCommandError"
