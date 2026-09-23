"""Device registry for managing driver instances.

# CLIENT-SPECIFIC: Simple device lookup for command execution

In addition to the configured live/PLR/Venus driver per device, the
registry lazily builds a paired cheshire-drivers Sim* driver keyed by
device type. The executor dispatches to the live driver under
``effective_mode = LIVE`` and to the sim driver under
``effective_mode = DEVICE_SIM``. Both drivers share the per-device asyncio
lock so concurrent dispatch on the same device is impossible regardless
of mode.
"""

import asyncio
import inspect
import logging
from typing import Dict, Optional, List
from cheshire_drivers import (
    BaseDriver,
    ITransporterDriver,
)
from cheshire_drivers.driver_introspection import (
    derive_capabilities,
    describe_driver,
)

from cheshire_drivers.gateway_protocol import (
    DeviceConnectInfo,
    DeviceLinkInfo,
    DeviceStatusInfo,
    DriverMode,
)
from .sim_driver_types import SIM_DRIVER_CLS_BY_TYPE

logger = logging.getLogger("orca_client.registry")


class DeviceRegistry:
    """Manages device driver instances with locks."""

    def __init__(self):
        self._drivers: Dict[str, BaseDriver | ITransporterDriver] = {}
        self._sim_drivers: Dict[str, BaseDriver | ITransporterDriver] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._device_types: Dict[str, str] = {}
        self._device_names: Dict[str, str] = {}

    def register(
        self,
        name: str,
        device_type: str,
        driver: BaseDriver | ITransporterDriver | None = None,
        sim_driver: BaseDriver | ITransporterDriver | None = None,
    ):
        """Register a device driver.

        Args:
            name: Operator-visible device name (binding key against topology)
            device_type: Device kind label (shaker, centrifuge, etc.)
            driver: LIVE driver instance (LIVE dispatch), or None when orca-client
                builds no LIVE driver (e.g. an Opentrons LH whose LIVE is the
                operator's real instrument). LIVE dispatch to a None-driver device
                fails with DeviceNotFoundError at the executor, by design.
            sim_driver: Explicit DEVICE_SIM driver (e.g. a device-owned vendor
                simulator). When None, the generic in-process Sim*Driver for the
                device type is paired instead.
        """
        device_id = name
        self._locks[device_id] = asyncio.Lock()
        self._device_types[device_id] = device_type
        self._device_names[device_id] = name

        # Attach lock to driver for convenience (dynamic attribute)
        if driver is not None:
            self._drivers[device_id] = driver
            driver.lock = self._locks[device_id]  # type: ignore[attr-defined]

        # Pair every device with an in-process Sim* driver matching its
        # type. The executor dispatches to this driver when the wire
        # CommandMessage carries effective_mode = DEVICE_SIM. Sharing the
        # same lock as the live driver keeps concurrent dispatch impossible
        # across modes.
        #
        # `lab_sim` interaction: a device registered with a `lab_sim`-built
        # live driver (carrying a SleepSim(scale_factor=N) strategy) gets
        # paired with a fresh default-strategy Sim* (instant-settle) here.
        # DEVICE_SIM dispatch on this device therefore does NOT honor the
        # configured scale_factor; only LIVE dispatch does. This is by
        # design: DEVICE_SIM realism is the Sim*Driver's own concern, and a
        # timed simulation drives its devices at LIVE. Documented here so a
        # future debugger doesn't chase
        # "why doesn't my scale_factor apply" under DEVICE_SIM.
        if sim_driver is None:
            sim_cls = SIM_DRIVER_CLS_BY_TYPE.get(device_type)
            if sim_cls is not None:
                sim_driver = sim_cls(device_id)
        if sim_driver is not None:
            sim_driver.lock = self._locks[device_id]  # type: ignore[attr-defined]
            self._sim_drivers[device_id] = sim_driver
        else:
            logger.warning(
                "No Sim* driver registered for device type %r (device %r); "
                "DEVICE_SIM dispatches to this device will fail at executor.",
                device_type,
                device_id,
            )

        advertised = self._advertised_driver(device_id)
        if advertised is not None and not self._interfaces_of(advertised):
            logger.error(
                "Driver class %s for device %r declares no `interfaces` "
                "ClassVar (or it is empty). It will not be advertised, so "
                "commands sent to it fail capability validation "
                "server-side. Fix the driver class to declare "
                "`interfaces: ClassVar[frozenset[str]] = frozenset({...})`.",
                type(advertised).__name__,
                device_id,
            )

        logger.debug(f"Registered device: {device_id} ({device_type})")

    def get_driver(self, device_id: str) -> Optional[BaseDriver | ITransporterDriver]:
        """Get driver by device ID.

        Args:
            device_id: Device identifier

        Returns:
            Driver instance or None if not found
        """
        return self._drivers.get(device_id)

    def get_sim_driver(
        self, device_id: str,
    ) -> Optional[BaseDriver | ITransporterDriver]:
        """Get the paired in-process Sim* driver for a device.

        Returns None when no sim driver was built (unknown device_type,
        or device not registered). The executor uses this when the wire
        CommandMessage carries effective_mode = DEVICE_SIM so the live
        backend stays untouched while the simulator answers.
        """
        return self._sim_drivers.get(device_id)

    def lock_for(self, device_id: str) -> Optional[asyncio.Lock]:
        """Get the per-device dispatch lock, or None if the device is unknown.

        Both the live and the paired sim driver share this one lock, so
        concurrent dispatch on a device is impossible across modes.
        """
        return self._locks.get(device_id)

    def is_busy(self, device_id: str) -> bool:
        """True while a command holds this device's dispatch lock."""
        lock = self.lock_for(device_id)
        return lock is not None and lock.locked()

    def get_device_type(self, device_id: str) -> Optional[str]:
        """Get device type by ID."""
        return self._device_types.get(device_id)

    def get_device_name(self, device_id: str) -> Optional[str]:
        """Get device name by ID."""
        return self._device_names.get(device_id)

    def get_all_device_ids(self) -> List[str]:
        """Get list of all registered device IDs."""
        return list(self._device_types.keys())

    def get_all_devices(self) -> Dict[str, Dict[str, str]]:
        """Get all devices with their metadata.

        Returns:
            Dict mapping device_id to {type, name}
        """
        return {
            device_id: {
                "type": self._device_types[device_id],
                "name": self._device_names[device_id]
            }
            for device_id in self._device_types.keys()
        }

    def connect_info_list(self) -> List[DeviceConnectInfo]:
        """Build the per-device handshake metadata for ConnectMessage.

        - `interfaces` is read off the driver class as a manual ClassVar.
        - `capabilities` is auto-derived: public methods + properties on the
          concrete class minus members on its declared abstract interfaces.
        - `provides_state` is read off the driver class (LH-specific flag).
        - `methods` is the full per-member introspection payload (signatures,
          return types, docstrings) used by the REST + MCP introspection
          endpoint to surface what is callable and how.
        """
        out: List[DeviceConnectInfo] = []
        for device_id, driver in self._advertised_devices():
            cls = type(driver)
            out.append(
                DeviceConnectInfo(
                    name=self._device_names[device_id],
                    type=self._device_types[device_id],
                    interfaces=self._interfaces_of(driver),
                    capabilities=derive_capabilities(cls),
                    provides_state=bool(getattr(cls, "provides_state", False)),
                    methods=describe_driver(driver),
                )
            )
        return out

    @staticmethod
    def _interfaces_of(driver: BaseDriver | ITransporterDriver) -> frozenset[str]:
        """The abstract interfaces this driver's class declares."""
        return frozenset(getattr(type(driver), "interfaces", frozenset()))

    def _advertised_driver(
        self, device_id: str,
    ) -> Optional[BaseDriver | ITransporterDriver]:
        """The driver whose class describes this device at the handshake.

        A sim-only device (no LIVE driver, e.g. DEVICE_SIM Opentrons) is
        described by its sim driver: same class, same capabilities.
        """
        return self._drivers.get(device_id) or self._sim_drivers.get(device_id)

    def _advertised_devices(
        self,
    ) -> List[tuple[str, BaseDriver | ITransporterDriver]]:
        """Every device the handshake advertises, with the driver describing it.

        The handshake and the status report have to agree on this set, or the
        agent reports on a device the server was never told about.
        """
        out: List[tuple[str, BaseDriver | ITransporterDriver]] = []
        for device_id in self._device_types:
            driver = self._advertised_driver(device_id)
            if driver is None or not self._interfaces_of(driver):
                continue
            out.append((device_id, driver))
        return out

    def status_report(self) -> Dict[str, DeviceStatusInfo]:
        """What the agent currently sees on each device it advertised.

        The link is reported per run mode because dispatch picks per mode, and
        the two drivers hold their own state: one pair of flags would describe
        whichever driver was read here, not the one the next command runs on.
        ``status`` is per device, since both drivers share the dispatch lock.
        """
        out: Dict[str, DeviceStatusInfo] = {}
        for device_id, _ in self._advertised_devices():
            per_mode: tuple[
                tuple[DriverMode, Optional[BaseDriver | ITransporterDriver]], ...
            ] = (
                ("LIVE", self._drivers.get(device_id)),
                ("DEVICE_SIM", self._sim_drivers.get(device_id)),
            )
            links: Dict[DriverMode, DeviceLinkInfo] = {}
            for mode, driver in per_mode:
                if driver is not None:
                    links[mode] = self._read_link(device_id, mode, driver)
            out[self._device_names[device_id]] = DeviceStatusInfo(
                status="busy" if self.is_busy(device_id) else "ready",
                links=links,
            )
        return out

    @staticmethod
    def _read_link(
        device_id: str,
        mode: DriverMode,
        driver: BaseDriver | ITransporterDriver,
    ) -> DeviceLinkInfo:
        """One driver's link state, reported closed when it cannot answer.

        Two things this must not do. It must not raise, or one bad driver
        blanks every other device's link. And it must not drop the entry:
        absence on the wire means the agent holds no driver for that mode, which
        sends the operator to the topology instead of to this driver.
        """
        try:
            return DeviceLinkInfo(
                is_connected=driver.is_connected,
                is_initialized=driver.is_initialized,
            )
        except Exception:
            logger.error(
                "Could not read %s link state for device %r; reporting it "
                "closed", mode, device_id, exc_info=True,
            )
            return DeviceLinkInfo(is_connected=False, is_initialized=False)

    async def initialize_all(self):
        """Initialize all devices on startup.

        This is CRITICAL for beta - validates all hardware before connecting
        to platform. Fails fast with clear errors if any device is unreachable.

        Raises:
            RuntimeError: If any device fails to initialize
        """
        logger.info(f"Initializing {len(self._drivers)} devices...")
        errors = []

        for device_id, driver in self._drivers.items():
            try:
                logger.info(f"Initializing {device_id}...")
                await self._call_initialize(driver)
                logger.info(f"{device_id} initialized successfully")
            except Exception as e:
                error_msg = f"{device_id}: {e}"
                errors.append(error_msg)
                logger.error(f"Failed to initialize {device_id}: {e}", exc_info=True)

        if errors:
            logger.error("Device initialization failed:")
            for error in errors:
                logger.error(f"  • {error}")
            raise RuntimeError(
                f"Device initialization failed. Fix configuration and try again.\n"
                + "\n".join(f"  • {e}" for e in errors)
            )

        logger.info(f"All {len(self._drivers)} devices initialized successfully")

    async def _call_initialize(self, driver) -> None:
        """Call driver.initialize() honoring its parameter shape.

        BaseDriver-derived drivers (shaker, sealer, centrifuge, LH, ...)
        declare `initialize(self) -> None`. ITransporterDriver declares
        `initialize(self, request: InitializeRequest) -> None`. Inspect the
        signature to construct the right call.
        """
        sig = inspect.signature(driver.initialize)
        params = [p for p in sig.parameters.values() if p.name != "self"]
        if not params:
            await driver.initialize()
            return
        if len(params) == 1:
            request_cls = params[0].annotation
            await driver.initialize(request_cls())
            return
        raise TypeError(
            f"Unexpected initialize() signature on {type(driver).__name__}: "
            f"{sig}"
        )

    async def cleanup_all(self):
        """Release every driver on shutdown, each by the lifecycle it has.

        A BaseDriver gets ``close`` (its per-op close/parking behavior) then
        ``_shutdown`` (the disposal hook): a device-owned vendor simulator (e.g.
        an Opentrons robot-server) stops its server in ``_shutdown`` so it does
        not outlive the client session.

        A transporter has neither, so it gets ``disconnect``, which is its own
        hand-back. On an arm that holds high power between commands, that is
        what drops it; skipping the arm left it powered after the client exited.
        Every step is guarded on its own so one failure still runs the rest.
        """
        logger.info("Cleaning up devices...")
        for device_id, driver in list(self._drivers.items()) + list(self._sim_drivers.items()):
            if isinstance(driver, BaseDriver):
                try:
                    await driver.close()
                except Exception as e:
                    logger.error(f"Error closing {device_id}: {e}")
                try:
                    await driver._shutdown()
                except Exception as e:
                    logger.error(f"Error shutting down {device_id}: {e}")
            elif isinstance(driver, ITransporterDriver):
                try:
                    await driver.disconnect()
                except Exception as e:
                    logger.error(f"Error disconnecting {device_id}: {e}")

        logger.info("Device cleanup complete")
