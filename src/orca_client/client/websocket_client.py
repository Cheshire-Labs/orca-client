"""WebSocket client for connecting to an Orca gateway.

# CLIENT-SPECIFIC: Handles secure connection, message routing, and reconnection
"""

import asyncio
import logging
import json
import time
from typing import Optional, Callable, Awaitable
from urllib.parse import urlparse
import websockets
from websockets import ClientConnection

from cheshire_drivers.driver_errors import report_failure
from pydantic import JsonValue, ValidationError

from cheshire_drivers.gateway_protocol import (
    AssignedDevice, AssignmentResult, ClientHello, CommandMessage, CancelMessage, DeviceAssignment,
    HeartbeatMessage, MessageEnvelope, PROTOCOL_VERSION, ResponseMessage, StatusMessage,
)
from ..config.logging_setup import log_known_failure
from cheshire_drivers.command_executor import answer_command
from ..executor import ClientCommandExecutor
from ..config.models import ClientConfig

logger = logging.getLogger("orca_client.client")



def _read_assigned_devices(payload: dict[str, JsonValue]) -> tuple[list[AssignedDevice], dict[str, str]]:
    """Each device read on its own, so one setup this client cannot read leaves the others running."""
    raw = payload.get("devices")
    if not isinstance(raw, list):
        raise ValueError("an assignment must carry a devices list")
    devices: list[AssignedDevice] = []
    unreadable: dict[str, str] = {}
    for item in raw:
        try:
            devices.append(AssignedDevice.model_validate(item))
        except ValidationError as refused:
            name = item.get("name") if isinstance(item, dict) else None
            unreadable[str(name)] = f"orca-client could not read this device's setup: {refused}"
    return devices, unreadable


