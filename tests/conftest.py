"""Pytest configuration and fixtures for orca-client tests."""

import pytest
import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import Dict, Any, AsyncGenerator

from orca_client.devices import DeviceRegistry, DeviceFactory
from orca_client.config.models import (
    DeviceConfig, DriverConfig, ConnectionConfig,
    PlatformConfig, ClientConfig
)


@pytest.fixture
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def sim_shaker_config() -> DeviceConfig:
    """Create a simulation shaker device config."""
    return DeviceConfig(
        type="shaker",
        name="test_shaker",
        driver=DriverConfig(type="sim")
    )


@pytest.fixture
def sim_centrifuge_config() -> DeviceConfig:
    """Create a simulation centrifuge device config."""
    return DeviceConfig(
        type="centrifuge",
        name="test_centrifuge",
        driver=DriverConfig(type="sim")
    )


@pytest.fixture
def platform_config() -> PlatformConfig:
    """Create a test platform config."""
    return PlatformConfig(
        url="wss://test.example.com/ws/devices",
        api_key="test_api_key",
        heartbeat_interval=10.0,
    )


@pytest.fixture
def client_config(
    platform_config: PlatformConfig,
    sim_shaker_config: DeviceConfig,
    sim_centrifuge_config: DeviceConfig
) -> ClientConfig:
    """Create a test client config with simulation devices."""
    return ClientConfig(
        client_id="test_client",
        site="test_site",
        lab="test_lab",
        platform=platform_config,
        devices=[sim_shaker_config, sim_centrifuge_config]
    )


@pytest.fixture
def device_factory() -> DeviceFactory:
    """Create a device factory."""
    return DeviceFactory()


@pytest.fixture
async def device_registry(
    device_factory: DeviceFactory,
    sim_shaker_config: DeviceConfig,
    sim_centrifuge_config: DeviceConfig
) -> AsyncGenerator[DeviceRegistry, None]:
    """Create a device registry with test devices."""
    registry = DeviceRegistry()

    # Register shaker
    shaker = device_factory.create_driver(sim_shaker_config)
    registry.register(
        name=sim_shaker_config.name,
        device_type=sim_shaker_config.type,
        driver=shaker,
    )

    # Register centrifuge
    centrifuge = device_factory.create_driver(sim_centrifuge_config)
    registry.register(
        name=sim_centrifuge_config.name,
        device_type=sim_centrifuge_config.type,
        driver=centrifuge,
    )

    # Initialize all
    await registry.initialize_all()

    yield registry

    # Cleanup
    await registry.cleanup_all()


@pytest.fixture
def recorded_sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record every ``asyncio.sleep`` duration and return instantly.

    Timing tests assert on the *requested* sleep, not measured wall-clock, so
    they stay deterministic and never flake under CI load.
    """
    durations: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay: float) -> None:
        durations.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return durations


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
