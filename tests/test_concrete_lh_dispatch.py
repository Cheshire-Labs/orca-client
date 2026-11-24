"""Concrete liquid-handler dispatch by backend name.

The driver lookup maps known LH backend names to concrete cheshire-drivers drivers, so a
device advertises its full motion surface instead of the bare wrapper's
ILiquidHandler-only surface. Unmapped names keep the bare ``PLRLiquidHandlerWrapper``,
non-LH devices never consult the map, and both driver slots (LIVE and SIM)
dispatch through the same entry.
"""

from typing import Literal

import pytest

from cheshire_drivers import PLRLiquidHandlerWrapper, PLRShakerBackendWrapper, driver_lookup
from cheshire_drivers.device_setup import (
    DeviceConnection,
    DeviceSetup,
    MachineSetup,
    OpentronsSimMachine,
    RealDriverSetup,
    SerialConnection,
    SimDriverSetup,
    TcpConnection,
    UsbConnection,
)
from cheshire_drivers.gateway_protocol import AssignedDevice
from cheshire_drivers.plr import (
    ChatterboxLiquidHandlerWithProtocolDriver,
    FlexLiquidHandlerDriver,
    OT2LiquidHandlerDriver,
    STARLiquidHandlerDriver,
)
from cheshire_drivers.plr.opentrons_sim_server import OpentronsSimServer
from pylabrobot.legacy.liquid_handling.backends import (
    OpentronsOT2ChatterboxBackend,
    VantageBackend,
)

from cheshire_drivers.command_executor import DeviceRegistry
from orca_client.devices import DeviceFactory

_FLEX_SURFACE = frozenset(
    {
        "ILiquidHandler",
        "ILiquidProbe",
        "IPipetteMotion",
        "IGripperMotion",
        "IForceGripperJaw",
        "IGantryParking",
        "IHomeable",
    }
)

# Two real backends outside the concrete map: the Vantage, built with no arguments, and
# the OT-2 chatterbox, which takes a host/port.
_UNMAPPED_LH_BACKEND = VantageBackend.__name__
_UNMAPPED_ENDPOINT_LH_BACKEND = OpentronsOT2ChatterboxBackend.__name__

_VENDOR_SIM_MACHINE = MachineSetup(
    opentrons_sim=OpentronsSimMachine(interpreter="py", cwd="cwd", config_folder="configs")
)


@pytest.fixture
def vendor_sim_factory() -> DeviceFactory:
    return DeviceFactory(machine=_VENDOR_SIM_MACHINE)


def _vendor_sim_port_is(monkeypatch: pytest.MonkeyPatch, port: int) -> None:
    monkeypatch.setattr(driver_lookup, "_free_tcp_port", lambda: port)


def _live_lh(backend: str, connection: DeviceConnection | None) -> AssignedDevice:
    return AssignedDevice(
        kind="liquid_handler",
        name="lh_1",
        setup=DeviceSetup(
            client="lab_pc",
            real=RealDriverSetup(type="plr", backend=backend, connection=connection),
        ),
    )


def _sim_lh(backend: str) -> AssignedDevice:
    return AssignedDevice(
        kind="liquid_handler",
        name="lh_1",
        setup=DeviceSetup(
            client="lab_pc",
            real=RealDriverSetup(type="plr", backend=backend),
            sim=SimDriverSetup(use_vendor_sim=True, simulator_config="cfg.json"),
        ),
    )


def test_mapped_opentrons_backend_builds_concrete_live_driver(
    device_factory: DeviceFactory,
) -> None:
    """A mapped Opentrons backend name builds its concrete driver at the connection's host/port."""
    driver = device_factory.create_driver(
        _live_lh("OpentronsOT2Backend", TcpConnection(type="tcp", host="10.0.0.5", port=41000))
    )
    assert type(driver) is OT2LiquidHandlerDriver
    assert (driver._ot_backend.host, driver._ot_backend.port) == ("10.0.0.5", 41000)


def test_missing_tcp_port_defaults_to_opentrons_default(
    device_factory: DeviceFactory,
) -> None:
    """A tcp connection without a port points the driver at the Opentrons default 31950."""
    driver = device_factory.create_driver(
        _live_lh("OpentronsFlex", TcpConnection(type="tcp", host="10.0.0.5"))
    )
    assert isinstance(driver, FlexLiquidHandlerDriver)
    assert driver._port == 31950


