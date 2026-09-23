"""Configuration models for orca-client.

Uses Pydantic for validation of JSON configuration files.
"""

from pydantic import BaseModel, Field, PositiveFloat, field_validator, model_validator
from typing import Literal, Optional, List
from urllib.parse import urlparse

from cheshire_drivers.faults import FaultSpec


DEFAULT_HEARTBEAT_SECONDS = 30.0
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")


class ConnectionConfig(BaseModel):
    """Device connection configuration."""
    type: Literal["serial", "tcp", "usb"]
    port: Optional[str] = None           # Serial/USB port
    baudrate: Optional[int] = 9600       # Serial baudrate
    host: Optional[str] = None           # TCP host
    tcp_port: Optional[int] = None       # TCP port


class ArmConfig(BaseModel):
    """Physical build of a robotic arm, for the arm backends that need it.

    Bring-up reads the arm's link lengths and tool length off the controller, so
    those are not here. What is left is what the controller cannot report: the
    tool's vertical offset from the wrist plate, and the jaw value at the fitted
    gripper's minimum width. Both are properties of the gripper someone bolted on,
    so they belong in the deployment's config rather than in a driver default.
    """
    gripper_length: float = Field(
        default=162.0,
        description="Wrist axis to tool tip, mm. A fallback: bring-up reads the real one.",
    )
    gripper_z_offset: float = Field(
        default=0.0,
        description="Wrist plate to tool tip vertical offset, mm.",
    )
    closed_gripper_position: float = Field(
        default=75.5,
        description="Firmware jaw value at the fitted gripper's minimum width.",
    )
    is_dual_gripper: bool = False
    has_rail: bool = False
    timeout: int = Field(default=20, description="Socket timeout, seconds.")


class VenusConfig(BaseModel):
    """Hamilton Venus driver configuration.

    Pairs with ``DriverConfig.type == "venus"``. Specifies the HxRun.exe
    path, the methods folder root, and the per-event protocol filepaths
    invoked when the workflow tells the device to initialize, prepare for
    pick / place, or notify of pick / place. All protocol fields are
    optional; when unset, the corresponding event is a no-op.
    """
    exe_path: str = Field(
        default=r"C:\Program Files (x86)\HAMILTON\Bin\HxRun.exe",
        description="Path to the Hamilton Venus runtime executable",
    )
    methods_folder: str = Field(
        default=r"C:\Program Files (x86)\HAMILTON\Methods",
        description="Root folder for relative method filepaths",
    )
    init_protocol: Optional[str] = None
    picked_protocol: Optional[str] = None
    placed_protocol: Optional[str] = None
    prepare_pick_protocol: Optional[str] = None
    prepare_place_protocol: Optional[str] = None
    open_protocol: Optional[str] = None
    close_protocol: Optional[str] = None


class OpentronsSimServerConfig(BaseModel):
    """Launch spec for a device-owned simulated Opentrons robot-server.

    When set on a ``plr`` Opentrons liquid handler, DEVICE_SIM boots this robot-server
    automatically and drives the vendor's own simulator over it (instead of the generic
    in-process Sim*Driver), then stops it with the device. LIVE is unaffected -- it uses
    the live ``connection``, which fails until real hardware is attached. ``port`` is
    auto-allocated per device when unset, so two liquid handlers get two servers.
    """
    interpreter: str        # a Python that can import robot_server + uvicorn
    cwd: str                # the robot-server working directory
    simulator_config: str   # instrument-config JSON (mounted pipettes + gripper)
    port: Optional[int] = None


class DriverConfig(BaseModel):
    """Driver configuration.

    Note: there is no `teachpoints_file` field. The server resolves
    position_ids to fully-flattened Teachpoint values and pushes them
    over the wire; orca-client never holds a local teachpoint registry.

    The ``lab_sim`` driver type builds a cheshire-drivers ``Sim*Driver``
    with a ``SleepSim(scale_factor=...)`` strategy, so a duration on the wire
    (shake / centrifuge / seal) adds ``duration * scale_factor`` seconds to
    the sim's own settle time. At the default ``scale_factor=0.0`` the
    duration is ignored. It is set per device in place of any other driver
    type; mode routing, command capture and the registry handshake are
    unchanged.
    """
    type: Literal["plr", "venus", "sim", "lab_sim"]
    # A PyLabRobot backend class name ("STARBackend", "OpentronsOT2Backend"), or a device
    # name the factory maps to a concrete driver ("OpentronsFlex"). See the factory's map.
    backend: Optional[str] = None
    connection: Optional[ConnectionConfig] = None
    # Which Hamilton STAR to bind when several are on the USB bus. A STAR takes no
    # `connection` block, so these are the only way to name one instrument of many.
    serial_number: Optional[str] = None
    device_address: Optional[int] = None
    deck_type: Optional[Literal["STAR", "STARLet"]] = None  # For liquid handlers
    venus: Optional[VenusConfig] = None  # Hamilton Venus driver configuration
    # Arm build (gripper geometry, rail). Only the arm backends read it; the defaults
    # match a standard-reach PreciseFlex with a single plate gripper.
    arm: Optional[ArmConfig] = None
    # `lab_sim`-only: scale factor for `SleepSim` so duration parameters
    # passed over the wire produce proportional real waits. 0.0 keeps the
    # historical instant-settle behavior. Recommended values: 1.0 for
    # real-time, 0.001 for compressed-realism tests.
    scale_factor: float = 0.0
    # When set (Opentrons liquid handler), DEVICE_SIM boots this robot-server and drives
    # the vendor sim over it instead of the generic Sim*Driver. See OpentronsSimServerConfig.
    sim_server: Optional[OpentronsSimServerConfig] = None


