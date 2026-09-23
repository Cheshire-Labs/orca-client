"""Tests for device registry."""

import pytest
from cheshire_drivers import (
    SimLiquidHandlerDriver,
    SimShakerDriver,
    SimStorageDriver,
    SimTransporterDriver,
    SimWasteDriver,
)
from cheshire_drivers.transporter_models import InitializeRequest

from orca_client.devices import DeviceRegistry, DeviceFactory
from orca_client.config.models import DeviceConfig, DriverConfig


@pytest.mark.asyncio
async def test_register_device(device_factory: DeviceFactory, sim_shaker_config: DeviceConfig):
    """Test registering a device."""
    registry = DeviceRegistry()
    driver = device_factory.create_driver(sim_shaker_config)

    registry.register(
        name=sim_shaker_config.name,
        device_type=sim_shaker_config.type,
        driver=driver,
    )

    assert registry.get_driver("test_shaker") is not None
    assert registry.get_device_type("test_shaker") == "shaker"
    assert registry.get_device_name("test_shaker") == "test_shaker"


@pytest.mark.asyncio
async def test_get_all_devices(device_registry: DeviceRegistry):
    """Test getting all registered devices."""
    devices = device_registry.get_all_devices()

    assert len(devices) == 2
    assert "test_shaker" in devices
    assert "test_centrifuge" in devices
    assert devices["test_shaker"]["type"] == "shaker"


@pytest.mark.asyncio
async def test_initialize_all_success(device_factory: DeviceFactory, sim_shaker_config: DeviceConfig):
    """Test successful initialization of all devices."""
    registry = DeviceRegistry()
    driver = device_factory.create_driver(sim_shaker_config)
    assert driver is not None

    registry.register(
        name=sim_shaker_config.name,
        device_type=sim_shaker_config.type,
        driver=driver,
    )

    # Should not raise
    await registry.initialize_all()

    # Driver should be initialized
    assert driver.is_initialized


@pytest.mark.asyncio
async def test_cleanup_all(device_registry: DeviceRegistry):
    """Test cleanup of all devices."""
    # Should not raise
    await device_registry.cleanup_all()


