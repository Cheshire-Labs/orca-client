"""A device whose simulator cannot be built leaves no live driver holding its port."""

import pytest
from cheshire_drivers.device_setup import DeviceSetup, SimDriverSetup
from cheshire_drivers.faults import RaiseFault
from cheshire_drivers.gateway_protocol import AssignedDevice
from cheshire_drivers.interfaces import BaseDriver
from cheshire_drivers.sims import SimShakerDriver

from orca_client.devices import DeviceFactory


@pytest.mark.asyncio
async def test_a_simulator_that_fails_to_build_releases_the_live_driver_built_before_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = SimShakerDriver("shaker_1")
    released: list[BaseDriver] = []

    async def recording_shutdown() -> None:
        released.append(live)

    monkeypatch.setattr(live, "_shutdown", recording_shutdown)
    factory = DeviceFactory()
    monkeypatch.setattr(factory, "create_driver", lambda device: live)
    unbuildable = DeviceSetup(sim=SimDriverSetup(faults=[RaiseFault(method="not_a_method", error_type="RuntimeError")]))

    with pytest.raises(ValueError, match="not a faultable method"):
        await factory.create_drivers(AssignedDevice(kind="shaker", name="shaker_1", setup=unbuildable))

    assert released == [live]
