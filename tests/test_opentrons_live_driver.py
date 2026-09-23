"""Live-mode driver readiness for an Opentrons liquid handler.

A device configured for device-sim (``sim_server``) has TWO independent driver
slots in the registry: a live-mode slot (dispatched for effective_mode=LIVE) and
a device-sim-mode slot (dispatched for effective_mode=DEVICE_SIM). These tests
pin the live slot's construction:

  * sim_server but NO real-robot connection -> no live driver (LIVE-mode commands
    fail by design; the physical robot is the operator's responsibility),
  * sim_server + a real-robot connection -> the live driver is built pointed at
    that robot, so LIVE is ready the moment a robot is attached (and merely fails
    to connect until then).
"""

from cheshire_drivers.plr import FlexLiquidHandlerDriver

from orca_client.config.models import (
    ConnectionConfig,
    DeviceConfig,
    DriverConfig,
    OpentronsSimServerConfig,
)
from orca_client.devices import DeviceFactory


def _sim_server() -> OpentronsSimServerConfig:
    return OpentronsSimServerConfig(interpreter="py", cwd="cwd", simulator_config="cfg.json")


def test_no_connection_builds_no_live_driver(device_factory: DeviceFactory) -> None:
    """sim_server + no connection -> live-mode slot is empty; LIVE fails by design."""
    config = DeviceConfig(
        type="liquid_handler",
        name="flex_1",
        driver=DriverConfig(
            type="plr", backend="OpentronsFlex", sim_server=_sim_server(),
        ),
    )
    assert device_factory.create_driver(config) is None


def test_connection_builds_live_driver_pointed_at_robot(
    device_factory: DeviceFactory,
) -> None:
    """sim_server + a real-robot connection -> the live driver is built from the
    connection, so LIVE is ready when a robot is attached."""
    config = DeviceConfig(
        type="liquid_handler",
        name="flex_1",
        driver=DriverConfig(
            type="plr",
            backend="OpentronsFlex",
            connection=ConnectionConfig(type="tcp", host="10.0.0.5", tcp_port=31950),
            sim_server=_sim_server(),
        ),
    )
    driver = device_factory.create_driver(config)
    assert isinstance(driver, FlexLiquidHandlerDriver)
    assert (driver._host, driver._port) == ("10.0.0.5", 31950)
