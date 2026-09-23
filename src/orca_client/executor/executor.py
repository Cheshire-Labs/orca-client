"""Command executor for routing string-based commands to device drivers.

# CLIENT-SPECIFIC: Uses string commands instead of typed actions
"""

import asyncio
import contextlib
import logging
from typing import Any, Dict, Optional

from pydantic import BaseModel, ValidationError

from cheshire_drivers.response_lookup import WIRE_READABLE_PROPERTIES
from cheshire_drivers.centrifuge_request_validation import (
    CENTRIFUGE_REQUEST_MODELS,
    wrap_centrifuge_payload,
)
from cheshire_drivers.command_responses import EmptyCommandResponse
from cheshire_drivers.driver_errors import (
    InstrumentOutcome,
    explain_driver_error,
    outcome_of,
)
from cheshire_drivers.delidder_request_validation import (
    DELIDDER_REQUEST_MODELS,
    wrap_delidder_payload,
)
from cheshire_drivers.lh_motion_request_validation import (
    LH_MOTION_REQUEST_MODELS,
    wrap_lh_motion_payload,
)
from cheshire_drivers.lh_request_validation import (
    LH_REQUEST_MODELS,
    LIQUID_PROBE_REQUEST_MODELS,
    wrap_lh_payload,
    wrap_liquid_probe_payload,
)
from cheshire_drivers.protocol_runner_request_validation import (
    PROTOCOL_RUNNER_REQUEST_MODELS,
    wrap_protocol_runner_payload,
)
from cheshire_drivers.reader_request_validation import (
    READER_REQUEST_MODELS,
    wrap_reader_payload,
)
from cheshire_drivers.response_lookup import lookup_response_model
from cheshire_drivers.sealer_request_validation import (
    SEALER_REQUEST_MODELS,
    wrap_sealer_payload,
)
from cheshire_drivers.shaker_request_validation import (
    SHAKER_REQUEST_MODELS,
    wrap_shaker_payload,
)
from cheshire_drivers.thermocycler_request_validation import (
    THERMOCYCLER_REQUEST_MODELS,
    wrap_thermocycler_payload,
)
from cheshire_drivers.transporter_request_validation import (
    TRANSPORTER_REQUEST_MODELS,
    wrap_transporter_payload,
)

from cheshire_drivers.gateway_protocol import CommandMessage, ResponseMessage
from ..devices import DeviceRegistry
from .idempotency_cache import CommandIdempotencyCache

logger = logging.getLogger("orca_client.executor")

# The manual-motion facets a liquid handler may advertise. Motion commands live
# in one registry spanning them all, so the gate fires when any is present.
_LH_MOTION_INTERFACES = frozenset({
    "IGantryParking",
    "IPipetteMotion",
    "IGripperMotion",
    "IGripperPosition",
    "IForceGripperJaw",
    "IWidthGripperJaw",
    "IGripperRotation",
})

# Properties (sync, not async methods) - accessed directly without calling.
# Sourced from cheshire-drivers so this end and the cloud's capability gate
# cannot drift: the gate refused `is_initialized` for as long as it shipped
# while this list happily served it.
PROPERTY_QUERIES = WIRE_READABLE_PROPERTIES


class CommandCancelled(Exception):
    """Raised inside the executor when an in-flight command is cancelled.

    Distinct from a bare ``asyncio.CancelledError`` (which means the handling
    task itself is being torn down, e.g. on shutdown): ``CommandCancelled``
    means the server asked us to stop this specific command, so the handler
    discards it and sends no response. The command's eventual driver result,
    if any, is dropped.
    """

    def __init__(self, command_id: str) -> None:
        self.command_id = command_id
        super().__init__(f"command {command_id} cancelled by server")