@pytest.mark.parametrize(
    "connection",
    [UsbConnection(type="usb"), SerialConnection(type="serial", port="COM7")],
)
def test_a_non_tcp_connection_on_an_opentrons_is_rejected(
    device_factory: DeviceFactory, connection: DeviceConnection
) -> None:
    """A device switched to usb/serial fails at build time, rather than dialing anything over tcp."""
    with pytest.raises(ValueError, match="needs a tcp connection"):
        device_factory.create_driver(_live_lh("OpentronsFlex", connection))


@pytest.mark.parametrize("backend", ["STAR", "STARBackend"])
def test_star_backend_builds_concrete_star_driver(
    device_factory: DeviceFactory, backend: str
) -> None:
    """Either Hamilton backend name builds the STAR driver (USB ctor, no host/port)."""
    driver = device_factory.create_driver(_live_lh(backend, None))
    assert type(driver) is STARLiquidHandlerDriver


def test_star_selectors_name_the_instrument_on_the_usb_bus(
    device_factory: DeviceFactory,
) -> None:
    """Two STARs on one bus are told apart by serial_number / device_address, not by a host."""
    device = _live_lh("STAR", UsbConnection(type="usb", serial_number="SN123", device_address=7))
    driver = device_factory.create_driver(device)
    assert isinstance(driver, STARLiquidHandlerDriver)
    assert driver._star_backend.io._serial_number == "SN123"
    assert driver._star_backend.io._device_address == 7


def test_opentrons_flex_builds_flex_driver(device_factory: DeviceFactory) -> None:
    """'OpentronsFlex' (the pylabrobot.opentrons device name) builds FlexLiquidHandlerDriver."""
    driver = device_factory.create_driver(
        _live_lh("OpentronsFlex", TcpConnection(type="tcp", host="10.0.0.5", port=41000))
    )
    assert type(driver) is FlexLiquidHandlerDriver
    assert (driver._host, driver._port) == ("10.0.0.5", 41000)


@pytest.mark.parametrize("backend", ["OpentronsOT2Backend", "STAR", "OpentronsFlex"])
def test_the_visualizer_flag_reaches_every_live_driver(backend: str) -> None:
    """Every liquid handler the factory builds opens the Visualizer when the client asks for it."""
    factory = DeviceFactory(enable_plr_visualizer=True)
    connection = None if backend == "STAR" else TcpConnection(type="tcp", host="10.0.0.5")
    driver = factory.create_driver(_live_lh(backend, connection))
    assert isinstance(
        driver,
        (OT2LiquidHandlerDriver, STARLiquidHandlerDriver, FlexLiquidHandlerDriver),
    )
    assert driver._visualize is True


@pytest.mark.parametrize("backend", ["OpentronsOT2Backend", "OpentronsFlex"])
def test_the_visualizer_flag_reaches_sim_drivers_too(backend: str) -> None:
    """The SIM driver opens the Visualizer too; the flag used to be dropped on this path."""
    factory = DeviceFactory(machine=_VENDOR_SIM_MACHINE, enable_plr_visualizer=True)

    driver = factory.create_sim_driver(_sim_lh(backend))

    assert isinstance(driver, (OT2LiquidHandlerDriver, FlexLiquidHandlerDriver))
    assert driver._visualize is True


def _deck_modeling_lh(channels: Literal[1, 8, 96]) -> AssignedDevice:
    return AssignedDevice(
        kind="liquid_handler",
        name="flex_1",
        deck_modeling=True,
        setup=DeviceSetup(sim=SimDriverSetup(channels=channels)),
    )


def test_a_sim_liquid_handler_that_models_its_deck_builds_the_chatterbox_with_its_channels(
    device_factory: DeviceFactory,
) -> None:
    """The chatterbox is the deck-modelling driver, so a sim handler that models its deck is built as one."""
    driver = device_factory.create_sim_driver(_deck_modeling_lh(8))
    assert type(driver) is ChatterboxLiquidHandlerWithProtocolDriver
    assert driver._backend.num_channels == 8


