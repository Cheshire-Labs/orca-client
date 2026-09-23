"""Liquid-handler executor wire-shape tests.

These tests exercise the orca-client CommandExecutor against a real
SimLiquidHandlerDriver (which uses the post-77d445a Pydantic Request shape)
with dict params from the wire. They are the regression guard for the
silent wire-break that landed when cheshire-drivers commit 77d445a unified
the driver interface to Pydantic models.

Without these tests, orca-client's executor `method(**params)` call would
TypeError on every aspirate (because the driver expects `request: AspirateRequest`,
not flat kwargs) and we would only find out by manual integration testing.
"""

import pytest
from typing import AsyncGenerator

from orca_client.executor import CommandExecutor
from cheshire_drivers.gateway_protocol import CommandMessage
from orca_client.devices import DeviceRegistry, DeviceFactory
from orca_client.config.models import DeviceConfig, DriverConfig


@pytest.fixture
def sim_liquid_handler_config() -> DeviceConfig:
    """Sim-driver liquid handler with the new Pydantic interface."""
    return DeviceConfig(
        type="liquid_handler",
        name="test_lh",
        driver=DriverConfig(type="sim"),
    )


@pytest.fixture
async def lh_registry(
    device_factory: DeviceFactory,
    sim_liquid_handler_config: DeviceConfig,
) -> AsyncGenerator[DeviceRegistry, None]:
    registry = DeviceRegistry()
    driver = device_factory.create_driver(sim_liquid_handler_config)
    registry.register(
        name=sim_liquid_handler_config.name,
        device_type=sim_liquid_handler_config.type,
        driver=driver,
    )
    await registry.initialize_all()
    yield registry
    await registry.cleanup_all()