@pytest.mark.asyncio
async def test_cleanup_all_invokes_shutdown_disposal_hook(
    device_factory: DeviceFactory,
    sim_shaker_config: DeviceConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cleanup_all must call each driver's ``_shutdown`` disposal hook, not only
    ``close``. A device-owned sim server (Opentrons robot-server) stops in
    ``_shutdown``; reverting cleanup to close()-only would leak it."""
    registry = DeviceRegistry()
    driver = device_factory.create_driver(sim_shaker_config)
    assert driver is not None
    registry.register(
        name=sim_shaker_config.name,
        device_type=sim_shaker_config.type,
        driver=driver,
    )

    calls: list[str] = []

    async def spy() -> None:
        calls.append("shutdown")

    monkeypatch.setattr(driver, "_shutdown", spy)

    await registry.cleanup_all()
    assert calls == ["shutdown"]


@pytest.mark.asyncio
async def test_register_storage_pairs_sim_driver(device_factory: DeviceFactory):
    """Storage devices must be paired with a SimStorageDriver for
    DEVICE_SIM dispatch. Prior to the canonical-table fix, registry
    skipped storage and waste so commands at effective_mode=DEVICE_SIM
    failed at the executor."""
    config = DeviceConfig(
        type="storage",
        name="test_storage",
        driver=DriverConfig(type="sim"),
    )
    registry = DeviceRegistry()
    registry.register(
        name=config.name,
        device_type=config.type,
        driver=device_factory.create_driver(config),
    )

    sim_driver = registry.get_sim_driver("test_storage")
    assert sim_driver is not None
    assert isinstance(sim_driver, SimStorageDriver)


@pytest.mark.asyncio
async def test_register_waste_pairs_sim_driver(device_factory: DeviceFactory):
    """Waste devices must be paired with a SimWasteDriver for DEVICE_SIM
    dispatch. Sibling regression to storage above."""
    config = DeviceConfig(
        type="waste",
        name="test_waste",
        driver=DriverConfig(type="sim"),
    )
    registry = DeviceRegistry()
    registry.register(
        name=config.name,
        device_type=config.type,
        driver=device_factory.create_driver(config),
    )

    sim_driver = registry.get_sim_driver("test_waste")
    assert sim_driver is not None
    assert isinstance(sim_driver, SimWasteDriver)


@pytest.mark.asyncio
async def test_status_report_follows_the_live_driver_through_bring_up(
    device_factory: DeviceFactory, sim_shaker_config: DeviceConfig,
):
    """The agent reports its own driver, so the server never has to guess.

    Bring-up is the only lifecycle verb orca dispatches, and on a backend whose
    link has no separate existence it opens the link too. A report that stayed
    closed through it renders a working bench offline.
    """
    registry = DeviceRegistry()
    driver = device_factory.create_driver(sim_shaker_config)
    assert isinstance(driver, SimShakerDriver)
    registry.register(
        name=sim_shaker_config.name,
        device_type=sim_shaker_config.type,
        driver=driver,
    )

    fresh = registry.status_report()["test_shaker"].links["LIVE"]
    assert fresh.is_connected is False
    assert fresh.is_initialized is False

    await driver.initialize()

    up = registry.status_report()["test_shaker"].links["LIVE"]
    assert up.is_connected is True
    assert up.is_initialized is True


@pytest.mark.asyncio
async def test_status_report_does_not_let_the_sim_driver_answer_for_the_live_one(
    device_factory: DeviceFactory, sim_shaker_config: DeviceConfig,
):
    """A DEVICE_SIM run brings up the paired sim driver and leaves the live one
    untouched. Reporting whichever driver the agent picked would render a whole
    simulated bench offline while it answers every command."""
    registry = DeviceRegistry()
    registry.register(
        name=sim_shaker_config.name,
        device_type=sim_shaker_config.type,
        driver=device_factory.create_driver(sim_shaker_config),
    )
    sim_driver = registry.get_sim_driver("test_shaker")
    assert isinstance(sim_driver, SimShakerDriver), "register pairs one automatically"
    await sim_driver.initialize()

    links = registry.status_report()["test_shaker"].links
    assert links["DEVICE_SIM"].is_connected is True
    assert links["DEVICE_SIM"].is_initialized is True
    assert links["LIVE"].is_connected is False
    assert links["LIVE"].is_initialized is False


@pytest.mark.asyncio
async def test_status_report_offers_no_live_link_when_no_live_driver_was_built():
    """An Opentrons LH configured with a sim_server and no connection has no
    live driver: a LIVE command to it fails with DeviceNotFoundError. Reporting
    its simulator would show an instrument that is up and cannot be commanded."""
    registry = DeviceRegistry()
    registry.register(name="flex_1", device_type="liquid_handler", driver=None)
    sim_driver = registry.get_sim_driver("flex_1")
    assert isinstance(sim_driver, SimLiquidHandlerDriver)
    await sim_driver.initialize()

    links = registry.status_report()["flex_1"].links
    assert "LIVE" not in links
    assert links["DEVICE_SIM"].is_connected is True


@pytest.mark.asyncio
async def test_a_lab_sim_bench_reports_itself_connected_after_bring_up(
    device_factory: DeviceFactory,
):
    """A `lab_sim` device is brought up with `initialize` alone, the way a
    LIVE run brings one up.
    So this is the whole simulated bench: if its report reads closed here, every
    device on the bed that is meant to catch this wave renders offline while it
    executes commands.
    """
    config = DeviceConfig(
        type="shaker",
        name="lab_sim_shaker",
        driver=DriverConfig(type="lab_sim", scale_factor=0.0),
    )
    registry = DeviceRegistry()
    driver = device_factory.create_driver(config)
    assert isinstance(driver, SimShakerDriver), 'lab_sim builds the paced Sim* driver'
    registry.register(name=config.name, device_type=config.type, driver=driver)

    await driver.initialize()

    observed = registry.status_report()["lab_sim_shaker"].observed_link
    assert observed.mode == "LIVE"
    assert observed.is_connected is True


class _ArmHoldingItsOwnSession(SimTransporterDriver):
    """An arm whose link is a thing of its own, the way a PF400's is.

    `BaseDriver.is_connected` defaults to tracking bring-up, and asks exactly
    the drivers that hold a per-connection session to override it so the link
    is observable on its own. This is that override: opening the socket is one
    step and powering the arm up is a later one, so the two can be true
    independently and either can be lost without the other.
    """

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self._is_linked = False

    @property
    def is_connected(self) -> bool:
        return self._is_linked

    async def connect(self) -> None:
        self._is_linked = True

    async def disconnect(self) -> None:
        self._is_linked = False


@pytest.mark.asyncio
async def test_status_report_keeps_a_devices_link_apart_from_its_bring_up():
    """The report carries both flags because a device can hold either alone.

    Derive one from the other and a PF400 whose socket is open but which has
    not been powered up republishes as disconnected, which is the bench
    symptom this whole surface exists to delete. It fails the other way too:
    an arm that loses its link while it stays powered up would keep reading
    connected, and an operator would send it commands it cannot take.
    """
    registry = DeviceRegistry()
    arm = _ArmHoldingItsOwnSession("pf400_1")
    registry.register(name="pf400_1", device_type="transporter", driver=arm)

    await arm.connect()

    linked = registry.status_report()["pf400_1"].links["LIVE"]
    assert linked.is_connected is True
    assert linked.is_initialized is False

    await arm.initialize(InitializeRequest())
    await arm.disconnect()

    dropped = registry.status_report()["pf400_1"].links["LIVE"]
    assert dropped.is_connected is False
    assert dropped.is_initialized is True


class _ShakerWhoseLinkCannotBeRead(SimShakerDriver):
    """A driver whose link property raises: a serial port that went away."""

    @property
    def is_connected(self) -> bool:
        raise RuntimeError("serial port went away")


@pytest.mark.asyncio
async def test_a_driver_that_cannot_be_read_reports_its_link_closed_not_missing(
    device_registry: DeviceRegistry,
):
    """A missing mode tells the operator there is no driver and to go fix the
    topology. A driver that exists and threw needs the opposite advice, so it
    has to stay in the map. An unguarded read would also take out the whole
    frame, leaving every device unreported.
    """
    device_registry.register(
        name="broken_shaker",
        device_type="shaker",
        driver=_ShakerWhoseLinkCannotBeRead("broken_shaker"),
    )

    report = device_registry.status_report()

    assert report["broken_shaker"].links["LIVE"].is_connected is False
    assert "DEVICE_SIM" in report["broken_shaker"].links
    assert "LIVE" in report["test_shaker"].links


@pytest.mark.asyncio
async def test_status_report_marks_a_device_busy_while_its_lock_is_held(
    device_registry: DeviceRegistry,
):
    """Status has to reflect the lock, or a queued command looks free to send."""
    lock = device_registry.lock_for("test_shaker")
    assert lock is not None
    async with lock:
        report = device_registry.status_report()
    assert report["test_shaker"].status == "busy"
    assert device_registry.status_report()["test_shaker"].status == "ready"