def test_a_device_that_models_its_deck_sims_on_the_chatterbox(
    device_factory: DeviceFactory,
) -> None:
    """SIM on a deck-modelling device is a chatterbox, not the print-only Sim* driver."""
    driver = device_factory.create_sim_driver(_deck_modeling_lh(1))
    assert type(driver) is ChatterboxLiquidHandlerWithProtocolDriver
    assert driver._backend.num_channels == 1


def test_unmapped_backend_keeps_bare_wrapper(device_factory: DeviceFactory) -> None:
    """An unmapped LH backend resolves through the real PLR lookup into the bare wrapper."""
    driver = device_factory.create_driver(_live_lh(_UNMAPPED_LH_BACKEND, None))
    assert type(driver) is PLRLiquidHandlerWrapper
    assert type(driver._backend).__name__ == _UNMAPPED_LH_BACKEND


def test_non_lh_device_never_consults_the_map(device_factory: DeviceFactory) -> None:
    """A non-LH device with a mapped backend name keeps its own wrapper type."""
    device = AssignedDevice(
        kind="shaker",
        name="shaker_1",
        setup=DeviceSetup(
            client="lab_pc",
            real=RealDriverSetup(
                type="plr",
                backend="OpentronsOT2Backend",
                connection=TcpConnection(type="tcp", host="h", port=1),
            ),
        ),
    )
    driver = device_factory.create_driver(device)
    assert type(driver) is PLRShakerBackendWrapper


def test_sim_builds_concrete_driver_with_sim_server(
    vendor_sim_factory: DeviceFactory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mapped backend's SIM driver is the concrete class, owning its sim server."""
    _vendor_sim_port_is(monkeypatch, 41001)
    driver = vendor_sim_factory.create_sim_driver(_sim_lh("OpentronsOT2Backend"))
    assert type(driver) is OT2LiquidHandlerDriver
    assert (driver._ot_backend.host, driver._ot_backend.port) == ("127.0.0.1", 41001)
    server = driver._sim_server
    assert isinstance(server, OpentronsSimServer)
    assert server._port == 41001


def test_sim_opentrons_flex_threads_sim_server(
    vendor_sim_factory: DeviceFactory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 'OpentronsFlex' SIM driver points at the sim server the factory built."""
    _vendor_sim_port_is(monkeypatch, 41002)
    driver = vendor_sim_factory.create_sim_driver(_sim_lh("OpentronsFlex"))
    assert type(driver) is FlexLiquidHandlerDriver
    assert (driver._host, driver._port) == ("127.0.0.1", 41002)
    server = driver._sim_server
    assert isinstance(server, OpentronsSimServer)
    assert server._port == 41002


@pytest.mark.parametrize("backend", ["STAR", "STARBackend"])
def test_sim_on_a_backend_with_no_endpoint_is_rejected(
    vendor_sim_factory: DeviceFactory, backend: str
) -> None:
    """A STAR cannot be pointed at an Opentrons robot-server, so a vendor sim is refused."""
    with pytest.raises(ValueError, match="has no vendor simulator"):
        vendor_sim_factory.create_sim_driver(_sim_lh(backend))


def test_sim_on_an_unmapped_backend_with_no_endpoint_is_rejected(
    vendor_sim_factory: DeviceFactory,
) -> None:
    """An unmapped backend whose constructor takes no host/port is refused by name, not by a bare TypeError."""
    with pytest.raises(ValueError, match="has no vendor simulator"):
        vendor_sim_factory.create_sim_driver(_sim_lh(_UNMAPPED_LH_BACKEND))


def test_mapped_flex_advertises_motion_surface_at_handshake(
    vendor_sim_factory: DeviceFactory,
) -> None:
    """The registry reads the mapped class's interfaces, so the motion facets reach the gateway."""
    driver = vendor_sim_factory.create_sim_driver(_sim_lh("OpentronsFlex"))
    assert type(driver) is FlexLiquidHandlerDriver
    assert type(driver).interfaces == _FLEX_SURFACE
    registry = DeviceRegistry()
    registry.register(name="flex_1", device_type="liquid_handler", sim_driver=driver)
    (info,) = registry.connect_info_list()
    assert info.interfaces == _FLEX_SURFACE
