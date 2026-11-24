"""Pytest configuration and fixtures for orca-client tests."""

import pytest
import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import AsyncGenerator

from cheshire_drivers.command_executor import DeviceRegistry
from orca_client.devices import DeviceFactory
from orca_client.config.models import PlatformConfig, ClientConfig
from cheshire_drivers.gateway_protocol import AssignedDevice


@pytest.fixture
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def sim_shaker_config() -> AssignedDevice:
    """Create a simulation shaker device config."""
    return AssignedDevice(kind="shaker", name="test_shaker")


@pytest.fixture
def sim_centrifuge_config() -> AssignedDevice:
    """Create a simulation centrifuge device config."""
    return AssignedDevice(kind="centrifuge", name="test_centrifuge")


@pytest.fixture
def platform_config() -> PlatformConfig:
    """Create a test platform config."""
    return PlatformConfig(
        url="wss://test.example.com/ws/devices",
        api_key="test_api_key",
        heartbeat_interval=10.0,
    )


@pytest.fixture
def client_config(platform_config: PlatformConfig) -> ClientConfig:
    """Create a test client config."""
    return ClientConfig(
        client_id="test_client",
        site="test_site",
        lab="test_lab",
        platform=platform_config,
    )


@pytest.fixture
def device_factory() -> DeviceFactory:
    """Create a device factory."""
    return DeviceFactory()


@pytest.fixture
async def device_registry(
    device_factory: DeviceFactory,
    sim_shaker_config: AssignedDevice,
    sim_centrifuge_config: AssignedDevice
) -> AsyncGenerator[DeviceRegistry, None]:
    """A registry with a sim-only shaker and centrifuge: neither has a real driver, so commands to them run as SIM."""
    registry = DeviceRegistry()
    for config in (sim_shaker_config, sim_centrifuge_config):
        registry.register(
            name=config.name,
            device_type=config.kind,
            sim_driver=device_factory.create_sim_driver(config),
        )

    yield registry

    await registry.cleanup_all()


@pytest.fixture
def wait_until() -> Callable[..., Awaitable[None]]:
    """Return ``await wait_until(pred, timeout=...)``: drive the event loop until
    ``pred()`` (sync or async) is truthy.

    Condition-driven, not time-driven, so it never flakes under load; the
    timeout is only a safety ceiling so a genuine hang fails loudly instead of
    spinning forever.
    """
    async def _wait_until(
        predicate: Callable[[], bool | Awaitable[bool]],
        *,
        timeout: float = 5.0,
    ) -> None:
        async def _run() -> None:
            while True:
                result = predicate()
                if inspect.isawaitable(result):
                    result = await result
                if result:
                    return
                await asyncio.sleep(0)

        await asyncio.wait_for(_run(), timeout=timeout)

    return _wait_until
