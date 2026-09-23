"""Executor wire-shape tests for `liquid_probe`.

Liquid sensing rides its own interface because it is hardware: a head either
carries a pressure sensor or it does not. The executor wraps the flat wire dict
into the driver's Request only for a driver that advertises ILiquidProbe, so a
liquid handler without the sensor can never dispatch the command as a Request
it has no method for.
"""

import pytest

from cheshire_drivers.liquid_handler_models import LiquidProbeRequest
from orca_client.devices import DeviceRegistry
from orca_client.executor import CommandExecutor


class _StubProbingDriver:
    """A Flex-like liquid handler that can find the liquid surface."""
    interfaces = frozenset({"ILiquidHandler", "ILiquidProbe"})


class _StubBlindDriver:
    """A liquid handler with no liquid-level sensing (an OT-2-style backend)."""
    interfaces = frozenset({"ILiquidHandler"})


def _executor() -> CommandExecutor:
    return CommandExecutor(DeviceRegistry())


def test_liquid_probe_wraps_into_request_model() -> None:
    out = _executor()._deserialize_params(
        "liquid_probe",
        {"labware": "plate_1", "positions": ["B3"]},
        _StubProbingDriver(),
    )
    assert isinstance(out["request"], LiquidProbeRequest)
    assert out["request"].labware == "plate_1"
    assert out["request"].positions == ["B3"]


def test_a_container_probe_carries_its_channels() -> None:
    out = _executor()._deserialize_params(
        "liquid_probe",
        {"labware": "trough_1", "use_channels": [0, 1]},
        _StubProbingDriver(),
    )
    assert isinstance(out["request"], LiquidProbeRequest)
    assert out["request"].positions is None
    assert out["request"].use_channels == [0, 1]


def test_gate_requires_the_probe_interface() -> None:
    """A liquid handler with no sensor does not wrap the command; the flat dict
    passes through untouched rather than becoming a Request it cannot serve."""
    params = {"labware": "plate_1", "positions": ["B3"]}
    out = _executor()._deserialize_params("liquid_probe", params, _StubBlindDriver())
    assert out == params
    assert "request" not in out


def test_unknown_probe_field_rejected() -> None:
    with pytest.raises(ValueError):
        _executor()._deserialize_params(
            "liquid_probe",
            {"labware": "plate_1", "positions": ["B3"], "bogus": 1},
            _StubProbingDriver(),
        )