class CommandExecutor:
    """Executes string-based commands on devices with validation and locking."""

    def __init__(
        self,
        registry: DeviceRegistry,
        idempotency_cache: Optional[CommandIdempotencyCache] = None,
    ):
        """Initialize command executor.

        Args:
            registry: Device registry for driver lookup
            idempotency_cache: Optional shared cache for command_id
                de-duplication. The gateway resends commands with the
                same command_id after a transient WebSocket drop; the
                cache makes those resends safe (in-flight duplicates
                await the original; completed duplicates return the
                cached ResponseMessage). Defaults to a fresh cache
                with the cache's default TTL.

        Commands run with no client-side duration bound: the engine owns the
        timeout (a long-running command that overruns its expected duration is
        an operator decision, not a client-side failure). The server cancels a
        command it no longer wants via a cancel message, which cancels the
        in-flight task here.
        """
        self.registry = registry
        self._idempotency_cache = (
            idempotency_cache
            if idempotency_cache is not None
            else CommandIdempotencyCache()
        )
        # In-flight driver tasks keyed by command_id, so a server cancel can
        # stop the running command. ``_cancelled_ids`` distinguishes a
        # server-requested cancel from task teardown when the CancelledError
        # surfaces inside the driver await.
        self._inflight_tasks: Dict[str, asyncio.Task[Any]] = {}
        self._cancelled_ids: set[str] = set()

    async def cancel(self, command_id: str) -> bool:
        """Cancel a command. Returns True if a running driver task was
        interrupted, False otherwise.

        Always records the cancel as a tombstone, even when no driver task is
        in flight yet: a command can sit for minutes waiting on the device
        lock before its task registers, and a cancel arriving in that window
        must still suppress the command's response rather than being lost.
        ``execute`` re-checks the tombstone after acquiring the lock and after
        the driver finishes, so the cancelled command produces no response.
        """
        if not await self._idempotency_cache.is_inflight(command_id):
            # Nothing running under this id (finished, TTL-retained, or
            # unknown): no response to suppress, and tombstoning it would
            # leak since no command execution would ever clear it.
            return False
        self._cancelled_ids.add(command_id)
        task = self._inflight_tasks.get(command_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def execute(self, cmd: CommandMessage) -> ResponseMessage:
        """Execute a command and return response.

        De-duplicates on ``cmd.command_id`` via :class:`CommandIdempotencyCache`:
        in-flight duplicates await the original execution; completed duplicates
        return the cached ResponseMessage without re-invoking the driver.

        Args:
            cmd: Command message from platform

        Returns:
            Response message with result or error
        """
        future, is_new = await self._idempotency_cache.claim(cmd.command_id)
        if not is_new:
            logger.info(
                "Duplicate command_id=%r received; awaiting original execution "
                "(device=%r, command=%r). Driver NOT re-invoked.",
                cmd.command_id, cmd.device_name, cmd.command,
            )
            return await future

        try:
            response = await self._execute_uncached(cmd)
        except CommandCancelled as exc:
            # A server cancel is terminal: retain the entry for the TTL so a
            # gateway replay of this command_id is suppressed (re-raises the
            # cancellation) rather than re-running the cancelled command.
            await self._idempotency_cache.record_cancelled(cmd.command_id, exc)
            raise
        except BaseException as exc:
            # The original execute_uncached path itself raised (not a driver
            # error reshaped to a ResponseMessage, but something unexpected).
            # Propagate to any duplicate awaiter and re-raise.
            await self._idempotency_cache.fail(cmd.command_id, exc)
            raise

        await self._idempotency_cache.complete(cmd.command_id, response)
        return response

    async def _execute_uncached(self, cmd: CommandMessage) -> ResponseMessage:
        """Run the command without idempotency de-duplication. The public
        :meth:`execute` wraps this in a cache claim so callers benefit from
        gateway-resend safety automatically."""
        logger.info(
            f"Executing: {cmd.command} on {cmd.device_name} "
            f"(effective_mode={cmd.effective_mode})"
        )

        # Block private methods (security)
        if cmd.command.startswith('_'):
            logger.warning(f"Attempted to call private method: {cmd.command}")
            return self._error_response(
                cmd.command_id,
                f"Private methods are not allowed: {cmd.command}",
                "SecurityError"
            )

        # PURE_SIM is handled server-side and must not reach orca-client.
        # If one does, the server has a contract violation; refuse loudly so
        # the bug surfaces immediately rather than silently dispatching to
        # whatever driver happens to match.
        if cmd.effective_mode == "PURE_SIM":
            logger.error(
                "PURE_SIM command reached orca-client (device=%r, command=%r). "
                "Wire-protocol violation: PURE_SIM should be intercepted "
                "server-side and never sent over the wire.",
                cmd.device_name,
                cmd.command,
            )
            return self._error_response(
                cmd.command_id,
                (
                    f"PURE_SIM commands must not reach orca-client "
                    f"(device={cmd.device_name!r}, command={cmd.command!r})"
                ),
                "PureSimWireViolation",
            )

        # Get driver. DEVICE_SIM picks the paired in-process Sim* driver;
        # LIVE picks the configured backend (PLR / Venus / sim-by-config).
        if cmd.effective_mode == "DEVICE_SIM":
            driver = self.registry.get_sim_driver(cmd.device_name)
            if not driver:
                logger.error(
                    "Sim driver not available for device %r (type missing "
                    "from sim-driver map?)", cmd.device_name,
                )
                return self._error_response(
                    cmd.command_id,
                    (
                        f"Device {cmd.device_name} has no paired sim driver "
                        f"for DEVICE_SIM dispatch"
                    ),
                    "SimDriverNotFoundError",
                )
        else:
            driver = self.registry.get_driver(cmd.device_name)
            if not driver:
                logger.error(f"Device not found: {cmd.device_name}")
                return self._error_response(
                    cmd.command_id,
                    f"Device {cmd.device_name} not found",
                    "DeviceNotFoundError"
                )

        # A property IS its own read: `getattr` runs the getter, so the read
        # has to happen where a failing one can be classified.
        if cmd.command in PROPERTY_QUERIES:
            try:
                raw = getattr(driver, cmd.command)
            except AttributeError as e:
                return self._unsupported(cmd, e)
            except Exception as e:
                # Reading a property actuates nothing, so a read that raises
                # cannot have left the instrument part-way through anything.
                # A driver that says otherwise about its own error is believed.
                logger.error(f"Property access {cmd.command} failed: {e}", exc_info=True)
                declared = outcome_of(e)
                return self._error_response(
                    cmd.command_id,
                    explain_driver_error(e),
                    type(e).__name__,
                    declared if declared.moved_nothing else InstrumentOutcome.REJECTED,
                )
            logger.debug(f"Property {cmd.command} = {raw}")
            return ResponseMessage(
                command_id=cmd.command_id,
                success=True,
                result=self._wrap_result(cmd.command, raw, driver),
            )

        # A forwarding driver says WHY it cannot resolve a name (optional
        # hardware absent, not connected yet); that reason reaches the operator.
        try:
            member = getattr(driver, cmd.command)
        except AttributeError as e:
            return self._unsupported(cmd, e)

        # Verify method is async (for non-property commands)
        method = member
        if not asyncio.iscoroutinefunction(method):
            logger.error(f"Command {cmd.command} is not async on {cmd.device_name}")
            return self._error_response(
                cmd.command_id,
                f"Command {cmd.command} is not async",
                "NotAsyncError"
            )

        # Execute under the per-device lock with NO client-side duration bound:
        # the engine owns the timeout. The driver call runs as a tracked task so
        # a server cancel can stop it.
        async with driver.lock:  # type: ignore[attr-defined]
            try:
                if cmd.command_id in self._cancelled_ids:
                    # Cancelled while waiting for the device lock -- never start
                    # the driver; the server is no longer awaiting a response.
                    raise CommandCancelled(cmd.command_id)
                try:
                    params = self._deserialize_params(cmd.command, cmd.params, driver)
                except (ValueError, ValidationError, TypeError) as e:
                    # Its own catch, and not the driver's: wrapping a payload
                    # into a Request touches no hardware, so this is a refusal
                    # however the driver's own failures are classified.
                    logger.error(f"Invalid parameters for {cmd.command}: {e}")
                    return self._error_response(
                        cmd.command_id,
                        f"Invalid parameters: {e}",
                        "ParameterError",
                    )
                try:
                    call = method(**params)
                except TypeError as e:
                    # Binding the arguments, not running the body: a coroutine
                    # function raises this at the call and its body never
                    # starts. Outside this catch it read as the driver's own
                    # failure and faulted a device nothing had touched.
                    logger.error(f"Invalid parameters for {cmd.command}: {e}")
                    return self._error_response(
                        cmd.command_id,
                        f"Invalid parameters: {e}",
                        "ParameterError",
                    )
                driver_task: asyncio.Task[Any] = asyncio.ensure_future(call)
                self._inflight_tasks[cmd.command_id] = driver_task
                try:
                    raw = await driver_task
                except asyncio.CancelledError:
                    # The await was interrupted -- a server cancel (task already
                    # cancelled) or client shutdown (task NOT cancelled). Stop the
                    # driver task and wait for it to unwind BEFORE the device lock
                    # releases below, so the next command can't start on the same
                    # driver while this call is still physically running.
                    driver_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await driver_task
                    if cmd.command_id in self._cancelled_ids:
                        raise CommandCancelled(cmd.command_id)
                    raise
                finally:
                    self._inflight_tasks.pop(cmd.command_id, None)

                if cmd.command_id in self._cancelled_ids:
                    # Cancel landed just as the driver finished -- still suppress
                    # the response so a cancelled command never replies.
                    raise CommandCancelled(cmd.command_id)

                logger.debug(f"Command {cmd.command_id} completed successfully")
                try:
                    wrapped = self._wrap_result(cmd.command, raw, driver)
                except Exception as e:
                    # The command SUCCEEDED and we cannot project its answer.
                    # Nothing is wrong with the instrument, so this must not
                    # fault it; it is our own shape that is broken, and no
                    # amount of asking again fixes that.
                    logger.error(
                        f"Cannot project the result of {cmd.command}: {e}",
                        exc_info=True,
                    )
                    return self._error_response(
                        cmd.command_id,
                        f"Command succeeded; its result could not be read: {e}",
                        "ResultProjectionError",
                    )
                return ResponseMessage(
                    command_id=cmd.command_id,
                    success=True,
                    result=wrapped,
                )

            except CommandCancelled:
                # Server cancelled this command; no response. Re-raise so the
                # handler suppresses the reply (and the cache awaiter is failed).
                logger.info("Command %s cancelled by server", cmd.command_id)
                raise
            except Exception as e:
                # Past the parameter catch above, so this is the driver's own
                # failure and the driver says what it left the instrument in.
                # An error that has not said reads FAILED.
                logger.error(f"Command {cmd.command_id} failed: {e}", exc_info=True)
                return self._error_response(
                    cmd.command_id,
                    explain_driver_error(e),
                    type(e).__name__,
                    outcome_of(e),
                )
            finally:
                self._cancelled_ids.discard(cmd.command_id)

    def _wrap_result(
        self, command: str, raw: object, driver: object,
    ) -> Any:
        """Project the driver's raw return into the typed Response wire shape.

        Pairs each command with its per-op Response model via
        ``cheshire_drivers.response_lookup``, then:

          * dumps an existing BaseModel via ``model_dump(mode='json')``;
          * sends ``EmptyCommandResponse().model_dump()`` for ``None``
            (ack-only commands) so the wire ``result`` field is always
            populated;
          * projects a primitive (bool / float / int / str) onto the
            Response's single field when the per-op model has one field
            (InitializedResponse.is_initialized,
            TemperatureResponse.temperature, SpeedResponse.speed);
          * validates a dict against the response_cls and dumps it.

        Raises ``ValueError`` (caught by the executor and surfaced as
        ``ParameterError``) on shapes that don't match the response_cls
        contract -- the gateway-side fallback was dropped so the wire
        contract must hold here. Vendor-extra commands fall through
        ``EmptyCommandResponse`` from the lookup, so primitive returns
        from those will surface as a wire-shape error rather than being
        silently dropped.
        """
        interfaces: frozenset[str] = frozenset(
            getattr(type(driver), "interfaces", frozenset())
        )
        response_cls = lookup_response_model(command, interfaces)

        if isinstance(raw, BaseModel):
            return raw.model_dump(mode="json")
        if raw is None:
            return EmptyCommandResponse().model_dump(mode="json")
        if isinstance(raw, (bool, float, int, str)):
            fields = list(response_cls.model_fields.keys())
            if len(fields) == 1:
                try:
                    return response_cls.model_validate(
                        {fields[0]: raw}
                    ).model_dump(mode="json")
                except ValidationError as exc:
                    raise ValueError(
                        f"Cannot project primitive {raw!r} for {command!r} "
                        f"onto {response_cls.__name__}: {exc}"
                    ) from exc
            raise ValueError(
                f"Primitive driver return {raw!r} for {command!r} cannot "
                f"project onto {response_cls.__name__} "
                f"(expected single-field model, got fields={fields})"
            )
        if isinstance(raw, dict):
            try:
                return response_cls.model_validate(raw).model_dump(mode="json")
            except ValidationError as exc:
                raise ValueError(
                    f"Driver dict for {command!r} did not validate as "
                    f"{response_cls.__name__}: {exc}"
                ) from exc
        raise ValueError(
            f"Unhandled driver return shape {type(raw).__name__} for "
            f"{command!r}; expected None, BaseModel, dict, or primitive"
        )

    def _unsupported(self, cmd: CommandMessage, e: AttributeError) -> ResponseMessage:
        """The driver has no such name, and it says why (optional hardware
        absent, not connected yet). That reason reaches the operator."""
        logger.error(f"Device {cmd.device_name} cannot resolve {cmd.command}: {e}")
        return self._error_response(
            cmd.command_id,
            f"Device {cmd.device_name} does not support command {cmd.command}: {e}",
            "UnsupportedCommandError",
        )

    def _error_response(
        self,
        command_id: str,
        error: str,
        error_type: str,
        outcome: InstrumentOutcome = InstrumentOutcome.REJECTED,
    ) -> ResponseMessage:
        """Create error response message.

        ``REJECTED`` by default because most of these never reach a driver and
        never will: a command this agent does not have, a device it does not
        hold, a payload that will not deserialize. Nothing was actuated, so the
        control plane must not fault the device -- and nothing is gained by
        asking again, which is what separates it from ``REFUSED``. A caller
        that waits on one of these waits out its whole patience budget and then
        reports a cause it never established.

        A failure that came out of a driver call passes what the driver said.
        """
        return ResponseMessage(
            command_id=command_id,
            success=False,
            result=None,
            error=error,
            error_type=error_type,
            instrument_outcome=outcome,
        )

    def _deserialize_params(
        self,
        command: str,
        params: Dict[str, Any],
        driver: Any,
    ) -> Dict[str, Any]:
        """Deserialize complex objects in params before passing to driver.

        Per category, delegate to the cheshire-drivers wrap_<category>_payload
        helper, which validates the flat dict against the category's Request
        model and wraps under the right kwarg name for dispatch. Teachpoint
        deserialization for `pick_at_coords` / `place_at_coords` /
        `move_to_coords` runs inside `wrap_transporter_payload` via the
        Request's field validator. For commands not in any category registry,
        pass through unchanged.

        Each registry lookup is gated on the driver's advertised `interfaces`
        ClassVar. Without the gate, command names that exist in multiple
        categories (e.g. transporter's typed `initialize` vs other devices'
        parameterless BaseDriver inheritance) would crash on the wrong device
        type because the executor would wrap with the wrong category's Request
        model.

        Raises ValueError / pydantic.ValidationError on invalid payloads; the
        caller (execute) catches both and surfaces them as ParameterError.
        """
        interfaces = getattr(type(driver), "interfaces", frozenset())

        if command in LH_REQUEST_MODELS and "ILiquidHandler" in interfaces:
            return wrap_lh_payload(command, params)

        if command in LH_MOTION_REQUEST_MODELS and interfaces & _LH_MOTION_INTERFACES:
            return wrap_lh_motion_payload(command, params)

        if command in LIQUID_PROBE_REQUEST_MODELS and "ILiquidProbe" in interfaces:
            return wrap_liquid_probe_payload(command, params)

        if command in TRANSPORTER_REQUEST_MODELS and "ITransporter" in interfaces:
            return wrap_transporter_payload(command, params)

        if command in SHAKER_REQUEST_MODELS and "IShaker" in interfaces:
            return wrap_shaker_payload(command, params)

        if command in DELIDDER_REQUEST_MODELS and "IDelidder" in interfaces:
            return wrap_delidder_payload(command, params)

        if command in SEALER_REQUEST_MODELS and "ISealer" in interfaces:
            return wrap_sealer_payload(command, params)

        if command in CENTRIFUGE_REQUEST_MODELS and "ICentrifuge" in interfaces:
            return wrap_centrifuge_payload(command, params)

        if command in READER_REQUEST_MODELS and "IReader" in interfaces:
            return wrap_reader_payload(command, params)

        if command in PROTOCOL_RUNNER_REQUEST_MODELS and "IProtocolRunner" in interfaces:
            return wrap_protocol_runner_payload(command, params)

        if command in THERMOCYCLER_REQUEST_MODELS and "IThermocycler" in interfaces:
            return wrap_thermocycler_payload(command, params)

        return params
