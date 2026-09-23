"""Tests for DeviceConnectInfo wire shape + DeviceRegistry connect_info_list.

Verifies the handshake metadata:
- DeviceConnectInfo rejects unknown fields (extra='forbid')
- DeviceConnectInfo serializes/deserializes through Pydantic round-trip
- DeviceRegistry.connect_info_list reads driver ClassVars at handshake time
"""

import pytest
from pydantic import ValidationError

from cheshire_drivers import sims, plr_wrappers
from orca_client.devices.registry import DeviceRegistry
from cheshire_drivers.gateway_protocol import (
    ConnectMessage,
    DeviceConnectInfo,
    PROTOCOL_VERSION,
)


class TestDeviceConnectInfoSchema:
    def test_minimal_valid_construction(self) -> None:
        info = DeviceConnectInfo(
            name="shaker_1",
            type="shaker",
            interfaces=frozenset({"IShaker"}),
        )
        assert info.name == "shaker_1"
        assert info.interfaces == frozenset({"IShaker"})
        assert info.capabilities == frozenset()
        assert info.provides_state is False

    def test_full_construction_with_all_fields(self) -> None:
        info = DeviceConnectInfo(
            name="lh_1",
            type="liquid_handler",
            interfaces=frozenset({"ILiquidHandler", "IProtocolRunner"}),
            capabilities=frozenset({"vendor_specific_thing"}),
            provides_state=True,
        )
        assert "ILiquidHandler" in info.interfaces
        assert "vendor_specific_thing" in info.capabilities
        assert info.provides_state is True

    def test_unknown_field_is_rejected(self) -> None:
        # extra='forbid' on the model — bug-finding for wire drift
        with pytest.raises(ValidationError):
            DeviceConnectInfo(
                name="x",
                type="shaker",
                interfaces=frozenset({"IShaker"}),
                some_made_up_field="oops",  # type: ignore[call-arg]
            )

    def test_missing_required_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DeviceConnectInfo(  # type: ignore[call-arg]
                # name missing
                type="shaker",
                interfaces=frozenset({"IShaker"}),
            )

    def test_round_trip_through_model_dump_and_validate(self) -> None:
        original = DeviceConnectInfo(
            name="lh_1",
            type="liquid_handler",
            interfaces=frozenset({"ILiquidHandler", "IProtocolRunner"}),
            capabilities=frozenset({"set_acceleration"}),
            provides_state=True,
        )
        wire = original.model_dump(mode="json")
        rehydrated = DeviceConnectInfo.model_validate(wire)
        assert rehydrated == original


class TestConnectMessageWithDeviceConnectInfo:
    def test_connect_message_carries_typed_devices_list(self) -> None:
        msg = ConnectMessage(
            protocol_version=PROTOCOL_VERSION,
            site="boston",
            lab="molbio",
            workcell=None,
            devices=[
                DeviceConnectInfo(
                    name="shaker_1",
                    type="shaker",
                    interfaces=frozenset({"IShaker"}),
                ),
                DeviceConnectInfo(
                    name="lh_1",
                    type="liquid_handler",
                    interfaces=frozenset({"ILiquidHandler", "IProtocolRunner"}),
                    provides_state=True,
                ),
            ],
        )
        assert len(msg.devices) == 2
        assert msg.devices[0].interfaces == frozenset({"IShaker"})
        assert msg.devices[1].provides_state is True

    def test_connect_message_round_trip_via_envelope_payload(self) -> None:
        # The websocket_client wraps ConnectMessage in an envelope via
        # `model_dump()`; the receiver reconstructs via ConnectMessage(**payload).
        # This test confirms that round-trip preserves the typed device list.
        original = ConnectMessage(
            protocol_version=PROTOCOL_VERSION,
            site="boston",
            lab="molbio",
            workcell="wc1",
            devices=[
                DeviceConnectInfo(
                    name="lh_1",
                    type="liquid_handler",
                    interfaces=frozenset({"ILiquidHandler", "IProtocolRunner"}),
                    provides_state=True,
                ),
            ],
        )
        wire = original.model_dump(mode="json")
        # frozenset serializes as list/array; ConnectMessage should still parse
        rehydrated = ConnectMessage.model_validate(wire)
        assert rehydrated.devices[0].interfaces == frozenset(
            {"ILiquidHandler", "IProtocolRunner"}
        )
        assert rehydrated.devices[0].provides_state is True


