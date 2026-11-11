"""Event stream of one zApp: WebSocket ``/zrap/ws`` with liveness check and resync.

Behaviour (protocol observations by diaznet, zeptrionair-api docs/protocol.md, section 5):

* The zApp sends nothing while idle and does not answer WebSocket pings, so no
  client heartbeat is used. After ``liveness_interval`` seconds without a frame,
  a cheap ``GET /zrap/rssi`` checks that the device still answers; if not, the
  connection is dropped and re-established.
* After every (re)connect, all channel states are read once (``/zrap/chscan``)
  and passed on as ``snapshot`` updates, so changes made during the gap are not lost.
* Reconnects use exponential backoff (1 s to 60 s) with +/-20 % jitter.

The class does not depend on Home Assistant: it reports through callbacks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import Callable, Coroutine, Iterable
from typing import Any

import aiohttp

from .api import ZeptrionAirApiClient, ZeptrionAirApiClientError
from .const import (
    LIVENESS_INTERVAL_S,
    RECONNECT_BACKOFF_MAX_S,
    RECONNECT_BACKOFF_MIN_S,
)
from .smartfront import KeyEvent, KeyFunction, KeyStateTracker

_LOGGER = logging.getLogger(__name__)

ChannelCallback = Callable[[int, int, bool], None]
KeyCallback = Callable[[KeyEvent], None]
ConnectionCallback = Callable[[bool, str | None], None]


class _DisconnectedError(Exception):
    """The current connection stopped working."""


def _parse_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


class ZeptrionAirWebsocketListener:
    """Keeps the event stream of one zApp running."""

    def __init__(
        self,
        client: ZeptrionAirApiClient,
        *,
        on_channel_update: ChannelCallback,
        on_key_event: KeyCallback | None = None,
        on_connection: ConnectionCallback | None = None,
        keys: Iterable[KeyFunction] = (),
        liveness_interval: float = LIVENESS_INTERVAL_S,
        backoff_min: float = RECONNECT_BACKOFF_MIN_S,
        backoff_max: float = RECONNECT_BACKOFF_MAX_S,
        create_task: Callable[[Coroutine[Any, Any, None]], asyncio.Task[None]] | None = None,
    ) -> None:
        self._client = client
        self._hostname = client.hostname
        self._on_channel_update = on_channel_update
        self._on_key_event = on_key_event
        self._on_connection = on_connection
        self._tracker = KeyStateTracker()
        self._tracker.set_keys(keys)
        self._liveness = liveness_interval
        self._backoff_min = backoff_min
        self._backoff_max = backoff_max
        self._create_task = create_task or asyncio.create_task
        self._task: asyncio.Task[None] | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None

        # Statistics for diagnostics
        self.connected = False
        self.connected_since: float | None = None
        self.last_message_time: float | None = None
        self.messages_received = 0
        self.connects = 0
        self.liveness_checks = 0
        self.last_error: str | None = None

    # ------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        """Start (or restart) the background task."""
        if self._task and not self._task.done():
            await self.stop()
        self._task = self._create_task(self._run())

    async def stop(self) -> None:
        """Stop the background task and close the connection."""
        task, self._task = self._task, None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as err:  # pragma: no cover - defensive
                _LOGGER.debug("[%s] Listener task ended with %s", self._hostname, err)
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        self._ws = None
        self.connected = False

    def is_alive(self) -> bool:
        """True while the background task runs (connected or reconnecting)."""
        return self._task is not None and not self._task.done()

    def set_keys(self, keys: Iterable[KeyFunction]) -> None:
        """Set the Smartfront key functions used to decode key events."""
        self._tracker.set_keys(keys)

    def get_stats(self) -> dict[str, Any]:
        """Connection statistics for diagnostics."""
        return {
            "connected": self.connected,
            "connected_since": self.connected_since,
            "last_message_time": self.last_message_time,
            "messages_received": self.messages_received,
            "connects": self.connects,
            "liveness_checks": self.liveness_checks,
            "last_error": self.last_error,
            "task_running": self.is_alive(),
        }

    # ------------------------------------------------------------------ loop

    async def _run(self) -> None:
        backoff = self._backoff_min
        while True:
            try:
                await self._connect_and_listen()
                error = "connection closed"
            except asyncio.CancelledError:
                raise
            except _DisconnectedError as err:
                error = str(err)
            except Exception as err:  # never let the task die
                _LOGGER.exception("[%s] Unexpected error in event stream", self._hostname)
                error = f"{type(err).__name__}: {err}"

            if self.connected_since is not None and self.connected:
                # The connection had been up: start over with a short wait.
                backoff = self._backoff_min
            self.connected = False
            self.last_error = error
            self._notify_connection(False, error)

            delay = backoff * random.uniform(0.8, 1.2)
            _LOGGER.debug("[%s] Event stream down (%s), reconnecting in %.1f s", self._hostname, error, delay)
            await asyncio.sleep(delay)
            backoff = min(self._backoff_max, backoff * 2)

    async def _connect_and_listen(self) -> None:
        try:
            async with asyncio.timeout(15):
                ws = await self._client.session.ws_connect(
                    self._client.websocket_url,
                    autoping=True,
                    heartbeat=None,
                )
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise _DisconnectedError(f"cannot connect WebSocket: {type(err).__name__}: {err}") from err

        self._ws = ws
        try:
            self.connects += 1
            self.connected = True
            self.connected_since = time.time()
            self._tracker.reset()
            self._notify_connection(True, None)
            await self._snapshot()

            while True:
                try:
                    message = await ws.receive(timeout=self._liveness)
                except TimeoutError:
                    await self._check_alive()
                    continue

                if message.type == aiohttp.WSMsgType.TEXT:
                    self.messages_received += 1
                    self.last_message_time = time.time()
                    self._handle_frame(message.data)
                elif message.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                ):
                    code = ws.close_code
                    raise _DisconnectedError(f"WebSocket closed ({message.type.name}, code {code})")
        finally:
            self._ws = None
            if not ws.closed:
                await ws.close()

    async def _check_alive(self) -> None:
        self.liveness_checks += 1
        try:
            await self._client.async_get_rssi()
        except ZeptrionAirApiClientError as err:
            raise _DisconnectedError(f"device stopped answering: {err}") from err

    async def _snapshot(self) -> None:
        """Read all channel states once and pass them on."""
        try:
            data = await self._client.async_get_all_channels_scan_info()
        except ZeptrionAirApiClientError as err:
            raise _DisconnectedError(f"cannot read channel states: {err}") from err
        chscan = data.get("chscan") if isinstance(data, dict) else None
        if not isinstance(chscan, dict):
            return
        for key, node in chscan.items():
            if not key.startswith("ch") or not isinstance(node, dict):
                continue
            channel = _parse_int(key[2:])
            value = _parse_int(node.get("val"))
            if channel is not None and value is not None:
                self._safe_call(self._on_channel_update, channel, value, True)

    # --------------------------------------------------------------- frames

    def _handle_frame(self, frame: str) -> None:
        try:
            data = json.loads(frame)
        except ValueError:
            _LOGGER.debug("[%s] Ignoring non-JSON frame: %r", self._hostname, frame)
            return
        if not isinstance(data, dict):
            return

        eid1 = data.get("eid1")
        if isinstance(eid1, dict):
            channel = _parse_int(eid1.get("ch"))
            value = _parse_int(eid1.get("val"))
            if channel is not None and value is not None:
                self._safe_call(self._on_channel_update, channel, value, False)
            else:
                _LOGGER.debug("[%s] Ignoring malformed eid1 frame: %s", self._hostname, data)

        eid2 = data.get("eid2")
        if isinstance(eid2, dict) and isinstance(eid2.get("bta"), str):
            for event in self._tracker.update(eid2["bta"]):
                if self._on_key_event is not None:
                    self._safe_call(self._on_key_event, event)

    # -------------------------------------------------------------- helpers

    def _notify_connection(self, connected: bool, error: str | None) -> None:
        if self._on_connection is not None:
            self._safe_call(self._on_connection, connected, error)

    def _safe_call(self, callback: Callable[..., None], *args: Any) -> None:
        try:
            callback(*args)
        except Exception:  # a broken consumer must not stop the stream
            _LOGGER.exception("[%s] Error in event stream consumer", self._hostname)
