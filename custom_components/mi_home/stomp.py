"""Minimal STOMP-over-WebSocket client for Moving Intelligence push updates.

The MI app uses Spring's STOMP-over-WebSocket implementation. We connect to
{base}/app/websocket?token={JSESSIONID} and subscribe to user-scoped topics.

STOMP frame format (text):
    COMMAND\\n
    header1:value1\\n
    header2:value2\\n
    \\n
    body\\x00

This client deliberately avoids external dependencies (stomppy, aiostomp) so
the integration has zero non-stdlib requirements beyond what HA already ships.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

import aiohttp
from yarl import URL

from .const import (
    SESSION_API_BASE,
    STOMP_HEARTBEAT_MS,
    STOMP_RECONNECT_DELAY,
    WEBSOCKET_PATH,
)

_LOGGER = logging.getLogger(__name__)

NULL = "\x00"
NEWLINE = "\n"


class StompFrame:
    """A parsed STOMP frame."""

    __slots__ = ("command", "headers", "body")

    def __init__(self, command: str, headers: dict[str, str], body: str) -> None:
        self.command = command
        self.headers = headers
        self.body = body


def _encode_frame(command: str, headers: dict[str, str], body: str = "") -> str:
    """Encode a STOMP frame for sending."""
    lines = [command]
    for k, v in headers.items():
        lines.append(f"{k}:{v}")
    lines.append("")
    lines.append(body)
    return NEWLINE.join(lines) + NULL


def _decode_frame(raw: str) -> StompFrame | None:
    """Decode a single STOMP frame from text. Returns None for empty frames."""
    raw = raw.rstrip(NULL).strip()
    if not raw or raw == "\n":
        return None  # heartbeat
    parts = raw.split("\n\n", 1)
    if len(parts) != 2:
        return None
    header_block, body = parts
    header_lines = header_block.split("\n")
    command = header_lines[0]
    headers: dict[str, str] = {}
    for line in header_lines[1:]:
        if ":" in line:
            key, _, val = line.partition(":")
            headers[key] = val
    return StompFrame(command, headers, body)


class MiStompClient:
    """STOMP-over-WebSocket client for MI live position events.

    Usage:
        client = MiStompClient(session_id, on_message=callback)
        await client.start()
        ...
        await client.stop()

    The on_message callback receives (topic, parsed_json_body) for every
    incoming MESSAGE frame on a subscribed destination.
    """

    def __init__(
        self,
        session_id: str,
        on_message: Callable[[str, dict], None],
        on_state_change: Callable[[bool], None] | None = None,
    ) -> None:
        self._session_id = session_id
        self._on_message = on_message
        self._on_state_change = on_state_change
        self._http: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._task: asyncio.Task | None = None
        self._subscriptions: dict[str, str] = {}  # destination → subscription id
        self._next_sub_id = 0
        self._stop_event = asyncio.Event()
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def update_session_id(self, session_id: str) -> None:
        """Update the session token (e.g. after re-login)."""
        self._session_id = session_id

    async def start(self) -> None:
        """Begin running the STOMP client. Reconnects automatically on failure."""
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._run_loop(), name="mi_home_stomp_client"
        )

    async def stop(self) -> None:
        """Stop the client and close all connections."""
        self._stop_event.set()
        if self._ws and not self._ws.closed:
            try:
                await self._ws.send_str(_encode_frame("DISCONNECT", {}))
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        if self._http and not self._http.closed:
            await self._http.close()
        self._http = None
        self._ws = None
        self._connected = False

    async def subscribe(self, destination: str) -> None:
        """Subscribe to a STOMP destination once connected."""
        if destination in self._subscriptions:
            return
        sub_id = f"sub-{self._next_sub_id}"
        self._next_sub_id += 1
        self._subscriptions[destination] = sub_id
        if self._ws and not self._ws.closed and self._connected:
            await self._send_subscribe(destination, sub_id)

    async def _run_loop(self) -> None:
        """Main connect/reconnect loop."""
        while not self._stop_event.is_set():
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                _LOGGER.warning("STOMP connection failed: %s", e)
            self._set_connected(False)
            if self._stop_event.is_set():
                break
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=STOMP_RECONNECT_DELAY
                )
            except asyncio.TimeoutError:
                continue

    async def _connect_and_listen(self) -> None:
        """Establish a single WebSocket+STOMP session and read frames until close."""
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession()

        ws_url = URL(SESSION_API_BASE).with_scheme("wss") / WEBSOCKET_PATH.lstrip("/")
        ws_url = ws_url.with_query(token=self._session_id)

        # Cookie-based auth: pass JSESSIONID as cookie too (server may check either)
        cookies = {"JSESSIONID": self._session_id}

        _LOGGER.debug("Connecting STOMP WebSocket to %s", ws_url)
        async with self._http.ws_connect(
            str(ws_url),
            cookies=cookies,
            heartbeat=STOMP_HEARTBEAT_MS / 1000,
        ) as ws:
            self._ws = ws
            await self._send_connect()
            await self._listen()

    async def _send_connect(self) -> None:
        """Send the STOMP CONNECT frame."""
        if not self._ws:
            return
        frame = _encode_frame(
            "CONNECT",
            {
                "accept-version": "1.2,1.1,1.0",
                "heart-beat": f"{STOMP_HEARTBEAT_MS},{STOMP_HEARTBEAT_MS}",
            },
        )
        await self._ws.send_str(frame)

    async def _send_subscribe(self, destination: str, sub_id: str) -> None:
        """Send a STOMP SUBSCRIBE frame."""
        if not self._ws:
            return
        frame = _encode_frame(
            "SUBSCRIBE",
            {"id": sub_id, "destination": destination},
        )
        await self._ws.send_str(frame)
        _LOGGER.debug("STOMP subscribed to %s (id=%s)", destination, sub_id)

    async def _listen(self) -> None:
        """Read STOMP frames until the WebSocket closes or stop is requested."""
        if not self._ws:
            return
        async for msg in self._ws:
            if self._stop_event.is_set():
                break
            if msg.type == aiohttp.WSMsgType.TEXT:
                frame = _decode_frame(msg.data)
                if frame is not None:
                    await self._handle_frame(frame)
            elif msg.type in (
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSING,
                aiohttp.WSMsgType.ERROR,
            ):
                _LOGGER.debug("STOMP WebSocket closed: %s", msg.type)
                break

    async def _handle_frame(self, frame: StompFrame) -> None:
        """Process a single inbound STOMP frame."""
        if frame.command == "CONNECTED":
            _LOGGER.info("STOMP connected (server: %s)", frame.headers.get("server", ""))
            self._set_connected(True)
            # Re-subscribe to all known destinations after (re)connect
            for destination, sub_id in self._subscriptions.items():
                await self._send_subscribe(destination, sub_id)
        elif frame.command == "MESSAGE":
            destination = frame.headers.get("destination", "")
            try:
                body = json.loads(frame.body) if frame.body else {}
            except json.JSONDecodeError:
                _LOGGER.warning("STOMP MESSAGE body not JSON on %s", destination)
                return
            try:
                self._on_message(destination, body)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("STOMP message handler raised")
        elif frame.command == "ERROR":
            _LOGGER.warning(
                "STOMP ERROR: %s — %s",
                frame.headers.get("message", ""),
                frame.body[:200],
            )
            self._set_connected(False)

    def _set_connected(self, connected: bool) -> None:
        if self._connected != connected:
            self._connected = connected
            if self._on_state_change:
                try:
                    self._on_state_change(connected)
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("STOMP state change callback raised")
