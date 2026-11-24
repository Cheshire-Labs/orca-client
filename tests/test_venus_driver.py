"""Tests for Venus driver wiring in orca-client.

Previously the factory raised ``NotImplementedError`` whenever a
topology declared a Venus liquid handler, so any deployment that named
Venus crashed at first device instantiation. The factory now creates a
real ``VenusProtocolDriver`` from cheshire-drivers; per-event protocol
filepaths come from the device's ``VenusProtocols``, and the exe path and
methods folder from this PC's ``MachineSetup``.
"""

import pytest
from pydantic import JsonValue

from cheshire_drivers import SimulationVenusProtocolDriver, VenusProtocolDriver
from cheshire_drivers.device_setup import (
    DeviceSetup,
    MachineSetup,
    RealDriverSetup,
    SimDriverSetup,
    VenusProtocols,
)
from cheshire_drivers.driver_lookup import check_device_setup
from cheshire_drivers.gateway_protocol import AssignedDevice, CommandMessage
from cheshire_drivers.command_executor import DeviceRegistry
from orca_client.devices import DeviceFactory
from orca_client.executor import ClientCommandExecutor


def _venus_setup(protocols: VenusProtocols | None = None) -> DeviceSetup:
    return DeviceSetup(client="lab_pc", real=RealDriverSetup(type="venus", venus=protocols))


def _venus_device(name: str, protocols: VenusProtocols | None = None) -> AssignedDevice:
    return AssignedDevice(kind="liquid_handler", name=name, setup=_venus_setup(protocols))


class TestVenusFactoryWiring:
    def test_creates_venus_protocol_driver(self) -> None:
        """A liquid_handler device with driver type 'venus' produces a real
        VenusProtocolDriver, not a NotImplementedError."""
        driver = DeviceFactory().create_driver(_venus_device("venus-01"))
        assert isinstance(driver, VenusProtocolDriver)
        assert driver.name == "venus-01"

    def test_passes_protocol_paths_through(self) -> None:
        """Per-event protocols from the setup and the PC's Venus paths reach the driver."""
        device = _venus_device(
            "venus-01",
            VenusProtocols(
                init_protocol="C:/methods/init.hsl",
                picked_protocol="C:/methods/picked.hsl",
                placed_protocol="C:/methods/placed.hsl",
                prepare_pick_protocol="C:/methods/prepare_pick.hsl",
                prepare_place_protocol="C:/methods/prepare_place.hsl",
                open_protocol="C:/methods/open.hsl",
                close_protocol="C:/methods/close.hsl",
            ),
        )
        factory = DeviceFactory(
            machine=MachineSetup(venus_exe_path="C:/Hamilton/HxRun.exe", venus_methods_folder="C:/Methods")
        )
        driver = factory.create_driver(device)
        assert isinstance(driver, VenusProtocolDriver)
        assert driver._init_protocol == "C:/methods/init.hsl"
        assert driver._picked_protocol == "C:/methods/picked.hsl"
        assert driver._placed_protocol == "C:/methods/placed.hsl"
        assert driver._prepare_pick_protocol == "C:/methods/prepare_pick.hsl"
        assert driver._prepare_place_protocol == "C:/methods/prepare_place.hsl"
        assert driver._open_protocol == "C:/methods/open.hsl"
        assert driver._close_protocol == "C:/methods/close.hsl"
        assert driver._methods_folder == "C:/Methods"
        assert driver._exe_path == "C:/Hamilton/HxRun.exe"

    def test_defaults_when_venus_block_omitted(self) -> None:
        """Operators who don't specify Venus protocols or paths get the Hamilton
        install defaults; per-event protocols stay None so events are no-ops."""
        driver = DeviceFactory().create_driver(_venus_device("venus-02"))
        assert isinstance(driver, VenusProtocolDriver)
        assert driver._exe_path == r"C:\Program Files (x86)\HAMILTON\Bin\HxRun.exe"
        assert driver._methods_folder == r"C:\Program Files (x86)\HAMILTON\Methods"
        assert driver._init_protocol is None
        assert driver._picked_protocol is None
        assert driver._open_protocol is None

    def test_rejects_non_liquid_handler_type(self) -> None:
        """Venus is a liquid-handler protocol runner; declaring a Venus
        driver under any other device type is a setup error and surfaces
        when the setup loads, not at first command."""
        with pytest.raises(ValueError, match="liquid_handler"):
            check_device_setup("bad", "shaker", _venus_setup())


def _venus_config() -> AssignedDevice:
    return _venus_device("ml_star", VenusProtocols(placed_protocol="Placed.hsl", picked_protocol="Picked.hsl"))


async def _registry_for(config: AssignedDevice) -> DeviceRegistry:
    factory = DeviceFactory()
    registry = DeviceRegistry()
    registry.register(
        name=config.name,
        device_type=config.kind,
        driver=factory.create_driver(config),
        sim_driver=factory.create_sim_driver(config),
    )
    return registry


class TestVenusInSim:
    """SIM runs the Venus commands against Orca's Venus simulator, never HxRun."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("command, params", [
        ("run_protocol", {"protocol_filepath": "AddBuffer.hsl", "params": {"vol": 50}}),
        ("notify_placed", {"labware_name": "plate_1", "labware_type": "Cos_96_Rd", "site": "sample_site"}),
        ("notify_picked", {"labware_name": "plate_1", "labware_type": "Cos_96_Rd", "site": "sample_site"}),
    ])
    async def test_a_venus_command_in_sim_runs_on_the_venus_simulator(
        self, command: str, params: dict[str, JsonValue],
    ) -> None:
        registry = await _registry_for(_venus_config())
        assert isinstance(registry.get_sim_driver("ml_star"), SimulationVenusProtocolDriver)

        response = await ClientCommandExecutor(registry).execute(CommandMessage(
            command_id="c1", device_name="ml_star", command=command,
            params=params, effective_mode="SIM",
        ))

        assert response.success, response.error

    def test_a_venus_declaring_a_vendor_sim_is_refused(self) -> None:
        """The vendor sim boots an Opentrons robot-server; a Venus has no use for one."""
        config = AssignedDevice(
            kind="liquid_handler",
            name="ml_star",
            setup=DeviceSetup(
                client="lab_pc",
                real=RealDriverSetup(type="venus"),
                sim=SimDriverSetup(use_vendor_sim=True, simulator_config="sim.json"),
            ),
        )

        with pytest.raises(ValueError, match="has no vendor simulator"):
            DeviceFactory().create_sim_driver(config)
