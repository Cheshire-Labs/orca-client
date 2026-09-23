"""Transporter executor wire-shape tests.

Regression guard. Mirrors `test_executor_liquid_handler.py` for the
transporter category: dispatches every transporter command via
CommandExecutor against a real SimTransporterDriver (which now expects Pydantic
Request models) with flat-dict params from the wire. Without these tests, the
orca-client executor `method(**params)` call would TypeError on every
transporter command after the Pydantic uplift, and we would only learn about
it via manual integration testing.
"""

from typing import AsyncGenerator

import pytest

from orca_client.config.models import DeviceConfig, DriverConfig
from orca_client.devices import DeviceFactory, DeviceRegistry
from orca_client.executor import CommandExecutor
from cheshire_drivers.move_parameters import SEED_MOVE_PARAMETERS
from cheshire_drivers.gateway_protocol import CommandMessage


@pytest.fixture
def sim_transporter_config() -> DeviceConfig:
    return DeviceConfig(
        type="transporter",
        name="test_arm",
        driver=DriverConfig(type="sim"),
    )


@pytest.fixture
async def transporter_registry(
    device_factory: DeviceFactory,
    sim_transporter_config: DeviceConfig,
) -> AsyncGenerator[DeviceRegistry, None]:
    registry = DeviceRegistry()
    driver = device_factory.create_driver(sim_transporter_config)
    registry.register(
        name=sim_transporter_config.name,
        device_type=sim_transporter_config.type,
        driver=driver,
    )
    await registry.initialize_all()
    yield registry
    await registry.cleanup_all()


def _seed_position(registry: DeviceRegistry, position: str, plate_name: str) -> None:
    """Seed a labware at a sim position so subsequent picks succeed."""
    from pylabrobot.resources.resource import Resource as PLRResource

    driver = registry.get_driver("test_arm")
    plate = PLRResource(name=plate_name, size_x=127.76, size_y=85.48, size_z=14.0)
    driver._seed_resource(position, plate)  # type: ignore[attr-defined]


_HANDLING = SEED_MOVE_PARAMETERS.model_dump()
"""The resolved move scalars the server sends with every pick and place. The server
owns the layering; the client validates the shape and hands it to the driver."""


_TEACHPOINT_DICT = {
    "position_id": "nest_1",
    "x": 1.0,
    "y": 2.0,
    "z": 3.0,
    "yaw": 0.0,
    "pitch": 0.0,
    "roll": 0.0,
    "orientation": "left",
    "access_type": "vertical",
    "gripper_offset": 20.0,
    "vertical_clearance": 20.0,
    "horizontal_clearance": 100.0,
}


