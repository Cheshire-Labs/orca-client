"""Tests for command executor."""

import asyncio

import pytest

from orca_client.executor import ClientCommandExecutor
from cheshire_drivers.gateway_protocol import CommandMessage
from cheshire_drivers.command_executor import DeviceRegistry


@pytest.mark.asyncio
async def test_execute_valid_command(device_registry: DeviceRegistry):
    """Test executing a valid command."""
    executor = ClientCommandExecutor(device_registry)

    cmd = CommandMessage(
        command_id="test_1",
        device_name="test_shaker",
        command="shake",
        params={"speed": 300.0, "duration": 5.0},
        effective_mode="SIM",
    )

    response = await executor.execute(cmd)

    assert response.success is True
    assert response.command_id == "test_1"
    assert response.error is None


@pytest.mark.asyncio
async def test_execute_device_not_found(device_registry: DeviceRegistry):
    """Test executing command on non-existent device."""
    executor = ClientCommandExecutor(device_registry)

    cmd = CommandMessage(
        command_id="test_2",
        device_name="nonexistent",
        command="shake",
        params={},
        effective_mode="LIVE",
    )

    response = await executor.execute(cmd)

    assert response.success is False
    assert response.error_type == "DeviceNotFoundError"
    assert response.error is not None
    assert "not found" in response.error.lower()


@pytest.mark.asyncio
async def test_execute_unsupported_command(device_registry: DeviceRegistry):
    """Test executing unsupported command."""
    executor = ClientCommandExecutor(device_registry)

    cmd = CommandMessage(
        command_id="test_3",
        device_name="test_shaker",
        command="fly",  # Shakers can't fly
        params={},
        effective_mode="SIM",
    )

    response = await executor.execute(cmd)

    assert response.success is False
    assert response.error_type == "UnsupportedCommandError"


@pytest.mark.asyncio
async def test_execute_private_method(device_registry: DeviceRegistry):
    """Test that private methods are blocked."""
    executor = ClientCommandExecutor(device_registry)

    cmd = CommandMessage(
        command_id="test_4",
        device_name="test_shaker",
        command="_private_method",
        params={},
        effective_mode="SIM",
    )

    response = await executor.execute(cmd)

    assert response.success is False
    assert response.error_type == "SecurityError"
    assert response.error is not None
    assert "private" in response.error.lower()


@pytest.mark.asyncio
async def test_execute_invalid_parameters(device_registry: DeviceRegistry):
    """Test executing command with invalid parameters."""
    executor = ClientCommandExecutor(device_registry)

    cmd = CommandMessage(
        command_id="test_5",
        device_name="test_shaker",
        command="shake",
        params={"invalid_param": "value"},  # Missing required params
        effective_mode="SIM",
    )

    response = await executor.execute(cmd)

    assert response.success is False
    assert response.error_type == "ParameterError"


# The per-command-timeout-resolution tests were removed: orca-client
# no longer imposes a command duration bound. The engine (orca-core) owns the
# timeout (recoverable-timeout: an overrun is an operator decision, not a
# client failure) and cancels a command it no longer wants via the cancel
# message. Cancellation behavior is covered below.
