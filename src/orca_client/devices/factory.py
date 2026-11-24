"""Builds each assigned device's drivers on this PC, from the server's setup and this PC's machine config."""

from cheshire_drivers import BaseDriver, ITransporterDriver
from cheshire_drivers.command_executor import DeviceDrivers, release_driver
from cheshire_drivers.device_setup import MachineSetup
from cheshire_drivers.driver_lookup import DriverRequest, build_real_driver, build_sim_driver
from cheshire_drivers.gateway_protocol import AssignedDevice


class DeviceFactory:
    """Creates driver instances for assigned devices."""

    def __init__(self, machine: MachineSetup | None = None, enable_plr_visualizer: bool = False) -> None:
        self._machine = machine or MachineSetup()
        self._enable_plr_visualizer = enable_plr_visualizer

    def _request(self, device: AssignedDevice) -> DriverRequest:
        return DriverRequest.for_device(device, self._machine, self._enable_plr_visualizer)

    def create_driver(self, device: AssignedDevice) -> BaseDriver | ITransporterDriver | None:
        """The driver for a LIVE command; None for a device with no real driver, which can only run as sim."""
        if device.setup.real is None or device.setup.always_sim:
            return None
        return build_real_driver(self._request(device))

    def create_sim_driver(self, device: AssignedDevice) -> BaseDriver | ITransporterDriver:
        """The driver for a SIM command."""
        return build_sim_driver(self._request(device))

    async def create_drivers(self, device: AssignedDevice) -> DeviceDrivers:
        """Both drivers for one assigned device. A simulator that fails to build releases the live driver."""
        live = self.create_driver(device)
        try:
            sim = self.create_sim_driver(device)
        except BaseException:
            if live is not None:
                await release_driver(device.name, live)
            raise
        return DeviceDrivers(live=live, sim=sim)