class TestLiquidHandlerExecutor:
    """Wire-shape regression tests for liquid-handler commands.

    These confirm that the executor wraps flat-dict params into the cheshire-drivers
    Pydantic Request models before invoking the driver method. They are the test
    that would have caught the 77d445a Pydantic-unify regression.
    """

    @pytest.mark.asyncio
    async def test_aspirate_with_dict_params_succeeds(self, lh_registry: DeviceRegistry):
        """Regression test for 77d445a + multi-rack #6: dict params must be
        wrapped into AspirateRequest with the new ``aspirations`` shape."""
        executor = CommandExecutor(lh_registry)
        cmd = CommandMessage(
            command_id="test_asp_1",
            device_name="test_lh",
            command="aspirate",
            params={
                "aspirations": [
                    {"labware": "src_plate", "positions": ["A1"], "volumes": [50.0]},
                ],
                "flow_rate": 100.0,
            },
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert response.success, f"Wire broken: {response.error}"
        assert response.error is None

    @pytest.mark.asyncio
    async def test_aspirate_with_explicit_flow_rates_list_succeeds(self, lh_registry: DeviceRegistry):
        executor = CommandExecutor(lh_registry)
        cmd = CommandMessage(
            command_id="test_asp_2",
            device_name="test_lh",
            command="aspirate",
            params={
                "aspirations": [
                    {"labware": "src_plate", "positions": ["A1", "B1", "C1"], "volumes": [50.0, 50.0, 50.0]},
                ],
                "flow_rates": [100.0, 100.0, 100.0],
            },
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert response.success, f"Wire broken: {response.error}"

    @pytest.mark.asyncio
    async def test_aspirate_with_both_flow_rate_and_flow_rates_raises(self, lh_registry: DeviceRegistry):
        """Conflict detection: caller bug surfaces as ParameterError, not silent drop."""
        executor = CommandExecutor(lh_registry)
        cmd = CommandMessage(
            command_id="test_asp_3",
            device_name="test_lh",
            command="aspirate",
            params={
                "aspirations": [
                    {"labware": "src_plate", "positions": ["A1"], "volumes": [50.0]},
                ],
                "flow_rate": 100.0,
                "flow_rates": [100.0],
            },
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert not response.success
        assert response.error_type == "ParameterError"
        assert "flow_rate" in (response.error or "").lower()

    @pytest.mark.asyncio
    async def test_aspirate_with_unknown_field_raises(self, lh_registry: DeviceRegistry):
        """Silent-drop regression guard: unknown fields rejected, not silently dropped."""
        executor = CommandExecutor(lh_registry)
        cmd = CommandMessage(
            command_id="test_asp_4",
            device_name="test_lh",
            command="aspirate",
            params={
                "aspirations": [
                    {"labware": "src_plate", "positions": ["A1"], "volumes": [50.0]},
                ],
                "wibble": 42,
            },
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert not response.success
        assert response.error_type == "ParameterError"
        assert "wibble" in (response.error or "")

    @pytest.mark.asyncio
    async def test_dispense_with_dict_params_succeeds(self, lh_registry: DeviceRegistry):
        executor = CommandExecutor(lh_registry)
        cmd = CommandMessage(
            command_id="test_disp_1",
            device_name="test_lh",
            command="dispense",
            params={
                "dispenses": [
                    {"labware": "dest_plate", "positions": ["A1"], "volumes": [50.0]},
                ],
            },
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert response.success

    @pytest.mark.asyncio
    async def test_aspirate_two_plates_succeeds(self, lh_registry: DeviceRegistry):
        """Regression: a single physical aspirate spanning two plates
        must reach the driver as two slices in ``aspirations``."""
        executor = CommandExecutor(lh_registry)
        cmd = CommandMessage(
            command_id="test_asp_multi",
            device_name="test_lh",
            command="aspirate",
            params={
                "aspirations": [
                    {"labware": "plate_a", "positions": ["A1", "B1"], "volumes": [50.0, 50.0]},
                    {"labware": "plate_b", "positions": ["A1"], "volumes": [25.0]},
                ],
            },
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert response.success, f"Multi-plate wire broken: {response.error}"

    @pytest.mark.asyncio
    async def test_pick_up_tips_two_racks_succeeds(self, lh_registry: DeviceRegistry):
        """Regression: a single physical pickup spanning two racks
        must reach the driver as two slices in ``picks``."""
        executor = CommandExecutor(lh_registry)
        cmd = CommandMessage(
            command_id="test_pickup_multi",
            device_name="test_lh",
            command="pick_up_tips",
            params={
                "picks": [
                    {"tip_rack": "rack_a", "positions": ["A1", "B1"]},
                    {"tip_rack": "rack_b", "positions": ["C1", "D1"]},
                ],
            },
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert response.success, f"Multi-rack pickup wire broken: {response.error}"

    @pytest.mark.asyncio
    async def test_configure_deck_with_flat_params_succeeds(self, lh_registry: DeviceRegistry):
        """configure_deck sends DeckLayoutConfig fields flat as params; the executor
        wraps them into a DeckLayoutConfig instance and passes under the `config` kwarg."""
        executor = CommandExecutor(lh_registry)
        cmd = CommandMessage(
            command_id="test_cfg_1",
            device_name="test_lh",
            command="configure_deck",
            params={
                "deck_type": "STARLet",
                "resources": [
                    {"name": "tip_carrier_1", "catalog_ref": "TIP_CAR_480_A00", "rail": 1},
                ],
            },
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert response.success, f"configure_deck wire broken: {response.error}"

    @pytest.mark.asyncio
    async def test_pick_up_tips_with_dict_params_succeeds(self, lh_registry: DeviceRegistry):
        executor = CommandExecutor(lh_registry)
        cmd = CommandMessage(
            command_id="test_tips_1",
            device_name="test_lh",
            command="pick_up_tips",
            params={
                "picks": [{"tip_rack": "tips_01", "positions": ["A1"]}],
            },
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert response.success

    @pytest.mark.asyncio
    async def test_drop_tips_with_dict_params_succeeds(self, lh_registry: DeviceRegistry):
        executor = CommandExecutor(lh_registry)
        cmd = CommandMessage(
            command_id="test_drop_1",
            device_name="test_lh",
            command="drop_tips",
            params={"to_waste": True},
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert response.success

    @pytest.mark.asyncio
    async def test_mix_carries_its_rate_on_the_resolved_record(self, lh_registry: DeviceRegistry):
        """A mix has no scalar flow_rate of its own: how it is pipetted comes from
        the resolved parameters, the same as an aspirate or a dispense. Only the
        cycle count and volume are the mix's own."""
        executor = CommandExecutor(lh_registry)
        cmd = CommandMessage(
            command_id="test_mix_1",
            device_name="test_lh",
            command="mix",
            params={
                "labware": "src_plate",
                "positions": ["A1"],
                "volume": 30.0,
                "repetitions": 3,
                "parameters": {"flow_rate": 50.0},
            },
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert response.success

        wrapped = executor._deserialize_params(
            "mix", cmd.params, lh_registry.get_driver("test_lh")
        )
        assert wrapped["request"].parameters.flow_rate == 50.0
        assert wrapped["request"].repetitions == 3

    @pytest.mark.asyncio
    async def test_mix_refuses_a_scalar_flow_rate_at_the_boundary(self, lh_registry: DeviceRegistry):
        """The field moved onto the record, so a caller still sending it gets a
        refusal rather than a silently-ignored rate."""
        executor = CommandExecutor(lh_registry)
        cmd = CommandMessage(
            command_id="test_mix_2",
            device_name="test_lh",
            command="mix",
            params={
                "labware": "src_plate",
                "positions": ["A1"],
                "volume": 30.0,
                "repetitions": 3,
                "flow_rate": 50.0,
            },
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert not response.success
        assert "flow_rate" in (response.error or "")

    @pytest.mark.asyncio
    async def test_aspirate96_with_scalar_flow_rate_succeeds(self, lh_registry: DeviceRegistry):
        """Aspirate96Request has scalar flow_rate field; pass through unchanged."""
        executor = CommandExecutor(lh_registry)
        cmd = CommandMessage(
            command_id="test_a96_1",
            device_name="test_lh",
            command="aspirate96",
            params={
                "labware": "src_plate",
                "volume": 50.0,
                "flow_rate": 100.0,
            },
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert response.success

    @pytest.mark.asyncio
    async def test_aspirate_returns_labware_state_in_result(self, lh_registry: DeviceRegistry):
        """Driver LabwareStateResponse round-trips into ResponseMessage.result via model_dump."""
        executor = CommandExecutor(lh_registry)
        cmd = CommandMessage(
            command_id="test_state_1",
            device_name="test_lh",
            command="aspirate",
            params={
                "aspirations": [
                    {"labware": "src_plate", "positions": ["A1"], "volumes": [50.0]},
                ],
            },
            effective_mode="LIVE",
        )
        response = await executor.execute(cmd)
        assert response.success
        assert response.result is not None
        # Pydantic model serialized via model_dump -> dict with success + labware_state.
        assert isinstance(response.result, dict)
        assert "labware_state" in response.result
        assert "success" in response.result
