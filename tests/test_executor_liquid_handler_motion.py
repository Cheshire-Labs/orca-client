"""Executor wire-shape tests for the liquid-handler manual-motion commands.

The motion commands (move_channel_to, grip_with_force, ...) span six facet
interfaces but share one request registry. The executor must wrap a flat wire
dict into the driver's Request kwarg only when the driver advertises a motion
facet, and reject unknown fields. There is no motion-capable sim driver (the
real drivers wrap PLR backends), so these drive `_deserialize_params` directly
with stub drivers carrying the relevant `interfaces` ClassVar.
"""

import pytest

from cheshire_drivers.gripper_models import GripWithForceRequest, SetJawWidthRequest
from cheshire_drivers.pipette_motion_models import MoveChannelToRequest
from orca_client.devices import DeviceRegistry
from orca_client.executor import CommandExecutor


class _StubMotionDriver:
    """A STAR-like liquid handler advertising the full motion surface."""
    interfaces = frozenset({
        "ILiquidHandler", "IPipetteMotion", "IGripperMotion",
        "IWidthGripperJaw", "IForceGripperJaw", "IGripperRotation",
    })


class _StubPlainLHDriver:
    """A liquid handler with no manual-motion facet (a Tecan-style backend)."""
    interfaces = frozenset({"ILiquidHandler"})


def _executor() -> CommandExecutor:
    return CommandExecutor(DeviceRegistry())


def test_move_channel_to_wraps_into_request_model() -> None:
    out = _executor()._deserialize_params(
        "move_channel_to",
        {"channel": 0, "z": 5.0, "z_reference": "tip_end"},
        _StubMotionDriver(),
    )
    assert isinstance(out["request"], MoveChannelToRequest)
    assert out["request"].channel == 0
    assert out["request"].z == 5.0


def test_set_jaw_width_wraps_into_request_model() -> None:
    out = _executor()._deserialize_params(
        "set_jaw_width", {"width": 80.0}, _StubMotionDriver(),
    )
    assert isinstance(out["request"], SetJawWidthRequest)
    assert out["request"].width == 80.0


def test_grip_with_force_omitted_force_wraps_to_default() -> None:
    out = _executor()._deserialize_params(
        "grip_with_force", {}, _StubMotionDriver(),
    )
    assert isinstance(out["request"], GripWithForceRequest)
    assert out["request"].force is None


def test_gate_requires_a_motion_facet() -> None:
    """A plain liquid handler does not wrap a motion command; the flat dict
    passes through untouched so it can never dispatch as a bogus Request."""
    params = {"channel": 0, "z": 5.0}
    out = _executor()._deserialize_params(
        "move_channel_to", params, _StubPlainLHDriver(),
    )
    assert out == params
    assert "request" not in out


def test_unknown_motion_field_rejected() -> None:
    with pytest.raises(ValueError):
        _executor()._deserialize_params(
            "move_channel_to",
            {"channel": 0, "z": 5.0, "bogus": 1},
            _StubMotionDriver(),
        )