class TestTransporterExecutor:
    """Wire-shape regression: every transporter command dispatches with dict params."""

    @pytest.mark.asyncio
    async def test_pick_at_coords_with_teachpoint_dict_succeeds(
        self, transporter_registry: DeviceRegistry
    ) -> None:
        _seed_position(transporter_registry, "nest_1", "plate_a")
        executor = CommandExecutor(transporter_registry)
        cmd = CommandMessage(
            command_id="t2",
            device_name="test_arm",
            command="pick_at_coords",
            params={"teachpoint": _TEACHPOINT_DICT, "handling": _HANDLING},
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert response.success, f"Wire broken: {response.error}"

    @pytest.mark.asyncio
    async def test_move_to_coords_with_teachpoint_dict_succeeds(
        self, transporter_registry: DeviceRegistry
    ) -> None:
        """Regression guard: the executor's wire-dict -> Pydantic Request ->
        driver method round-trip works for move_to_coords. After Path 2
        retired the name-based move_to_position command, move_to_coords is
        the sole "go to a teachpoint without picking/placing" wire entry."""
        executor = CommandExecutor(transporter_registry)
        cmd = CommandMessage(
            command_id="t12",
            device_name="test_arm",
            command="move_to_coords",
            params={"teachpoint": _TEACHPOINT_DICT},
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert response.success, f"Wire broken: {response.error}"

    @pytest.mark.asyncio
    async def test_unknown_field_rejected_as_parameter_error(
        self, transporter_registry: DeviceRegistry
    ) -> None:
        executor = CommandExecutor(transporter_registry)
        cmd = CommandMessage(
            command_id="t3",
            device_name="test_arm",
            command="pick_at_coords",
            params={"teachpoint": _TEACHPOINT_DICT, "handling": _HANDLING, "bogus": 1},
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert not response.success
        assert response.error_type == "ParameterError"

    @pytest.mark.asyncio
    async def test_home_with_empty_params_succeeds(
        self, transporter_registry: DeviceRegistry
    ) -> None:
        executor = CommandExecutor(transporter_registry)
        cmd = CommandMessage(
            command_id="t4",
            device_name="test_arm",
            command="home",
            params={},
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert response.success, f"Wire broken: {response.error}"

    @pytest.mark.asyncio
    async def test_set_free_mode_with_string_axes_succeeds(
        self, transporter_registry: DeviceRegistry
    ) -> None:
        executor = CommandExecutor(transporter_registry)
        cmd = CommandMessage(
            command_id="t6",
            device_name="test_arm",
            command="set_free_mode",
            params={"axes": "all"},
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert response.success, f"Wire broken: {response.error}"

    @pytest.mark.asyncio
    async def test_set_free_mode_with_list_axes_succeeds(
        self, transporter_registry: DeviceRegistry
    ) -> None:
        executor = CommandExecutor(transporter_registry)
        cmd = CommandMessage(
            command_id="t7",
            device_name="test_arm",
            command="set_free_mode",
            params={"axes": ["rail", "base"]},
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert response.success, f"Wire broken: {response.error}"

    @pytest.mark.asyncio
    async def test_set_speed_with_dict_params_succeeds(
        self, transporter_registry: DeviceRegistry
    ) -> None:
        executor = CommandExecutor(transporter_registry)
        cmd = CommandMessage(
            command_id="t8",
            device_name="test_arm",
            command="set_speed",
            params={"speed": 0.75},
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert response.success, f"Wire broken: {response.error}"

    @pytest.mark.asyncio
    async def test_get_speed_returns_typed_response(
        self, transporter_registry: DeviceRegistry
    ) -> None:
        # Executor projects the driver's float return onto SpeedResponse
        # so the wire shape is self-describing across the gateway boundary.
        executor = CommandExecutor(transporter_registry)
        cmd = CommandMessage(
            command_id="t9",
            device_name="test_arm",
            command="get_speed",
            params={},
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert response.success
        assert isinstance(response.result, dict)
        assert "speed" in response.result
        assert isinstance(response.result["speed"], float)

    @pytest.mark.asyncio
    async def test_get_joint_position_returns_dict(
        self, transporter_registry: DeviceRegistry
    ) -> None:
        executor = CommandExecutor(transporter_registry)
        cmd = CommandMessage(
            command_id="t10",
            device_name="test_arm",
            command="get_joint_position",
            params={},
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert response.success
        # JointCoordinates is now a Pydantic _StrictModel; the executor's
        # `result.model_dump()` path produces a wire-friendly dict.
        assert isinstance(response.result, dict)
        assert {"rail", "base", "shoulder", "elbow", "wrist", "gripper"} <= set(
            response.result
        )

    @pytest.mark.asyncio
    async def test_is_initialized_returns_typed_response(
        self, transporter_registry: DeviceRegistry
    ) -> None:
        # Property-query path: BaseDriver.is_initialized returns a bool that
        # the executor projects onto InitializedResponse.is_initialized so
        # the wire payload stays self-describing post typed-surface.
        executor = CommandExecutor(transporter_registry)
        cmd = CommandMessage(
            command_id="t11",
            device_name="test_arm",
            command="is_initialized",
            params={},
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert response.success
        assert isinstance(response.result, dict)
        assert "is_initialized" in response.result
        assert isinstance(response.result["is_initialized"], bool)

    @pytest.mark.asyncio
    async def test_is_connected_returns_typed_response(
        self, transporter_registry: DeviceRegistry
    ) -> None:
        """The device's own link has to be askable, or it is not knowable.

        Two connections matter here and only the client's heartbeat was ever
        reachable from the cloud. This is the other one, and it needs both the
        property allow-list and a response model with a field to land in --
        without the model the bool has nothing to project onto and the query
        comes back an error instead.
        """
        executor = CommandExecutor(transporter_registry)
        cmd = CommandMessage(
            command_id="t12",
            device_name="test_arm",
            command="is_connected",
            params={},
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert response.success, response.error
        assert isinstance(response.result, dict)
        assert isinstance(response.result["is_connected"], bool)

    @pytest.mark.asyncio
    async def test_ack_only_command_returns_empty_response_dict(
        self, transporter_registry: DeviceRegistry
    ) -> None:
        # Ack-only commands (driver returns None) must wrap as
        # EmptyCommandResponse() so the gateway sees a populated `result`
        # field rather than null. Pre-fix the executor passed None through
        # and the gateway side relied on a silent fallback.
        executor = CommandExecutor(transporter_registry)
        cmd = CommandMessage(
            command_id="t12",
            device_name="test_arm",
            command="home",
            params={},
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert response.success
        # EmptyCommandResponse is `{}` post model_dump.
        assert response.result == {}
