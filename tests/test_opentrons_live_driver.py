"""Live-mode driver readiness for an Opentrons liquid handler.

A device set up with the vendor simulator has TWO independent driver slots in
the registry: a live-mode slot (dispatched for effective_mode=LIVE) and a
sim-mode slot (dispatched for effective_mode=SIM). These tests pin
the live slot's construction:

  * vendor sim but NO real-robot connection -> no live driver (LIVE-mode commands
    fail by design; the physical robot is the operator's responsibility),
  * vendor sim + a real-robot connection -> the live driver is built pointed at
    that robot, so LIVE is ready the moment a robot is attached (and merely fails
    to connect until then).
"""

from cheshire_drivers.device_setup import DeviceConnection, DeviceSetup, RealDriverSetup, SimDriverSetup, TcpConnection
from cheshire_drivers.gateway_protocol import AssignedDevice
from cheshire_drivers.plr import FlexLiquidHandlerDriver

from orca_client.devices import DeviceFactory


def _vendor_simulated_flex(connection: DeviceConnection | None) -> AssignedDevice:
    return AssignedDevice(
        kind="liquid_handler",
        name="flex_1",
        setup=DeviceSetup(
            client="lab_pc",
            real=RealDriverSetup(type="plr", backend="OpentronsFlex", connection=connection),
            sim=SimDriverSetup(use_vendor_sim=True, simulator_config="cfg.json"),
        ),
    )


def test_no_connection_builds_no_live_driver(device_factory: DeviceFactory) -> None:
    """Vendor sim + no connection -> live-mode slot is empty; LIVE fails by design."""
    assert device_factory.create_driver(_vendor_simulated_flex(None)) is None


def test_connection_builds_live_driver_pointed_at_robot(
    device_factory: DeviceFactory,
) -> None:
    """Vendor sim + a real-robot connection -> the live driver is built from the
    connection, so LIVE is ready when a robot is attached."""
    driver = device_factory.create_driver(
        _vendor_simulated_flex(TcpConnection(type="tcp", host="10.0.0.5", port=31950))
    )
    assert isinstance(driver, FlexLiquidHandlerDriver)
    assert (driver._host, driver._port) == ("10.0.0.5", 31950)
