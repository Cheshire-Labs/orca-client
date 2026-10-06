"""Tests for Venus driver wiring in orca-client.

Previously the factory raised ``NotImplementedError`` whenever a
topology declared a Venus liquid handler, so any deployment that named
Venus crashed at first device instantiation. The factory now creates a
real ``VenusProtocolDriver`` from cheshire-drivers; per-event protocol
filepaths come from a ``VenusConfig`` block on ``DriverConfig.venus``.
"""

import pytest
from pydantic import JsonValue

from cheshire_drivers import SimulationVenusProtocolDriver, VenusProtocolDriver
from cheshire_drivers.gateway_protocol import CommandMessage
from orca_client.config.models import (
    DeviceConfig,
    DriverConfig,
    OpentronsSimServerConfig,
    VenusConfig,
)
from orca_client.devices import DeviceFactory, DeviceRegistry
from orca_client.executor import CommandExecutor


class TestVenusFactoryWiring:
    def test_creates_venus_protocol_driver(self) -> None:
        """A liquid_handler device with driver type 'venus' produces a real
        VenusProtocolDriver, not a NotImplementedError."""
        config = DeviceConfig(
            type="liquid_handler",
            name="venus-01",
            driver=DriverConfig(type="venus"),
        )
        factory = DeviceFactory()
        driver = factory._create_venus_driver(config)
        assert isinstance(driver, VenusProtocolDriver)
        assert driver.name == "venus-01"

    def test_passes_protocol_paths_through(self) -> None:
        """Per-event protocol filepaths from VenusConfig reach the driver."""
        config = DeviceConfig(
            type="liquid_handler",
            name="venus-01",
            driver=DriverConfig(
                type="venus",
                venus=VenusConfig(
                    init_protocol="C:/methods/init.hsl",
                    picked_protocol="C:/methods/picked.hsl",
                    placed_protocol="C:/methods/placed.hsl",
                    prepare_pick_protocol="C:/methods/prepare_pick.hsl",
                    prepare_place_protocol="C:/methods/prepare_place.hsl",
                    open_protocol="C:/methods/open.hsl",
                    close_protocol="C:/methods/close.hsl",
                    methods_folder="C:/Methods",
                    exe_path="C:/Hamilton/HxRun.exe",
                ),
            ),
        )
        factory = DeviceFactory()
        driver = factory._create_venus_driver(config)
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
        """Operators who don't specify a VenusConfig get the Hamilton install
        defaults; per-event protocols stay None so events are no-ops."""
        config = DeviceConfig(
            type="liquid_handler",
            name="venus-02",
            driver=DriverConfig(type="venus"),
        )
        factory = DeviceFactory()
        driver = factory._create_venus_driver(config)
        assert driver._exe_path == r"C:\Program Files (x86)\HAMILTON\Bin\HxRun.exe"
        assert driver._methods_folder == r"C:\Program Files (x86)\HAMILTON\Methods"
        assert driver._init_protocol is None
        assert driver._picked_protocol is None
        assert driver._open_protocol is None

    def test_rejects_non_liquid_handler_type(self) -> None:
        """Venus is a liquid-handler protocol runner; declaring a Venus
        driver under any other device type is a config error and surfaces
        at factory time, not at first command."""
        config = DeviceConfig(
            type="shaker",
            name="bad",
            driver=DriverConfig(type="venus"),
        )
        factory = DeviceFactory()
        with pytest.raises(ValueError, match="liquid_handler"):
            factory._create_venus_driver(config)

    def test_factory_dispatch_routes_venus_through(self) -> None:
        """``DeviceFactory.create_driver`` dispatches type=='venus' to the
        real Venus path; the previous NotImplementedError is gone."""
        config = DeviceConfig(
            type="liquid_handler",
            name="venus-03",
            driver=DriverConfig(type="venus"),
        )
        factory = DeviceFactory()
        driver = factory.create_driver(config)
        assert isinstance(driver, VenusProtocolDriver)


def _venus_config() -> DeviceConfig:
    return DeviceConfig(
        type="liquid_handler",
        name="ml_star",
        driver=DriverConfig(
            type="venus",
            venus=VenusConfig(placed_protocol="Placed.hsl", picked_protocol="Picked.hsl"),
        ),
    )


async def _registry_for(config: DeviceConfig) -> DeviceRegistry:
    factory = DeviceFactory()
    registry = DeviceRegistry()
    registry.register(
        name=config.name,
        device_type=config.type,
        driver=factory.create_driver(config),
        sim_driver=factory.create_sim_driver(config),
    )
    return registry


class TestVenusInDeviceSim:
    """DEVICE_SIM runs the Venus commands against Orca's Venus simulator, never HxRun."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("command, params", [
        ("run_protocol", {"protocol_filepath": "AddBuffer.hsl", "params": {"vol": 50}}),
        ("notify_placed", {"labware_name": "plate_1", "labware_type": "Cos_96_Rd", "site": "sample_site"}),
        ("notify_picked", {"labware_name": "plate_1", "labware_type": "Cos_96_Rd", "site": "sample_site"}),
    ])
    async def test_a_venus_command_in_device_sim_runs_on_the_venus_simulator(
        self, command: str, params: dict[str, JsonValue],
    ) -> None:
        registry = await _registry_for(_venus_config())
        assert isinstance(registry.get_sim_driver("ml_star"), SimulationVenusProtocolDriver)

        response = await CommandExecutor(registry).execute(CommandMessage(
            command_id="c1", device_name="ml_star", command=command,
            params=params, effective_mode="DEVICE_SIM",
        ))

        assert response.success, response.error

    def test_a_venus_declaring_a_sim_server_is_refused(self) -> None:
        """sim_server boots an Opentrons robot-server; a Venus has no use for one."""
        config = _venus_config()
        config.driver.sim_server = OpentronsSimServerConfig(interpreter="python", cwd=".", simulator_config="sim.json")

        with pytest.raises(ValueError, match="sim_server is only valid"):
            DeviceFactory().create_sim_driver(config)
