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

from cheshire_drivers.driver_errors import explain_driver_error
from cheshire_drivers.gateway_protocol import (
    MessageEnvelope, ConnectMessage, CommandMessage, CancelMessage,
    ResponseMessage, StatusMessage, HeartbeatMessage, PROTOCOL_VERSION
)
from ..executor import CommandCancelled, CommandExecutor
from ..config.models import ClientConfig

logger = logging.getLogger("orca_client.client")


class WebSocketClient:
    """Manages the WebSocket connection to the gateway, with reconnection."""

    def __init__(self, config: ClientConfig, executor: CommandExecutor):
        """Initialize WebSocket client.

        Args:
            config: Client configuration; `platform` sets the redial delays and heartbeat interval
            executor: Command executor for handling commands
        """
        self.config = config
        self.executor = executor

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

        Reconnects on transient errors (network drops, timeouts). Stops
        on permanent rejections (close code 1002 contract collision, close
        code 1008 or HTTP 401/403 auth failure): retrying those will loop
        forever because the operator has to fix something on-prem (config,
        driver class, API key) before the platform will accept the connection.
        """
        self._running = True
        self._wake.clear()
        self._stopped_because = None
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
        logger.error("Connection refused permanently by platform. Stopping. %s", reason)
        self._running = False
        self._stopped_because = reason

    @staticmethod
    def _refusal_reason(code: int, reason: str) -> str:
        """Operator-facing reason a refusal stopped the client.

        The close reason is the only place the platform names what it objected
        to (the protocol version it expects, or the device it will not accept).
        """
        said = reason or "no reason given"
        if code == 1008:
            return (
                f"The platform rejected the API key (code {code}): {said}. "
                f"Fix the key and restart the client."
            )
        return (
            f"The platform refused the connection (code {code}): {said}. "
            f"The reason above names what it expects. Either the two builds "
            f"disagree on the protocol version, in whichever direction, or a "
            f"device's contract does not match the topology. Reconcile the two "
            f"sides and restart."
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

            # Send connection message
            await self._send_connect_message()

            # Wait for connection acknowledgment
            # (For MVP, we assume immediate connection)
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

    async def _send_connect_message(self) -> None:
        """Send initial connection message to platform."""
        msg = ConnectMessage(
            protocol_version=PROTOCOL_VERSION,
            site=self.config.site,
            lab=self.config.lab,
            workcell=self.config.workcell,
            devices=self.executor.registry.connect_info_list(),
        )
        envelope = MessageEnvelope.wrap_connect(msg)
        await self._send(envelope)
        logger.info(f"Sent connection message for client: {self.config.client_id} (site={self.config.site}, lab={self.config.lab})")

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
        """Handle command message from platform.

        Args:
            envelope: Message envelope with command payload
        """
        try:
            # Parse command
            cmd = CommandMessage.model_validate(envelope.payload)
            logger.info(f"Received command: {cmd.command} on {cmd.device_name}")

            # Execute command
            response = await self.executor.execute(cmd)

            # Before the response: the platform reads this socket in order, so
            # responding first unblocks the caller while the flag is still unread.
            await self.publish_device_status()

            response_envelope = MessageEnvelope.wrap_response(response)
            await self._send(response_envelope)

            logger.info(f"Command {cmd.command_id} completed: {response.success}")

        except CommandCancelled as cancelled:
            # Server no longer awaits this command: no response, and the driver
            # result is dropped. It stopped part-way, so still report state.
            logger.info(
                "Command %s cancelled by server; no response sent",
                cancelled.command_id,
            )
            await self.publish_device_status()
            return
        except asyncio.CancelledError:
            # This handler task itself is being torn down (shutdown). Propagate.
            raise
        except Exception as e:
            logger.error(f"Error handling command: {e}", exc_info=True)
            # Reported before the error goes out, same ordering as a success.
            await self.publish_device_status()
            try:
                raw_id = envelope.payload.get("command_id", "unknown")
                cmd_id = raw_id if isinstance(raw_id, str) else "unknown"
                error_response = ResponseMessage(
                    command_id=cmd_id,
                    success=False,
                    result=None,
                    error=explain_driver_error(e),
                    error_type=type(e).__name__,
                    # No outcome on purpose. This catch spans the executor
                    # call, so an escape can come from before a driver ran or
                    # from after one did, and the control plane reads a missing
                    # outcome as a failure -- the assumption that is safe to be
                    # wrong about. Everything the executor handles classifies
                    # itself and never reaches here.
                )
                error_envelope = MessageEnvelope.wrap_response(error_response)
                await self._send(error_envelope)
            except Exception as send_error:
                logger.error(f"Failed to send error response: {send_error}")

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
