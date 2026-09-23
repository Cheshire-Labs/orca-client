"""Every failure this agent reports says whether it touched the instrument.

The control plane latches a device fault on a failed command, and a faulted
device is blocked for every caller until an operator clears it. It decided
whether to latch from whether the send returned, which is true of every reply
this agent makes -- including the ones where it refused a command outright and
no driver ran. A misspelled command name could fault a healthy instrument.

So the reply carries what actually happened to the machine. Most of what this
agent refuses never reaches a driver at all; a driver's own failure carries
what the driver said.
"""

import pytest

from cheshire_drivers.driver_errors import (
    DriverRefusedError,
    InstrumentOutcome,
)
from cheshire_drivers.gateway_protocol import CommandMessage
from orca_client.devices import DeviceRegistry
from orca_client.executor import CommandExecutor


def _cmd(command: str, params: dict | None = None, device: str = "test_shaker"):
    return CommandMessage(
        command_id="c1", device_name=device, command=command,
        params=params or {}, effective_mode="LIVE",
    )


@pytest.mark.asyncio
async def test_a_successful_command_classifies_nothing(
    device_registry: DeviceRegistry,
) -> None:
    response = await CommandExecutor(device_registry).execute(
        _cmd("shake", {"speed": 300.0, "duration": 5.0}),
    )

    assert response.success is True
    assert response.instrument_outcome is None


@pytest.mark.asyncio
@pytest.mark.parametrize("command, device", [
    ("shake", "nonexistent"),
    ("command_no_driver_has", "test_shaker"),
    ("_private", "test_shaker"),
])
async def test_a_command_this_agent_will_never_run_is_a_rejection(
    device_registry: DeviceRegistry, command: str, device: str,
) -> None:
    """The bug this closes, and half of a second one.

    These reply over the same wire as a real failure, and the control plane
    could not tell them apart. They are REJECTED and not REFUSED because
    waiting changes nothing: a caller that treats them as "busy, ask again"
    spends its whole patience budget on a command that can never work.
    """
    response = await CommandExecutor(device_registry).execute(
        _cmd(command, device=device),
    )

    assert response.success is False
    assert response.instrument_outcome is InstrumentOutcome.REJECTED
    assert response.instrument_outcome.moved_nothing is True
    assert response.instrument_outcome.worth_asking_again is False


@pytest.mark.asyncio
async def test_a_payload_that_will_not_deserialize_is_a_refusal(
    device_registry: DeviceRegistry,
) -> None:
    """Wrapping a payload into a Request touches no hardware. It used to share
    a catch with the driver call, so the two could not be told apart."""
    response = await CommandExecutor(device_registry).execute(
        _cmd("shake", {"speed": "not a number", "duration": 5.0}),
    )

    assert response.success is False
    assert response.error_type == "ParameterError"
    assert response.instrument_outcome is InstrumentOutcome.REJECTED


@pytest.mark.asyncio
async def test_a_driver_failure_carries_what_the_driver_said(
    device_registry: DeviceRegistry, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A driver that ran and stopped part-way. The default has to be FAILED so
    an undeclared failure is never mistaken for a refusal."""
    driver = device_registry.get_driver("test_shaker")
    assert driver is not None

    async def _stalls(**_: object) -> None:
        raise RuntimeError("Stall or Collision Detected")

    monkeypatch.setattr(driver, "shake", _stalls)

    response = await CommandExecutor(device_registry).execute(
        _cmd("shake", {"speed": 300.0, "duration": 5.0}),
    )

    assert response.success is False
    assert response.instrument_outcome is InstrumentOutcome.FAILED


@pytest.mark.asyncio
async def test_a_driver_refusal_reaches_the_wire_as_a_refusal(
    device_registry: DeviceRegistry, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 2026-09-02 case: a handler refusing because a transfer is under way.
    Correct, actuated nothing, and it latched a device fault."""
    driver = device_registry.get_driver("test_shaker")
    assert driver is not None

    async def _busy(**_: object) -> None:
        raise DriverRefusedError("still holds tips, so a transfer is under way")

    monkeypatch.setattr(driver, "shake", _busy)

    response = await CommandExecutor(device_registry).execute(
        _cmd("shake", {"speed": 300.0, "duration": 5.0}),
    )

    assert response.success is False
    assert response.instrument_outcome is InstrumentOutcome.REFUSED


@pytest.mark.asyncio
async def test_a_driver_failure_keeps_the_detail_the_driver_had(
    device_registry: DeviceRegistry, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`explain_driver_error` folds a driver's structured detail into the text.
    It shipped with no caller, so the detail was still dropped here."""
    driver = device_registry.get_driver("test_shaker")
    assert driver is not None

    async def _chained(**_: object) -> None:
        try:
            raise ValueError("axis Z jammed at 41mm")
        except ValueError as inner:
            raise RuntimeError("Stall or Collision Detected") from inner

    monkeypatch.setattr(driver, "shake", _chained)

    response = await CommandExecutor(device_registry).execute(
        _cmd("shake", {"speed": 300.0, "duration": 5.0}),
    )

    assert response.error is not None
    assert "Stall or Collision Detected" in response.error


@pytest.mark.asyncio
async def test_a_bad_keyword_is_a_rejection_not_a_driver_failure(
    device_registry: DeviceRegistry, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`method(**params)` raises the binding TypeError at the call and the
    coroutine body never starts, so nothing was actuated. It used to land in
    the handler for a driver's own failure and fault a healthy instrument --
    reachable on every vendor extra, where params pass through unvalidated."""
    driver = device_registry.get_driver("test_shaker")
    assert driver is not None
    ran = False

    async def _records_that_it_ran(*, speed: float, duration: float) -> None:
        nonlocal ran
        ran = True

    monkeypatch.setattr(driver, "shake", _records_that_it_ran)

    response = await CommandExecutor(device_registry).execute(
        _cmd("shake", {"speed": 1.0, "duration": 1.0, "not_a_kwarg": 2}),
    )

    assert ran is False
    assert response.success is False
    assert response.instrument_outcome is InstrumentOutcome.REJECTED


@pytest.mark.asyncio
async def test_a_command_that_succeeded_never_faults_its_device(
    device_registry: DeviceRegistry, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The driver ran and finished. If we cannot project its answer that is
    our shape being wrong, not the instrument being left somewhere."""
    executor = CommandExecutor(device_registry)

    def _cannot_project(*_: object, **__: object) -> object:
        raise ValueError("no response model for that shape")

    monkeypatch.setattr(executor, "_wrap_result", _cannot_project)

    response = await executor.execute(
        _cmd("shake", {"speed": 300.0, "duration": 5.0}),
    )

    assert response.success is False
    assert response.instrument_outcome is not None
    assert response.instrument_outcome.moved_nothing is True


@pytest.mark.asyncio
async def test_a_property_read_that_raises_is_classified_not_escaped(
    device_registry: DeviceRegistry, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reading `is_initialized` runs the getter, so the read has to happen
    where a failure can be classified. It used to happen above that handler,
    and the exception left `execute` entirely; the reply that came back from
    the socket layer carried no outcome, which the control plane reads as a
    failure and faults a device nothing had touched."""
    driver = device_registry.get_driver("test_shaker")

    def _boom(_self: object) -> bool:
        raise RuntimeError("the link dropped mid-read")

    monkeypatch.setattr(type(driver), "is_initialized", property(_boom))

    response = await CommandExecutor(device_registry).execute(
        _cmd("is_initialized"),
    )

    assert response.success is False
    assert response.instrument_outcome is not None
    assert response.instrument_outcome.moved_nothing is True
