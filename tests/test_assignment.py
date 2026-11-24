"""ClientSession.apply_assignment runs exactly the devices the platform assigns."""

from cheshire_drivers.command_executor import DeviceRegistry
import asyncio
from cheshire_drivers import SimShakerDriver
from cheshire_drivers.device_setup import DeviceSetup, SimDriverSetup
from cheshire_drivers.faults import RaiseFault
from cheshire_drivers.gateway_protocol import AssignedDevice, DeviceAssignment

from orca_client.config.models import ClientConfig
from orca_client.session import ClientSession


def _shaker(name: str = "shaker_1", setup: DeviceSetup | None = None) -> AssignedDevice:
    return AssignedDevice(kind="shaker", name=name, setup=setup or DeviceSetup())


def _a_changed_setup() -> DeviceSetup:
    return DeviceSetup(sim=SimDriverSetup(faults=[RaiseFault(method="shake", error_type="RuntimeError")]))


def _record_release(driver: SimShakerDriver, released: list[SimShakerDriver]) -> None:
    original = driver._shutdown

    async def recording_shutdown() -> None:
        released.append(driver)
        await original()

    driver._shutdown = recording_shutdown


def _sim_shaker(session: ClientSession, name: str = "shaker_1") -> SimShakerDriver:
    driver = session.registry.get_sim_driver(name)
    assert isinstance(driver, SimShakerDriver)
    return driver


async def test_a_new_device_is_built_and_reported(client_config: ClientConfig) -> None:
    session = ClientSession(client_config)

    result = await session.apply_assignment(DeviceAssignment(assignment_id="a1", devices=[_shaker()]))

    assert session.registry.get_all_device_ids() == ["shaker_1"]
    assert session.registry.get_driver("shaker_1") is None, "a shaker with no real driver runs only as sim"
    assert session.registry.get_sim_driver("shaker_1") is not None
    assert [info.name for info in result.devices] == ["shaker_1"]
    assert result.errors == {}


async def test_an_unchanged_device_keeps_the_same_driver_across_a_second_assignment(
    client_config: ClientConfig,
) -> None:
    session = ClientSession(client_config)
    await session.apply_assignment(DeviceAssignment(assignment_id="a1", devices=[_shaker()]))
    first = _sim_shaker(session)
    released: list[SimShakerDriver] = []
    _record_release(first, released)

    result = await session.apply_assignment(DeviceAssignment(assignment_id="a1", devices=[_shaker()]))

    assert _sim_shaker(session) is first
    assert released == []
    assert [info.name for info in result.devices] == ["shaker_1"]


async def test_a_device_whose_setup_changed_gets_a_new_driver_and_the_old_one_is_released(
    client_config: ClientConfig,
) -> None:
    session = ClientSession(client_config)
    await session.apply_assignment(DeviceAssignment(assignment_id="a1", devices=[_shaker()]))
    first = _sim_shaker(session)
    released: list[SimShakerDriver] = []
    _record_release(first, released)

    await session.apply_assignment(DeviceAssignment(assignment_id="a1", devices=[_shaker(setup=_a_changed_setup())]))

    assert _sim_shaker(session) is not first
    assert released == [first]


async def test_a_device_dropped_from_the_assignment_is_released_and_forgotten(
    client_config: ClientConfig,
) -> None:
    session = ClientSession(client_config)
    await session.apply_assignment(DeviceAssignment(assignment_id="a1", devices=[_shaker(), _shaker("shaker_2")]))
    dropped = _sim_shaker(session, "shaker_2")
    released: list[SimShakerDriver] = []
    _record_release(dropped, released)

    result = await session.apply_assignment(DeviceAssignment(assignment_id="a1", devices=[_shaker()]))

    assert released == [dropped]
    assert session.registry.get_all_device_ids() == ["shaker_1"]
    assert session.registry.get_driver("shaker_2") is None
    assert session.registry.get_sim_driver("shaker_2") is None
    assert [info.name for info in result.devices] == ["shaker_1"]


async def test_a_device_that_fails_to_build_is_in_errors_by_name_while_the_others_still_build(
    client_config: ClientConfig,
) -> None:
    session = ClientSession(client_config)
    broken = _shaker("broken", DeviceSetup(sim=SimDriverSetup(faults=[RaiseFault(method="not_a_method", error_type="RuntimeError")])))

    result = await session.apply_assignment(DeviceAssignment(assignment_id="a1", devices=[broken, _shaker()]))

    assert set(result.errors) == {"broken"}
    assert "not a faultable method" in result.errors["broken"]
    assert session.registry.get_all_device_ids() == ["shaker_1"]
    assert [info.name for info in result.devices] == ["shaker_1"]


async def test_a_device_being_replaced_waits_for_its_running_command() -> None:
    registry = DeviceRegistry()
    registry.register(name="shaker_1", device_type="shaker", driver=SimShakerDriver("shaker_1"))
    lock = registry.lock_for("shaker_1")
    assert lock is not None

    await lock.acquire()
    removing = asyncio.create_task(registry.remove("shaker_1"))
    await asyncio.sleep(0)
    assert registry.get_driver("shaker_1") is not None

    lock.release()
    await removing
    assert registry.get_driver("shaker_1") is None
