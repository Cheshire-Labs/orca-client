"""Concrete liquid-handler dispatch by backend name.

The factory maps known LH backend names to concrete cheshire-drivers drivers, so a
device advertises its full motion surface at handshake instead of the bare wrapper's
ILiquidHandler-only surface. Unmapped names keep the bare ``PLRLiquidHandlerWrapper``,
non-LH devices never consult the map, and both driver slots (LIVE and DEVICE_SIM)
dispatch through the same entry.
"""

from typing import Literal

import pytest

from cheshire_drivers import PLRLiquidHandlerWrapper, PLRShakerBackendWrapper
from cheshire_drivers.plr import (
    FlexLiquidHandlerDriver,
    OT2LiquidHandlerDriver,
    STARLiquidHandlerDriver,
)
from cheshire_drivers.plr.opentrons_sim_server import OpentronsSimServer
from pylabrobot.legacy.liquid_handling.backends import (
    LiquidHandlerChatterboxBackend,
    OpentronsOT2ChatterboxBackend,
)

from orca_client.config.models import (
    ConnectionConfig,
    DeviceConfig,
    DriverConfig,
    OpentronsSimServerConfig,
)
from orca_client.devices import DeviceFactory, DeviceRegistry, factory as factory_module

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

# Two real backends outside the concrete map: the generic chatterbox, and
# the OT-2 chatterbox, which takes a host/port.
_UNMAPPED_LH_BACKEND = LiquidHandlerChatterboxBackend.__name__
_UNMAPPED_ENDPOINT_LH_BACKEND = OpentronsOT2ChatterboxBackend.__name__


def _live_lh(backend: str, connection: ConnectionConfig | None) -> DeviceConfig:
    return DeviceConfig(
        type="liquid_handler",
        name="lh_1",
        driver=DriverConfig(type="plr", backend=backend, connection=connection),
    )


def _sim_lh(backend: str, port: int) -> DeviceConfig:
    return DeviceConfig(
        type="liquid_handler",
        name="lh_1",
        driver=DriverConfig(
            type="plr",
            backend=backend,
            sim_server=OpentronsSimServerConfig(
                interpreter="py", cwd="cwd", simulator_config="cfg.json", port=port
            ),
        ),
    )


def test_mapped_opentrons_backend_builds_concrete_live_driver(
    device_factory: DeviceFactory,
) -> None:
    """A mapped Opentrons backend name builds its concrete driver at the connection's host/port."""
    driver = device_factory.create_driver(
        _live_lh(
            "OpentronsOT2Backend", ConnectionConfig(type="tcp", host="10.0.0.5", tcp_port=41000)
        )
    )
    assert type(driver) is OT2LiquidHandlerDriver
    assert (driver._ot_backend.host, driver._ot_backend.port) == ("10.0.0.5", 41000)


def test_missing_tcp_port_defaults_to_opentrons_default(
    device_factory: DeviceFactory,
) -> None:
    """A tcp connection without a tcp_port points the driver at the Opentrons default 31950."""
    driver = device_factory.create_driver(
        _live_lh("OpentronsFlex", ConnectionConfig(type="tcp", host="10.0.0.5"))
    )
    assert isinstance(driver, FlexLiquidHandlerDriver)
    assert driver._port == 31950


@pytest.mark.parametrize("connection_type", ["usb", "serial"])
def test_non_tcp_connection_carrying_a_host_is_rejected(
    device_factory: DeviceFactory, connection_type: Literal["usb", "serial"]
) -> None:
    """A device switched to usb/serial with a stale host fails at build time, rather than dialing it over tcp."""
    with pytest.raises(ValueError, match="requires a tcp connection"):
        device_factory.create_driver(
            _live_lh(
                "OpentronsFlex",
                ConnectionConfig(type=connection_type, host="10.0.0.5", port="COM7"),
            )
        )


@pytest.mark.parametrize("backend", ["STAR", "STARBackend"])
def test_star_backend_builds_concrete_star_driver(
    device_factory: DeviceFactory, backend: str
) -> None:
    """Either Hamilton backend name builds the STAR driver (USB ctor, no host/port)."""
    driver = device_factory.create_driver(_live_lh(backend, None))
    assert type(driver) is STARLiquidHandlerDriver