class WebSocketClient:
    """Manages the WebSocket connection to the gateway, with reconnection."""

    def __init__(
        self,
        config: ClientConfig,
        executor: ClientCommandExecutor,
        apply_assignment: Callable[[DeviceAssignment], Awaitable[AssignmentResult]],
    ):
        """Initialize WebSocket client.

        Args:
            config: Client configuration; `platform` sets the redial delays and heartbeat interval
            executor: Command executor for handling commands
            apply_assignment: Builds the devices the platform assigns and reports what it built
        """
        self.config = config
        self.executor = executor
        self._apply_assignment = apply_assignment

        self.ws: Optional[ClientConnection] = None
        self._connected = False
        # Set once the socket opens, so the redial delays restart after a working connection.
        self._opened = False
        self._running = False
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._receive_task: Optional[asyncio.Task] = None
        # The dial or live connection in flight, so stop() can cancel it rather
        # than wait out the connect timeout.
        self._attempt: Optional[asyncio.Task] = None
        self._start_time: float = time.time()  # Track uptime for heartbeats
        # Per-command handler tasks. Commands run concurrently (not awaited
        # inline in the receive loop) so a cancel message can be read and acted
        # on while a long command is still running. Done tasks self-evict.
        self._command_tasks: set[asyncio.Task] = set()
        self._assignment_lock = asyncio.Lock()
        # Serializes outbound frames: commands dispatch concurrently, so multiple
        # _handle_command tasks (and the heartbeat) can call _send at once, and
        # websockets' send() is not safe to interleave across coroutines.
        self._send_lock = asyncio.Lock()
        # Cuts a backoff between reconnect attempts short. stop() sets it so
        # quitting does not hold the devices open for the rest of the delay;
        # reconnect() sets it so the operator does not wait one out either.
        self._wake = asyncio.Event()
        # Why the client gave up, for the operator. None while it is running.
        self._stopped_because: Optional[str] = None

        # Status callback for UI/logging
        self.on_status_change: Optional[Callable[[str], Awaitable[None]]] = None

    @property
    def is_connected(self) -> bool:
        """Check if currently connected to platform."""
        return self._connected and self.ws is not None

    @property
    def is_running(self) -> bool:
        """True while the client is up and (re)connecting on its own.

        Goes False when ``stop`` is called or the platform refuses the
        connection permanently, which is the operator-visible difference
        between "reconnecting" and "gave up".
        """
        return self._running

    @property
    def uptime_seconds(self) -> float:
        """Seconds since the client was constructed, as reported in heartbeats."""
        return time.time() - self._start_time

    @property
    def stopped_because(self) -> Optional[str]:
        """Why the client gave up, or None while it is still running."""
        return self._stopped_because

    async def reconnect(self) -> None:
        """Redial now: drop any live socket and cut short any backoff."""
        logger.info("Reconnect requested")
        self._wake.set()
        if self.ws is not None:
            await self.ws.close()

    async def start(self) -> None:
        """Start the WebSocket client with automatic reconnection.

        Reconnects on transient errors: a network drop, a timeout, a runtime
        that is not listening yet or is restarting, or the platform still
        holding this client ID's old socket. Stops on permanent rejections (a
        URL that is not a WebSocket URL, close code 1002 version mismatch,
        close code 1008 or HTTP 401/403 auth failure): retrying those will
        loop forever because the operator has to fix something on-prem
        before the platform will accept the connection.
        """
        self._running = True
        self._wake.clear()
        self._stopped_because = None
        await self._run_until_stopped()

    async def _run_until_stopped(self) -> None:
        # Waits since the last connection that opened; picks the reconnect_backoff entry.
        waits = 0

        while self._running:
            self._wake.clear()
            self._opened = False
            self._attempt = asyncio.create_task(self._connect_and_run())
            try:
                await self._attempt

            except asyncio.CancelledError:
                logger.info("Client cancelled, stopping...")
                break

            except websockets.exceptions.ConnectionClosed as e:
                if e.code in (1002, 1008):
                    self._give_up(self._refusal_reason(e.code, e.reason))
                    break
                logger.warning("Connection closed by platform (code=%d reason=%r)", e.code, e.reason)

            except websockets.exceptions.InvalidStatus as e:
                # The Orca gateway refuses a bad key before the socket opens, which arrives as HTTP 403.
                status = e.response.status_code
                if status in (401, 403):
                    self._give_up(
                        f"The platform rejected the API key (HTTP {status}). "
                        f"Fix the key and restart the client."
                    )
                    break
                logger.error(f"Connection error: {e}")

            except websockets.exceptions.InvalidURI as e:
                self._give_up(
                    f"{e}. Fix platform.url in the config and restart the client."
                )
                break

            except OSError as unreachable:
                # Nothing is listening, or the link is down. Retried on purpose:
                # this process is expected to outlive a runtime that restarts.
                log_known_failure(
                    logger,
                    f"Could not reach the Orca runtime at {self.config.platform.url}: "
                    f"{unreachable}. Is it running?",
                )

            except Exception as e:
                logger.error(f"Connection error: {e}", exc_info=True)

            finally:
                self._attempt = None

            if not self._running:
                break
            # Even a clean close waits: a platform that closes every connection on open would spin this loop.
            if self._opened:
                waits = 0
            delay = self._delay_after(waits)
            logger.info("Reconnecting in %gs...", delay)
            await self._wait_before_retry(delay)
            waits += 1

    def _give_up(self, reason: str) -> None:
        """Stop redialing and record why, for the operator."""
        logger.error("Stopping instead of redialing. %s", reason)
        self._running = False
        self._stopped_because = reason

    @staticmethod
    def _refusal_reason(code: int, reason: str) -> str:
        """Operator-facing reason a refusal stopped the client.

        The close reason is the only place the platform names what it objected
        to (the protocol version it expects).
        """
        said = reason or "no reason given"
        if code == 1008:
            return (
                f"The platform rejected the API key (code {code}): {said}. "
                f"Fix the key and restart the client."
            )
        return (
            f"The platform refused the connection (code {code}): {said}. "
            f"The two builds disagree on the protocol version. Deploy matching "
            f"builds and restart."
        )

    def _delay_after(self, waits: int) -> float:
        """The configured delay after this many waits since the last connection that opened; the last one repeats."""
        delays = self.config.platform.reconnect_backoff
        return delays[min(waits, len(delays) - 1)]

    async def _wait_before_retry(self, delay: float) -> None:
        """Back off before redialing, returning early if stop or reconnect lands."""
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass

    @staticmethod
    async def _finish(task: Optional[asyncio.Task]) -> None:
        """End a task, discarding whatever it raised.

        A task that already failed holds its exception until awaited. Letting
        that escape stop() would skip the caller's device cleanup, so the
        failure is logged and dropped: the connection is going away regardless.
        """
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as error:
            logger.debug(f"Discarding error from a task being stopped: {error}")

    async def stop(self) -> None:
        """Stop the WebSocket client and cleanup."""
        logger.info("Stopping WebSocket client...")
        self._running = False
        self._wake.set()

        # Cancel tasks
        if self._attempt is not None:
            self._attempt.cancel()
        await self._finish(self._heartbeat_task)
        await self._finish(self._receive_task)

        # Cancel any in-flight command handler tasks.
        for task in list(self._command_tasks):
            task.cancel()
        for task in list(self._command_tasks):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._command_tasks.clear()

        # Close connection
        if self.ws:
            await self.ws.close()
            self.ws = None

        self._connected = False
        logger.info("WebSocket client stopped")

    async def _connect_and_run(self) -> None:
        """Connect to platform and run until disconnected."""
        parsed = urlparse(self.config.platform.url)

        # PlatformConfig already refused ws:// to any host but this machine.
        ssl_context = None if parsed.scheme == "ws" else True

        logger.info(f"Connecting to the Orca gateway: {self.config.platform.url}")

        # Send API key via header (not query param) for security
        async with websockets.connect(
            self.config.platform.url,
            ssl=ssl_context,
            additional_headers={"X-API-Key": self.config.platform.api_key}
        ) as ws:
            if not self._running:
                # stop() landed while we were dialing; leaving the context closes it.
                return
            self._opened = True
            self.ws = ws
            logger.info("Connected to the Orca gateway")

            await self._send_hello()

            self._connected = True
            await self._notify_status("CONNECTED")

            # Start heartbeat and receive tasks
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            self._receive_task = asyncio.create_task(self._receive_loop())

            # Wait for either task to complete (indicates disconnection)
            done, pending = await asyncio.wait(
                [self._heartbeat_task, self._receive_task],
                return_when=asyncio.FIRST_COMPLETED
            )

            # Cancel remaining task
            for task in pending:
                task.cancel()

            self._connected = False
            await self._notify_status("DISCONNECTED")

            # A failed task holds its exception until retrieved. Re-raise so
            # start() can tell a refusal from a link that merely dropped.
            errors = [t.exception() for t in done if not t.cancelled()]
            for error in errors:
                if error is not None:
                    raise error

    async def _send_hello(self) -> None:
        """Tell the platform who this client is; it answers with the devices to run."""
        msg = ClientHello(
            protocol_version=PROTOCOL_VERSION,
            client_id=self.config.client_id,
            site=self.config.site,
            lab=self.config.lab,
            workcell=self.config.workcell,
        )
        await self._send(MessageEnvelope.wrap_hello(msg))
        logger.info(f"Sent hello for client: {self.config.client_id} (site={self.config.site}, lab={self.config.lab})")

    async def _handle_assignment(self, envelope: MessageEnvelope) -> None:
        """Run the assigned devices, then tell the platform what was built. Assignments apply in the order they came."""
        async with self._assignment_lock:
            assignment_id = envelope.payload.get("assignment_id")
            if not isinstance(assignment_id, str):
                logger.error("An assignment came with no assignment_id, so it was not applied and cannot be answered.")
                return
            result = await self._build_assignment(assignment_id, envelope.payload)
            try:
                await self._send(MessageEnvelope.wrap_assignment_result(result))
                await self.publish_device_status()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Could not report an assignment's result: {e}", exc_info=True)

    async def _build_assignment(self, assignment_id: str, payload: dict[str, JsonValue]) -> AssignmentResult:
        """What was built. An assignment that fails as a whole still gets a result, naming the error."""
        try:
            devices, unreadable = _read_assigned_devices(payload)
            result = await self._apply_assignment(DeviceAssignment(assignment_id=assignment_id, devices=devices))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Error applying an assignment: {e}", exc_info=True)
            return AssignmentResult(
                assignment_id=assignment_id,
                devices=self.executor.registry.connect_info_list(),
                error=f"{type(e).__name__}: {e}",
            )
        if unreadable:
            result = result.model_copy(update={"errors": {**result.errors, **unreadable}})
        return result

    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeats to keep connection alive."""
        try:
            while self._connected:
                # Send heartbeat first, then sleep (ensures immediate heartbeat on connection)
                heartbeat = HeartbeatMessage(
                    timestamp=time.time(),  # Unix timestamp (float)
                    uptime=time.time() - self._start_time  # Seconds since start
                )
                envelope = MessageEnvelope.wrap_heartbeat(heartbeat)
                await self._send(envelope)
                logger.debug("Sent heartbeat")
                await self.publish_device_status()

                await asyncio.sleep(self.config.platform.heartbeat_interval)

        except asyncio.CancelledError:
            logger.debug("Heartbeat loop cancelled")
            raise
        except websockets.exceptions.ConnectionClosed:
            # The platform going away mid-heartbeat is ordinary. start() decides
            # whether to redial, and a trace here would bury its line.
            logger.info("Connection closed while sending a heartbeat")
            raise
        except Exception as e:
            logger.error(f"Heartbeat error: {e}", exc_info=True)
            raise

    async def _receive_loop(self) -> None:
        """Receive and process messages from platform."""
        if self.ws is None:
            return
        try:
            async for message in self.ws:
                try:
                    # Parse envelope
                    data = json.loads(message)
                    envelope = MessageEnvelope.model_validate(data)

                    # Route based on message type. Commands dispatch as
                    # concurrent tasks so the loop stays free to read a cancel
                    # (or another command) while a long command runs.
                    if envelope.type == "command":
                        task = asyncio.create_task(self._handle_command(envelope))
                        self._command_tasks.add(task)
                        task.add_done_callback(self._command_tasks.discard)
                    elif envelope.type == "cancel":
                        await self._handle_cancel(envelope)
                    elif envelope.type == "assignment":
                        # Beside the loop: replacing a device waits for its running command to finish.
                        task = asyncio.create_task(self._handle_assignment(envelope))
                        self._command_tasks.add(task)
                        task.add_done_callback(self._command_tasks.discard)
                    elif envelope.type == "heartbeat":
                        logger.debug("Received heartbeat")
                    else:
                        logger.warning(f"Unknown message type: {envelope.type}")

                except Exception as e:
                    logger.error(f"Error processing message: {e}", exc_info=True)

        except asyncio.CancelledError:
            logger.debug("Receive loop cancelled")
            raise
        except websockets.exceptions.ConnectionClosed:
            logger.info("Connection closed by platform")
            raise
        except Exception as e:
            logger.error(f"Receive error: {e}", exc_info=True)
            raise

    async def _handle_command(self, envelope: MessageEnvelope) -> None:
        """Run one command from the platform and answer it."""
        try:
            cmd = CommandMessage.model_validate(envelope.payload)
        except ValidationError as e:
            logger.error(f"Invalid command message: {e}", exc_info=True)
            raw_id = envelope.payload.get("command_id", "unknown")
            report = report_failure(e)
            await self._send(MessageEnvelope.wrap_response(ResponseMessage(
                command_id=raw_id if isinstance(raw_id, str) else "unknown",
                success=False, result=None, error=report.error, error_type=report.error_type,
            )))
            return
        logger.info(f"Received command: {cmd.command} on {cmd.device_name}")
        await answer_command(
            self.executor, cmd, respond=self._respond, report_status=self.publish_device_status,
        )

    async def _respond(self, response: ResponseMessage) -> None:
        try:
            await self._send(MessageEnvelope.wrap_response(response))
        except Exception as send_error:
            logger.error(f"Failed to send response {response.command_id}: {send_error}")

    async def _handle_cancel(self, envelope: MessageEnvelope) -> None:
        """Cancel an in-flight command at the server's request.

        The cancelled command's handler suppresses its response, so no reply is
        sent for it. Idempotent: a cancel for an unknown / finished command is a
        no-op.
        """
        try:
            msg = CancelMessage.model_validate(envelope.payload)
        except Exception as e:
            logger.error(f"Invalid cancel message: {e}", exc_info=True)
            return
        cancelled = await self.executor.cancel(msg.command_id)
        logger.info(
            "Cancel request for command %s (%s): %s",
            msg.command_id, msg.reason or "no reason",
            "cancelled" if cancelled else "not in flight",
        )

    async def _send(self, envelope: MessageEnvelope) -> None:
        """Send a message envelope to the platform over the websocket."""
        if not self.ws:
            raise RuntimeError("Not connected to platform")

        message = envelope.model_dump_json()
        async with self._send_lock:
            await self.ws.send(message)

    async def _notify_status(self, status: str) -> None:
        """Notify status change callback.

        Args:
            status: New status (CONNECTED, DISCONNECTED, etc.)
        """
        logger.info(f"Status changed: {status}")
        if self.on_status_change:
            try:
                await self.on_status_change(status)
            except Exception as e:
                logger.error(f"Error in status callback: {e}", exc_info=True)

    async def publish_device_status(self) -> None:
        """Report what the agent sees on each device to the control plane.

        The agent holds the driver objects, so it is the only party that can
        answer whether a device's own link is open. Sent with every heartbeat
        and again after each command, since connect / disconnect / initialize
        move that state.
        """
        if not self.is_connected:
            return

        try:
            devices = self.executor.registry.status_report()
            status_msg = StatusMessage(devices=devices, timestamp=time.time())
            envelope = MessageEnvelope.wrap_status(status_msg)
            await self._send(envelope)
            logger.debug(f"Sent status for {len(devices)} devices")

        except Exception as e:
            logger.error(f"Error sending status: {e}", exc_info=True)
