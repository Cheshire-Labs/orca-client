"""Device factory for creating driver instances from configuration.

Wire source-of-truth: orca-client never resolves position_ids locally.
The server resolves `position_id` through its `ITeachpointStore` and
dispatches `pick_at_coords` / `place_at_coords` / `move_to_coords` with
fully-flattened `Teachpoint` values inlined into the wire payload, so the
on-prem transporter driver receives wire-ready coordinates and holds no
teachpoint store of its own.
"""

import inspect
import logging
from dataclasses import dataclass
from typing import Any, Callable
from ..config.models import ArmConfig, DeviceConfig

from cheshire_drivers import (
    BaseDriver,
    ITransporterDriver,
    PLRShakerBackendWrapper,
    PLRCentrifugeBackendWrapper,
    PLRSealerBackendWrapper,
    PLRThermocyclerBackendWrapper,
    PLRTransporterBackendWrapper,
    PLRLiquidHandlerWrapper,
    VenusProtocolDriver,
)
from cheshire_drivers.interfaces import ILiquidHandlerDriver
from cheshire_drivers.plr import (
    FlexLiquidHandlerDriver,
    OT2LiquidHandlerDriver,
    PreciseFlexTransporterDriver,
    STARLiquidHandlerDriver,
    get_plr_backend_class,
)
from cheshire_drivers.plr.opentrons_sim_server import (
    OpentronsSimServer,
    OpentronsSimServerSpec,
)
from cheshire_drivers.sims import SleepSim

from .sim_driver_types import SIM_DRIVER_CLS_BY_TYPE

logger = logging.getLogger("orca_client.factory")


def _free_tcp_port() -> int:
    """An ephemeral loopback port for a per-device sim server."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


_PLR_WRAPPER_BY_TYPE: dict[str, Callable[[Any], BaseDriver]] = {
    "shaker": PLRShakerBackendWrapper,
    "centrifuge": PLRCentrifugeBackendWrapper,
    "sealer": PLRSealerBackendWrapper,
    "thermocycler": PLRThermocyclerBackendWrapper,
}


def _tcp_endpoint(config: DeviceConfig, default_port: int = 31950) -> tuple[str, int]:
    """The device's host/port from its tcp connection block."""
    connection = config.driver.connection
    if connection is None or connection.type != "tcp" or connection.host is None:
        raise ValueError(
            f"Backend {config.driver.backend!r} on device {config.name!r} "
            f"requires a tcp connection with a host"
        )
    return connection.host, connection.tcp_port if connection.tcp_port is not None else default_port


def _takes_host_and_port(backend_cls: type) -> bool:
    """Whether a backend is reached at a host/port, which is all a sim robot-server can serve."""
    try:
        parameters = inspect.signature(backend_cls).parameters
    except (TypeError, ValueError):
        return False
    return {"host", "port"} <= parameters.keys()


def _no_sim_server_form(config: DeviceConfig) -> str:
    return (
        f"sim_server boots an Opentrons robot-server; backend {config.driver.backend!r} "
        f"on device {config.name!r} has no sim-server form (it is not reached at a "
        f"host/port)"
    )


@dataclass(frozen=True)
class _ConcreteLHSpec:
    """How to build one backend name's driver in each mode.

    ``sim`` is None for a backend with no host/port form, which the Opentrons sim
    robot-server cannot serve.
    """

    live: Callable[[DeviceConfig, bool], ILiquidHandlerDriver]
    sim: Callable[[str, int, OpentronsSimServer, bool], ILiquidHandlerDriver] | None


def _star_live(config: DeviceConfig, visualize: bool) -> ILiquidHandlerDriver:
    """A STAR is found on the USB bus, so a connection block cannot be honored."""
    if config.driver.connection is not None:
        raise ValueError(
            f"Backend {config.driver.backend!r} on device {config.name!r} connects over "
            f"Hamilton USB and takes no connection block; name a specific instrument with "
            f"driver.serial_number / driver.device_address"
        )
    return STARLiquidHandlerDriver(
        serial_number=config.driver.serial_number,
        device_address=config.driver.device_address,
        visualize=visualize,
    )


_STAR_SPEC = _ConcreteLHSpec(live=_star_live, sim=None)

