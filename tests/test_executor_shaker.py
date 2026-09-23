"""Shaker executor wire-shape tests.

Regression guard. Mirrors `test_executor_transporter.py` for the
shaker category: dispatches every shaker command via CommandExecutor against
a real SimShakerDriver (which now expects Pydantic Request models) with
flat-dict params from the wire.
"""

import pytest

from orca_client.executor import CommandExecutor
from cheshire_drivers.gateway_protocol import CommandMessage


class TestShakerExecutor:
    """Wire-shape regression: every shaker command dispatches with dict params."""

    @pytest.mark.asyncio
    async def test_shake_with_dict_params_succeeds(self, device_registry) -> None:
        executor = CommandExecutor(device_registry)
        cmd = CommandMessage(
            command_id="t1",
            device_name="test_shaker",
            command="shake",
            params={"speed": 500.0, "duration": 2.0},
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert response.success is True, response.error

    @pytest.mark.asyncio
    async def test_lock_plate_with_empty_dict_succeeds(self, device_registry) -> None:
        executor = CommandExecutor(device_registry)
        cmd = CommandMessage(
            command_id="t2",
            device_name="test_shaker",
            command="lock_plate",
            params={},
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert response.success is True, response.error

    @pytest.mark.asyncio
    async def test_unlock_plate_with_empty_dict_succeeds(self, device_registry) -> None:
        executor = CommandExecutor(device_registry)
        cmd = CommandMessage(
            command_id="t3",
            device_name="test_shaker",
            command="unlock_plate",
            params={},
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert response.success is True, response.error

    @pytest.mark.asyncio
    async def test_stop_shaking_with_empty_dict_succeeds(self, device_registry) -> None:
        executor = CommandExecutor(device_registry)
        cmd = CommandMessage(
            command_id="t4",
            device_name="test_shaker",
            command="stop_shaking",
            params={},
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert response.success is True, response.error

    @pytest.mark.asyncio
    async def test_shake_with_unknown_field_fails(self, device_registry) -> None:
        executor = CommandExecutor(device_registry)
        cmd = CommandMessage(
            command_id="t5",
            device_name="test_shaker",
            command="shake",
            params={"speed": 500.0, "duration": 2.0, "bogus": True},
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert response.success is False
        assert response.error_type == "ParameterError"

    @pytest.mark.asyncio
    async def test_shake_missing_required_field_fails(self, device_registry) -> None:
        executor = CommandExecutor(device_registry)
        cmd = CommandMessage(
            command_id="t6",
            device_name="test_shaker",
            command="shake",
            params={"speed": 500.0},
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert response.success is False
        assert response.error_type == "ParameterError"

    @pytest.mark.asyncio
    async def test_initialize_on_shaker_does_not_wrap_as_transporter(self, device_registry) -> None:
        """Latent bug regression: command='initialize' on a non-transporter
        device must dispatch to the parameterless BaseDriver.initialize, not
        get wrapped as a transporter InitializeRequest. Without the
        interface-aware gate in _deserialize_params, the executor would call
        shaker.initialize(request=InitializeRequest()) -> TypeError.
        """
        executor = CommandExecutor(device_registry)
        cmd = CommandMessage(
            command_id="t7",
            device_name="test_shaker",
            command="initialize",
            params={},
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert response.success is True, response.error