class DeviceConfig(BaseModel):
    """Single device configuration.

    ``name`` is the operator-visible identifier and the binding key against
    the runtime's topology declaration. A topology ``Shaker(name="shaker_1")`` binds
    to the orca-client device whose config has ``name: "shaker_1"``.

    ``faults`` is honored only for ``driver.type=lab_sim`` drivers; pre-armed
    faults raise/hang on selected calls of named driver methods. Faults
    targeting other driver types are accepted by the schema but never
    fire (they construct a real PLR/Venus/sim driver that has no fault
    hook). Boot-time validation rejects unknown method names + invalid
    error_type strings on the lab_sim path; see
    ``cheshire_drivers.faults`` for the spec shape.
    """
    type: Literal["shaker", "centrifuge", "sealer", "transporter", "translator", "liquid_handler", "plate_washer", "reader", "delidder", "storage", "waste", "thermocycler"]
    name: str = Field(..., description="Operator-visible device name (binding key against topology)")
    driver: DriverConfig
    faults: List[FaultSpec] = Field(
        default_factory=list,
        description="Pre-armed faults applied to the device's sim driver (lab_sim driver type only).",
    )

    @model_validator(mode="after")
    def _faults_only_on_lab_sim(self):
        if self.faults and self.driver.type != "lab_sim":
            raise ValueError(
                f"DeviceConfig {self.name!r}: fault injection is only "
                f"wired through the ``lab_sim`` factory (got "
                f"driver.type={self.driver.type!r}). Use ``lab_sim`` "
                f"instead. Note: ``sim`` and ``lab_sim`` build the same "
                f"underlying ``Sim*Driver`` class -- the fence is by "
                f"design to keep one canonical fault-config surface, "
                f"not because the fault hook is missing on ``sim``."
            )
        return self


class PlatformConfig(BaseModel):
    """Connection to the Orca gateway."""
    url: str = Field(..., description="WebSocket URL (wss://... or ws://localhost)")
    api_key: str = Field(..., description="API key for authentication")
    reconnect_backoff: List[PositiveFloat] = Field(
        default=[1, 2, 4, 8, 16, 30],
        min_length=1,
        description="Seconds to wait before each redial after a failure; the last value repeats",
    )
    heartbeat_interval: float = Field(
        default=DEFAULT_HEARTBEAT_SECONDS,
        description="Heartbeat interval in seconds"
    )

    @field_validator("url")
    @classmethod
    def _remote_hosts_need_wss(cls, url: str) -> str:
        """Refuse plain ws:// to another host here, so the agent never starts rather than redialing forever."""
        parsed = urlparse(url)
        if parsed.scheme == "ws" and parsed.hostname not in LOOPBACK_HOSTS:
            raise ValueError(
                f"Insecure WebSocket (ws://) not allowed for remote hosts. "
                f"Use wss:// for {parsed.hostname}"
            )
        return url


class ClientConfig(BaseModel):
    """Complete client configuration."""
    client_id: str = Field(..., description="Unique client identifier")
    site: str = Field(..., description="Geographic site location")
    lab: str = Field(..., description="Lab or workcell name")
    workcell: Optional[str] = Field(default=None, description="Optional workcell identifier")
    enable_plr_visualizer: bool = Field(
        default=False,
        description="When True, PLR liquid handlers open the PLR Visualizer at localhost:1337."
    )
    platform: PlatformConfig
    devices: List[DeviceConfig]

    def devices_metadata(self) -> List[dict]:
        """Get device metadata for connection message.

        Returns:
            List of device metadata dicts
        """
        return [
            {
                "type": device.type,
                "name": device.name,
            }
            for device in self.devices
        ]