# Consulted before get_plr_backend_class, so a mapped name never reaches the bare wrapper.
# "OpentronsFlexBackend" is absent on purpose: PLR dropped it, so naming it still fails loudly.
_CONCRETE_LH_BY_BACKEND: dict[str, _ConcreteLHSpec] = {
    "OpentronsOT2Backend": _ConcreteLHSpec(
        live=lambda config, visualize: OT2LiquidHandlerDriver(
            *_tcp_endpoint(config), visualize=visualize
        ),
        sim=lambda host, port, server, visualize: OT2LiquidHandlerDriver(
            host, port, visualize=visualize, sim_server=server
        ),
    ),
    "STAR": _STAR_SPEC,
    "STARBackend": _STAR_SPEC,
    "OpentronsFlex": _ConcreteLHSpec(
        live=lambda config, visualize: FlexLiquidHandlerDriver(
            *_tcp_endpoint(config), visualize=visualize
        ),
        sim=lambda host, port, server, visualize: FlexLiquidHandlerDriver(
            host, port, sim_server=server, visualize=visualize
        ),
    ),
}

# Derived from the map so a new Hamilton alias cannot fall out of sync with it.
_INSTRUMENT_SELECTOR_BACKENDS = frozenset(
    name for name, spec in _CONCRETE_LH_BY_BACKEND.items() if spec is _STAR_SPEC
)


def _reject_unusable_instrument_selectors(config: DeviceConfig) -> None:
    """Only the Hamilton driver reads these, so anywhere else they would vanish unnoticed."""
    if config.driver.serial_number is None and config.driver.device_address is None:
        return
    if config.driver.backend in _INSTRUMENT_SELECTOR_BACKENDS:
        return
    raise ValueError(
        f"driver.serial_number / driver.device_address name one Hamilton STAR on the USB "
        f"bus; device {config.name!r} (backend {config.driver.backend!r}) does not read "
        f"them. Remove them, or address the device with driver.connection."
    )


def _precise_flex(config: DeviceConfig) -> ITransporterDriver:
    """The Brooks PreciseFlex arm, built from its plain-class driver.

    Its gripper geometry has no defaults the generic host/port path could supply,
    so the arm cannot be built by class lookup like a shaker or a sealer can.
    """
    host, port = _tcp_endpoint(config, default_port=10100)
    arm = config.driver.arm or ArmConfig()
    return PreciseFlexTransporterDriver(
        host=host,
        port=port,
        gripper_length=arm.gripper_length,
        gripper_z_offset=arm.gripper_z_offset,
        closed_gripper_position=arm.closed_gripper_position,
        is_dual_gripper=arm.is_dual_gripper,
        has_rail=arm.has_rail,
        timeout=arm.timeout,
    )


# Consulted before get_plr_backend_class, same as the liquid-handler map above.
# "PreciseFlex" is the pylabrobot.brooks device. The backend-shaped
# "PreciseFlexBackend" is the legacy arm and is no longer what an arm runs, so a
# config still naming it fails loudly at build rather than quietly running the old one.
_CONCRETE_TRANSPORTER_BY_BACKEND: dict[str, Callable[[DeviceConfig], ITransporterDriver]] = {
    "PreciseFlex": _precise_flex,
}


