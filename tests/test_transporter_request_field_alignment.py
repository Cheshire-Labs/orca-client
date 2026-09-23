"""Walk-style alignment guard between transporter wire callers and cheshire-drivers Pydantic models.

Mirrors `test_lh_request_field_alignment.py` for the transporter category.
Every wire-side caller of a transporter command must align its parameter
names with the corresponding cheshire-drivers Pydantic Request model. If a
caller adds a field the model does not declare, `_StrictModel`'s
`extra='forbid'` raises at runtime, but this unit test catches the
divergence at CI time, not at first runtime call.
"""

import pytest
from pydantic import BaseModel

from cheshire_drivers.transporter_request_validation import (
    TRANSPORTER_REQUEST_MODELS,
)


@pytest.mark.parametrize(
    "command_name,binding", list(TRANSPORTER_REQUEST_MODELS.items())
)
def test_executor_transporter_request_registry_kwarg_name_is_known(
    command_name: str, binding: tuple[str, type[BaseModel]]
) -> None:
    """Every entry maps to (kwarg_name, RequestModel) where kwarg_name is the
    kwarg the cheshire-drivers method expects. The transporter category uses
    `request` uniformly."""
    kwarg_name, model_cls = binding
    assert kwarg_name == "request", (
        f"Unexpected kwarg name {kwarg_name!r} for {command_name}"
    )
    assert issubclass(model_cls, BaseModel), (
        f"Model class for {command_name} must be a Pydantic BaseModel"
    )


@pytest.mark.parametrize(
    "command_name,binding", list(TRANSPORTER_REQUEST_MODELS.items())
)
def test_transporter_request_model_is_strict_mode(
    command_name: str, binding: tuple[str, type[BaseModel]]
) -> None:
    """Every Pydantic Request model must reject unknown fields."""
    _, model_cls = binding
    assert model_cls.model_config.get("extra") == "forbid", (
        f"{model_cls.__name__} (for {command_name}) is not strict-mode "
        f"(extra='forbid'). Inherit from _StrictModel or set "
        f"model_config = ConfigDict(extra='forbid')."
    )


def test_executor_transporter_request_registry_covers_known_commands() -> None:
    """Sanity check: every well-known transporter command appears in
    TRANSPORTER_REQUEST_MODELS. If a future change adds a command on
    ITransporterDriver but forgets to register it here, the orca-client
    executor will silently fall through to the generic params path and the
    driver call will TypeError on the wire. This test fails first.
    """
    expected = {
        "initialize", "home", "move_to_safe",
        "open_gripper", "close_gripper", "halt",
        "pick_at_coords", "place_at_coords", "move_to_coords",
        "move_single_axis", "move_single_axis_relative",
        "set_free_mode", "set_speed",
        "get_joint_position", "get_cartesian_position", "get_speed",
    }
    covered = set(TRANSPORTER_REQUEST_MODELS.keys())
    missing = expected - covered
    assert missing == set(), (
        f"Transporter commands missing from TRANSPORTER_REQUEST_MODELS: {missing}."
    )
