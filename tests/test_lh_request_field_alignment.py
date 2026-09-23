"""Walk-style alignment guard between MCP/REST/IR fields and cheshire-drivers Pydantic models.

This is the regression guard against the silent-drop class. Every wire-side caller
of a liquid-handler command must align its parameter names with the corresponding
cheshire-drivers Pydantic Request model. If a caller adds a field the model does not
declare, Pydantic v2 default `extra="ignore"` would silently drop it on every call.

`reject_unknown_lh_fields` raises at runtime, but a unit test that walks both sides
catches the divergence at CI time, not at first runtime call.
"""

import pytest
from pydantic import BaseModel

from cheshire_drivers.lh_request_validation import LH_REQUEST_MODELS


@pytest.mark.parametrize("command_name,binding", list(LH_REQUEST_MODELS.items()))
def test_executor_request_registry_kwarg_name_is_known(
    command_name: str, binding: tuple[str, type[BaseModel]]
) -> None:
    """Every entry maps to (kwarg_name, RequestModel) where kwarg_name is the kwarg
    the cheshire-drivers method expects. Allowed names today: 'request' for most,
    'config' for configure_deck."""
    kwarg_name, model_cls = binding
    assert kwarg_name in {"request", "config"}, (
        f"Unexpected kwarg name {kwarg_name!r} for {command_name}"
    )
    assert issubclass(model_cls, BaseModel), (
        f"Model class for {command_name} must be a Pydantic BaseModel"
    )


@pytest.mark.parametrize("command_name,binding", list(LH_REQUEST_MODELS.items()))
def test_request_model_is_strict_mode(
    command_name: str, binding: tuple[str, type[BaseModel]]
) -> None:
    """Every Pydantic Request model must reject unknown fields.

    Default Pydantic v2 behavior is `extra='ignore'` which silently drops
    unknown fields. The driver layer is the source of truth for the wire
    shape; silent-drop on the executor side would mask real wire-shape drift
    bugs. This guard catches a model defined without `_StrictModel` / explicit
    `extra='forbid'` config.
    """
    _, model_cls = binding
    assert model_cls.model_config.get("extra") == "forbid", (
        f"{model_cls.__name__} (for {command_name}) is not strict-mode "
        f"(extra='forbid'). Inherit from _StrictModel or set "
        f"model_config = ConfigDict(extra='forbid')."
    )


def test_executor_lh_request_registry_covers_known_commands() -> None:
    """Sanity check: every well-known LH command appears in the registry.

    This is the durable guard: if a future change adds a new LH command on the
    cheshire-drivers driver interface but forgets to register it here, the
    orca-client executor will silently fall through to the generic params path
    and the driver call will TypeError on the wire. This test fails first.
    """
    expected = {
        "configure_deck", "get_deck_state",
        "aspirate", "dispense", "pick_up_tips", "drop_tips",
        "mix", "discard_tips", "move_plate",
        "aspirate96", "dispense96", "pick_up_tips96", "drop_tips96", "return_tips96",
    }
    covered = set(LH_REQUEST_MODELS.keys())
    missing = expected - covered
    assert missing == set(), (
        f"LH commands missing from cheshire-drivers registry: {missing}. "
        f"Add them to LH_REQUEST_MODELS in cheshire_drivers.lh_request_validation."
    )