class DeviceFactory:
    """Creates driver instances from configuration."""

    def __init__(self, enable_plr_visualizer: bool = False) -> None:
        self._enable_plr_visualizer = enable_plr_visualizer

    def create_driver(
        self, config: DeviceConfig
    ) -> BaseDriver | ITransporterDriver | None:
        """Build the device's live-mode driver (dispatched for
        ``effective_mode=LIVE``), or None when there is nothing to build.

        An Opentrons liquid handler configured with a device-sim ``sim_server``
        but no real-robot ``connection`` returns None: its live mode is the
        operator's physical instrument, which orca-client does not construct, so
        a LIVE-mode command fails by design (nothing to connect to). Add a
        ``connection`` (the robot's host/port) and the live driver is built
        pointed at it -- ready the moment a robot is attached, and simply failing
        to connect until then. The device-sim-mode driver is built separately in
        ``create_sim_driver``.
        """
        _reject_unusable_instrument_selectors(config)
        if config.driver.sim_server is not None and config.driver.connection is None:
            return None
        if config.driver.type == "sim":
            return self._create_sim_driver(config)
        elif config.driver.type == "plr":
            return self._create_plr_driver(config)
        elif config.driver.type == "venus":
            return self._create_venus_driver(config)
        elif config.driver.type == "lab_sim":
            return self._create_lab_sim_driver(config)
        else:
            raise ValueError(f"Unknown driver type: {config.driver.type}")

    def create_sim_driver(
        self, config: DeviceConfig
    ) -> BaseDriver | ITransporterDriver | None:
        """Build the device's device-sim-mode driver (dispatched for
        ``effective_mode=DEVICE_SIM``), or None to fall back to the generic
        in-process Sim*Driver.

        Only a liquid handler declaring ``driver.sim_server`` gets one: its driver
        binds to an auto-booted Opentrons robot-server, so a DEVICE_SIM command
        exercises the vendor stack rather than the print-only in-process sim. That
        server is reached at a host/port, so a backend that has no such form (a
        USB-connected STAR) is rejected rather than handed an endpoint it cannot take.
        """
        sim = config.driver.sim_server
        if sim is None:
            return None
        if config.type != "liquid_handler" or config.driver.type != "plr" or not config.driver.backend:
            raise ValueError(
                f"sim_server is only valid on a 'plr' liquid_handler with a backend "
                f"(device {config.name!r})"
            )
        _reject_unusable_instrument_selectors(config)
        port = sim.port if sim.port is not None else _free_tcp_port()
        server = OpentronsSimServer(
            OpentronsSimServerSpec(
                interpreter=sim.interpreter,
                cwd=sim.cwd,
                simulator_config=sim.simulator_config,
            ),
            port=port,
        )
        spec = _CONCRETE_LH_BY_BACKEND.get(config.driver.backend)
        if spec is not None:
            if spec.sim is None:
                raise ValueError(_no_sim_server_form(config))
            return spec.sim("127.0.0.1", port, server, self._enable_plr_visualizer)
        backend_cls = get_plr_backend_class(config.driver.backend)
        if not _takes_host_and_port(backend_cls):
            raise ValueError(_no_sim_server_form(config))
        return PLRLiquidHandlerWrapper(
            backend=backend_cls(host="127.0.0.1", port=port),
            visualize=self._enable_plr_visualizer,
            sim_server=server,
        )

    def _create_lab_sim_driver(self, config: DeviceConfig) -> BaseDriver | ITransporterDriver:
        """Create a driver for a timed simulation.

        Same Sim*Driver classes as `_create_sim_driver` but constructed
        with a `SleepSim(scale_factor=...)` strategy so caller-supplied
        durations (shake, seal, centrifuge) translate into proportional
        real waits. The scale factor comes from `config.driver.scale_factor`
        and defaults to 0.0 (instant settle, matching the plain `sim` type).

        DEVICE_SIM dispatch caveat: ``DeviceRegistry.register()`` (in this
        same package) lazily pairs every registered live driver with a
        fresh ``Sim*Driver`` keyed by device type for DEVICE_SIM dispatch.
        That paired driver is constructed with the default ``SleepSim()``
        strategy (instant-settle) -- it does NOT inherit the
        ``scale_factor`` configured here. So commands dispatched at
        ``effective_mode=LIVE`` against a ``lab_sim``-configured device
        honor scale_factor; commands at ``effective_mode=DEVICE_SIM`` do
        not. That is by design: DEVICE_SIM realism is the cheshire-drivers
        Sim*Driver's own concern, and a timed simulation drives its devices
        at LIVE. Consumers wanting per-device-type
        scale_factor on DEVICE_SIM dispatch should configure the device
        for ``driver.type=lab_sim`` and submit workflows at
        ``default_run_mode=LIVE``, not DEVICE_SIM.
        """
        cls = SIM_DRIVER_CLS_BY_TYPE.get(config.type)
        if cls is None:
            raise ValueError(f"Unknown device type for lab_sim driver: {config.type}")
        strategy = SleepSim(scale_factor=config.driver.scale_factor)
        return cls(config.name, sim_strategy=strategy, faults=config.faults)

    def _create_sim_driver(self, config: DeviceConfig) -> BaseDriver | ITransporterDriver:
        """Create simulation driver."""
        cls = SIM_DRIVER_CLS_BY_TYPE.get(config.type)
        if cls is None:
            raise ValueError(f"Unknown device type for sim driver: {config.type}")
        return cls(config.name)

    def _create_plr_driver(self, config: DeviceConfig) -> BaseDriver | ITransporterDriver:
        """Create PyLabRobot driver."""
        backend_name = config.driver.backend
        if not backend_name:
            raise ValueError(f"PLR backend not specified for device {config.name}")

        if config.type == "liquid_handler":
            spec = _CONCRETE_LH_BY_BACKEND.get(backend_name)
            if spec is not None:
                return spec.live(config, self._enable_plr_visualizer)
        if config.type in ("transporter", "translator"):
            build = _CONCRETE_TRANSPORTER_BY_BACKEND.get(backend_name)
            if build is not None:
                return build(config)

        backend = self._create_plr_backend(config, backend_name)

        wrapper_cls = _PLR_WRAPPER_BY_TYPE.get(config.type)
        if wrapper_cls is not None:
            return wrapper_cls(backend)
        if config.type in ("transporter", "translator"):
            # No teachpoint store on the driver: the server inlines fully
            # flattened Teachpoint values into pick_at_coords / place_at_coords
            # / move_to_coords, so the wrapper never resolves names.
            return PLRTransporterBackendWrapper(backend)
        if config.type == "liquid_handler":
            return self._create_plr_liquid_handler(config, backend_name, backend)
        raise ValueError(f"Unknown device type for PLR driver: {config.type}")

    def _create_plr_backend(self, config: DeviceConfig, backend_name: str) -> Any:
        """Instantiate PyLabRobot backend from config."""
        backend_class = self._get_plr_backend_class(backend_name)

        # Create backend based on connection type
        if config.driver.connection:
            if config.driver.connection.type == "serial":
                return backend_class(port=config.driver.connection.port)
            elif config.driver.connection.type == "tcp":
                return backend_class(
                    host=config.driver.connection.host,
                    port=config.driver.connection.tcp_port
                )
            elif config.driver.connection.type == "usb":
                return backend_class(device_path=config.driver.connection.port)
        else:
            # No connection config - instantiate with defaults
            return backend_class()

    def _get_plr_backend_class(self, backend_name: str) -> type:
        """Get backend class by name via cheshire-drivers' centralized lookup."""
        try:
            return get_plr_backend_class(backend_name)
        except ImportError as e:
            logger.error(f"Failed to import backend '{backend_name}': {e}")
            raise

    def _create_plr_liquid_handler(
        self, config: DeviceConfig, backend_name: str, backend: Any
    ) -> PLRLiquidHandlerWrapper:
        """Create PLR liquid handler.

        The PLR deck is constructed inside the wrapper when configure_deck is called.
        Per cheshire-drivers PLRLiquidHandlerWrapper.__init__: signature is (backend, visualize).
        """
        return PLRLiquidHandlerWrapper(backend=backend, visualize=self._enable_plr_visualizer)

    def _create_venus_driver(self, config: DeviceConfig) -> BaseDriver:
        """Create Hamilton Venus protocol-runner driver.

        Venus drivers run vendor HSL methods via the local ``HxRun.exe``;
        the executable path, methods folder, and per-event protocol filepaths
        come from ``config.driver.venus`` (a ``VenusConfig`` block). When the
        block is omitted, the driver falls back to the Hamilton install
        defaults but the per-event protocol fields remain empty so events
        like ``prepare_for_pick`` are no-ops.
        """
        if config.type != "liquid_handler":
            raise ValueError(
                f"Venus driver requires device type 'liquid_handler', "
                f"got {config.type!r} for device {config.name!r}"
            )
        from ..config.models import VenusConfig
        venus = config.driver.venus or VenusConfig()
        return VenusProtocolDriver(
            name=config.name,
            init_protocol=venus.init_protocol,
            picked_protocol=venus.picked_protocol,
            placed_protocol=venus.placed_protocol,
            prepare_pick_protocol=venus.prepare_pick_protocol,
            prepare_place_protocol=venus.prepare_place_protocol,
            open_protocol=venus.open_protocol,
            close_protocol=venus.close_protocol,
            exe_path=venus.exe_path,
            methods_folder=venus.methods_folder,
        )