@pytest.mark.parametrize(
    "connection",
    [
        ConnectionConfig(type="usb", port="/dev/star0"),
        ConnectionConfig(type="serial", port="COM7"),
        ConnectionConfig(type="tcp", host="10.0.0.5", tcp_port=41000),
    ],
)
def test_star_with_a_connection_block_is_rejected(
    device_factory: DeviceFactory, connection: ConnectionConfig
) -> None:
    """A STAR takes no connection block, so one that was written is refused, not dropped."""
    with pytest.raises(ValueError, match="takes no connection block"):
        device_factory.create_driver(_live_lh("STAR", connection))


def test_star_selectors_name_the_instrument_on_the_usb_bus(
    device_factory: DeviceFactory,
) -> None:
    """Two STARs on one bus are told apart by serial_number / device_address, not by a host."""
    config = DeviceConfig(
        type="liquid_handler",
        name="star_1",
        driver=DriverConfig(
            type="plr", backend="STAR", serial_number="SN123", device_address=7
        ),
    )
    driver = device_factory.create_driver(config)
    assert isinstance(driver, STARLiquidHandlerDriver)
    assert driver._star_backend.io._serial_number == "SN123"
    assert driver._star_backend.io._device_address == 7


def test_opentrons_flex_builds_flex_driver(device_factory: DeviceFactory) -> None:
    """'OpentronsFlex' (the pylabrobot.opentrons device name) builds FlexLiquidHandlerDriver."""
    driver = device_factory.create_driver(
        _live_lh("OpentronsFlex", ConnectionConfig(type="tcp", host="10.0.0.5", tcp_port=41000))
    )
    assert type(driver) is FlexLiquidHandlerDriver
    assert (driver._host, driver._port) == ("10.0.0.5", 41000)


@pytest.mark.parametrize("backend", ["OpentronsOT2Backend", "STAR", "OpentronsFlex"])
def test_the_visualizer_flag_reaches_every_live_driver(backend: str) -> None:
    """Every liquid handler the factory builds opens the Visualizer when the client asks for it."""
    factory = DeviceFactory(enable_plr_visualizer=True)
    connection = (
        None if backend == "STAR" else ConnectionConfig(type="tcp", host="10.0.0.5")
    )
    driver = factory.create_driver(_live_lh(backend, connection))
    assert isinstance(
        driver,
        (OT2LiquidHandlerDriver, STARLiquidHandlerDriver, FlexLiquidHandlerDriver),
    )
    assert driver._visualize is True


@pytest.mark.parametrize("backend", ["OpentronsOT2Backend", "OpentronsFlex"])
def test_the_visualizer_flag_reaches_device_sim_drivers_too(backend: str) -> None:
    """The DEVICE_SIM driver opens the Visualizer too; the flag used to be dropped on this path."""
    factory = DeviceFactory(enable_plr_visualizer=True)

    driver = factory.create_sim_driver(_sim_lh(backend, port=41005))

    assert isinstance(driver, (OT2LiquidHandlerDriver, FlexLiquidHandlerDriver))
    assert driver._visualize is True


def test_unmapped_backend_keeps_bare_wrapper(device_factory: DeviceFactory) -> None:
    """An unmapped LH backend resolves through the real PLR lookup into the bare wrapper."""
    driver = device_factory.create_driver(_live_lh(_UNMAPPED_LH_BACKEND, None))
    assert type(driver) is PLRLiquidHandlerWrapper
    assert type(driver._backend).__name__ == _UNMAPPED_LH_BACKEND


def test_non_lh_device_never_consults_the_map(device_factory: DeviceFactory) -> None:
    """A non-LH device with a mapped backend name keeps its own wrapper type."""
    config = DeviceConfig(
        type="shaker",
        name="shaker_1",
        driver=DriverConfig(
            type="plr",
            backend="OpentronsOT2Backend",
            connection=ConnectionConfig(type="tcp", host="h", tcp_port=1),
        ),
    )
    driver = device_factory.create_driver(config)
    assert type(driver) is PLRShakerBackendWrapper


def test_device_sim_builds_concrete_driver_with_sim_server(
    device_factory: DeviceFactory,
) -> None:
    """A mapped backend's DEVICE_SIM driver is the concrete class, owning its sim server."""
    driver = device_factory.create_sim_driver(_sim_lh("OpentronsOT2Backend", port=41001))
    assert type(driver) is OT2LiquidHandlerDriver
    assert (driver._ot_backend.host, driver._ot_backend.port) == ("127.0.0.1", 41001)
    server = driver._sim_server
    assert isinstance(server, OpentronsSimServer)
    assert server._port == 41001