class TestRegistryConnectInfoList:
    def test_connect_info_reads_metadata_from_registered_sim_shaker(self) -> None:
        registry = DeviceRegistry()
        sim_shaker = sims.SimShakerDriver(name="Sim Shaker")
        registry.register(name="shaker_1", device_type="shaker", driver=sim_shaker)

        info_list = registry.connect_info_list()
        assert len(info_list) == 1
        info = info_list[0]
        assert info.name == "shaker_1"
        assert info.type == "shaker"
        assert "IShaker" in info.interfaces
        # The whole lifecycle surface is on the driver contract now, so none of
        # it is a vendor extra: auto-derived capabilities are extras only, and
        # anything an interface declares is subtracted. This sim shaker ends up
        # advertising no extras at all. The verbs stay reachable through the
        # interface, which is where `methods` shows them.
        for member in ("connect", "disconnect", "is_connected"):
            assert member not in info.capabilities
            assert member in info.methods
        assert info.provides_state is False
        # Methods dict carries the full callable surface
        assert "shake" in info.methods
        assert info.methods["shake"].kind == "method"
        assert "is_initialized" in info.methods
        assert info.methods["is_initialized"].kind == "property"

    def test_sim_lh_advertises_provides_state_false(self) -> None:
        registry = DeviceRegistry()
        sim_lh = sims.SimLiquidHandlerDriver(name="Sim LH")
        registry.register(name="lh_1", device_type="liquid_handler", driver=sim_lh)

        info_list = registry.connect_info_list()
        info = info_list[0]
        # SimLiquidHandlerDriver inherits provides_state=False from the interface.
        assert info.provides_state is False
        assert "ILiquidHandler" in info.interfaces
        # After the ILiquidHandlerDriver / IProtocolRunnerDriver split, the
        # sim LH advertises only ILiquidHandler -- protocol-runner is a
        # separate driver class.
        assert "IProtocolRunner" not in info.interfaces
        aspirate_mi = info.methods.get("aspirate")
        assert aspirate_mi is not None
        assert aspirate_mi.kind == "method"
        assert "request" in aspirate_mi.params

    def test_plr_lh_advertises_provides_state_true(self) -> None:
        # PLRLiquidHandlerWrapper overrides provides_state to True; the wire
        # advertisement must reflect that so the server can trust the
        # driver-reported state instead of maintaining its own shadow copy.
        registry = DeviceRegistry()
        wrapper = plr_wrappers.PLRLiquidHandlerWrapper.__new__(
            plr_wrappers.PLRLiquidHandlerWrapper
        )
        registry.register(name="lh_plr", device_type="liquid_handler", driver=wrapper)

        info_list = registry.connect_info_list()
        info = info_list[0]
        assert info.provides_state is True
        assert "ILiquidHandler" in info.interfaces

    def test_connect_info_auto_derives_concrete_driver_extras(self) -> None:
        registry = DeviceRegistry()
        # PLRCentrifugeBackendWrapper.stop and .set_acceleration are NOT on
        # ICentrifugeDriver. derive_capabilities must surface them.
        fake_wrapper = plr_wrappers.PLRCentrifugeBackendWrapper.__new__(
            plr_wrappers.PLRCentrifugeBackendWrapper
        )
        fake_wrapper._is_initialized = False  # type: ignore[attr-defined]
        registry.register(name="centrifuge_1", device_type="centrifuge", driver=fake_wrapper)

        info_list = registry.connect_info_list()
        info = info_list[0]
        assert "ICentrifuge" in info.interfaces
        assert "stop" in info.capabilities
        assert "set_acceleration" in info.capabilities
        # Methods dict surfaces the extras with their schemas
        assert "set_acceleration" in info.methods
        sa_mi = info.methods["set_acceleration"]
        assert sa_mi.kind == "method"
        assert "acceleration" in sa_mi.params