def test_device_sim_opentrons_flex_threads_sim_server(
    device_factory: DeviceFactory,
) -> None:
    """The 'OpentronsFlex' DEVICE_SIM driver points at the sim server the factory built."""
    driver = device_factory.create_sim_driver(_sim_lh("OpentronsFlex", port=41002))
    assert type(driver) is FlexLiquidHandlerDriver
    assert (driver._host, driver._port) == ("127.0.0.1", 41002)
    server = driver._sim_server
    assert isinstance(server, OpentronsSimServer)
    assert server._port == 41002


def test_device_sim_unmapped_backend_keeps_bare_wrapper(
    device_factory: DeviceFactory,
) -> None:
    """An unmapped backend's DEVICE_SIM driver is the bare wrapper, aimed at the sim server's port."""
    driver = device_factory.create_sim_driver(
        _sim_lh(_UNMAPPED_ENDPOINT_LH_BACKEND, port=41003)
    )
    assert type(driver) is PLRLiquidHandlerWrapper
    backend = driver._backend
    assert isinstance(backend, OpentronsOT2ChatterboxBackend)
    assert (backend.host, backend.port) == ("127.0.0.1", 41003)
    assert isinstance(driver._sim_server, OpentronsSimServer)


@pytest.mark.parametrize("backend", ["STAR", "STARBackend"])
def test_device_sim_on_a_backend_with_no_endpoint_is_rejected(
    device_factory: DeviceFactory, backend: str
) -> None:
    """A STAR cannot be pointed at an Opentrons robot-server, so sim_server is refused."""
    with pytest.raises(ValueError, match="has no sim-server form"):
        device_factory.create_sim_driver(_sim_lh(backend, port=41005))


def test_device_sim_on_an_unmapped_backend_with_no_endpoint_is_rejected(
    device_factory: DeviceFactory,
) -> None:
    """An unmapped backend whose constructor takes no host/port is refused by name, not by a bare TypeError."""
    with pytest.raises(ValueError, match="has no sim-server form"):
        device_factory.create_sim_driver(_sim_lh(_UNMAPPED_LH_BACKEND, port=41006))


def test_mapped_flex_advertises_motion_surface_at_handshake(
    device_factory: DeviceFactory,
) -> None:
    """The registry handshake reads the mapped class's interfaces, so the motion facets reach the gateway."""
    driver = device_factory.create_sim_driver(_sim_lh("OpentronsFlex", port=41004))
    assert type(driver) is FlexLiquidHandlerDriver
    assert type(driver).interfaces == _FLEX_SURFACE
    registry = DeviceRegistry()
    registry.register(name="flex_1", device_type="liquid_handler", sim_driver=driver)
    (info,) = registry.connect_info_list()
    assert info.interfaces == _FLEX_SURFACE


@pytest.mark.parametrize("backend", ["OpentronsFlex", "OpentronsOT2Backend"])
def test_instrument_selectors_on_a_non_hamilton_device_are_rejected(
    device_factory: DeviceFactory, backend: str
) -> None:
    """Only the Hamilton driver reads serial_number / device_address, so elsewhere they are refused, not dropped."""
    config = DeviceConfig(
        type="liquid_handler",
        name="lh_1",
        driver=DriverConfig(
            type="plr",
            backend=backend,
            connection=ConnectionConfig(type="tcp", host="10.0.0.5"),
            serial_number="SN123",
        ),
    )
    with pytest.raises(ValueError, match="does not read them"):
        device_factory.create_driver(config)


def test_instrument_selectors_are_rejected_on_the_device_sim_path_too(
    device_factory: DeviceFactory,
) -> None:
    """A selector no driver reads is refused whichever slot the device is being built for."""
    config = DeviceConfig(
        type="liquid_handler",
        name="lh_1",
        driver=DriverConfig(
            type="plr",
            backend="OpentronsFlex",
            device_address=7,
            sim_server=OpentronsSimServerConfig(
                interpreter="py", cwd="cwd", simulator_config="cfg.json", port=41007
            ),
        ),
    )
    with pytest.raises(ValueError, match="does not read them"):
        device_factory.create_sim_driver(config)


def test_a_constructor_that_blows_up_is_not_reported_as_a_missing_sim_server_form(
    device_factory: DeviceFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A backend that takes a host/port but fails inside surfaces its own error, not 'no sim-server form'."""

    class _ExplodingBackend:
        def __init__(self, host: str, port: int) -> None:
            raise TypeError("vendor bug deep inside the constructor")

    monkeypatch.setattr(
        factory_module, "get_plr_backend_class", lambda name: _ExplodingBackend
    )
    with pytest.raises(TypeError, match="vendor bug"):
        device_factory.create_sim_driver(_sim_lh("SomeVendorBackend", port=41008))
