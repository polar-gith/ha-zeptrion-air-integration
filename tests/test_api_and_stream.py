"""Tests of the HTTP client and the event stream against the simulated zApp."""

from __future__ import annotations

import asyncio

import aiohttp
import pytest

from custom_components.zeptrion_air.api import (
    ZeptrionAirApiClient,
    ZeptrionAirApiClientCommunicationError,
)
from custom_components.zeptrion_air.smartfront import parse_btfu, parse_sensor_value
from custom_components.zeptrion_air.websocket_listener import ZeptrionAirWebsocketListener


async def _wait_for(predicate, timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


async def test_command_is_sent_once_and_redirect_not_followed(fake_zapp) -> None:
    device, host = fake_zapp
    async with aiohttp.ClientSession() as session:
        client = ZeptrionAirApiClient(host, session)
        await client.async_channel_open(1)
    assert device.commands == [(1, "open")]
    assert "GET /" not in device.requests  # 302 answer was not followed


async def test_command_not_retried_on_timeout(fake_zapp) -> None:
    device, host = fake_zapp
    device.fail_commands_with_timeout = True
    async with aiohttp.ClientSession() as session:
        client = ZeptrionAirApiClient(host, session, request_timeout=0.3)
        with pytest.raises(ZeptrionAirApiClientCommunicationError):
            await client.async_channel_move_open(1, 5000)
    assert device.commands == [(1, "move_open_5000")]


async def test_read_retried_on_server_error(fake_zapp) -> None:
    device, host = fake_zapp
    device.http_down = True
    async with aiohttp.ClientSession() as session:
        client = ZeptrionAirApiClient(host, session, request_timeout=0.2, base_delay=0.01)
        with pytest.raises(ZeptrionAirApiClientCommunicationError):
            await client.async_get_rssi()
    assert device.requests.count("GET /zrap/rssi") == 3


async def test_at_most_two_parallel_requests(fake_zapp) -> None:
    device, host = fake_zapp
    device.command_delay = 0.1
    async with aiohttp.ClientSession() as session:
        client = ZeptrionAirApiClient(host, session)
        await asyncio.gather(*(client.async_channel_stop(1) for _ in range(6)))
    assert len(device.commands) == 6
    assert device.max_in_flight == 2


async def test_smartfront_info_and_missing_smartfront(fake_zapp) -> None:
    device, host = fake_zapp
    async with aiohttp.ClientSession() as session:
        client = ZeptrionAirApiClient(host, session)
        info = await client.async_get_smartfront_info()
        assert info["btfu"] == device.btfu
        device.btfu = None
        assert await client.async_get_smartfront_info() is None
        assert await client.async_get_smartfront_sensors() is None


def test_btfu_and_sensor_parsing() -> None:
    keys = parse_btfu("0,0,1000,1000,8501,8602,4501,4602")
    assert [key.kind for key in keys] == [
        "unused", "unused", "smartbutton", "smartbutton", "channel", "channel", "channel", "channel",
    ]
    assert keys[4].channels == (1,) and keys[4].direction == "up"
    assert keys[7].channels == (2,) and keys[7].direction == "down"
    assert parse_btfu("") == ()
    assert parse_sensor_value("35.44C") == 35.44
    assert parse_sensor_value("none") is None


async def test_event_stream_snapshot_updates_and_keys(fake_zapp) -> None:
    device, host = fake_zapp
    device.values = {1: 0, 2: 100}
    updates: list[tuple[int, int, bool]] = []
    keys: list[tuple[int, bool, str | None]] = []
    connection: list[bool] = []

    async with aiohttp.ClientSession() as session:
        client = ZeptrionAirApiClient(host, session)
        listener = ZeptrionAirWebsocketListener(
            client,
            on_channel_update=lambda ch, val, snap: updates.append((ch, val, snap)),
            on_key_event=lambda ev: keys.append(
                (ev.key, ev.pressed, ev.function.kind if ev.function else None)
            ),
            on_connection=lambda up, err: connection.append(up),
            keys=parse_btfu(device.btfu),
        )
        await listener.start()
        try:
            await _wait_for(lambda: len(updates) >= 2)
            assert connection == [True]
            assert sorted(updates) == [(1, 0, True), (2, 100, True)]

            await device.push({"eid1": {"ch": 1, "val": 100}})
            await _wait_for(lambda: (1, 100, False) in updates)

            await device.press(7)
            await _wait_for(lambda: len(keys) >= 2)
            assert keys == [(7, True, "channel"), (7, False, "channel")]
        finally:
            await listener.stop()


async def test_event_stream_reconnects_and_resyncs(fake_zapp) -> None:
    device, host = fake_zapp
    updates: list[tuple[int, int, bool]] = []
    connection: list[bool] = []

    async with aiohttp.ClientSession() as session:
        client = ZeptrionAirApiClient(host, session)
        listener = ZeptrionAirWebsocketListener(
            client,
            on_channel_update=lambda ch, val, snap: updates.append((ch, val, snap)),
            on_connection=lambda up, err: connection.append(up),
            backoff_min=0.05,
        )
        await listener.start()
        try:
            await _wait_for(lambda: connection == [True])
            device.values[2] = 100  # changes while the connection is down
            await device.drop_websockets()
            await _wait_for(lambda: connection[-1] is True and len(connection) >= 3)
            assert connection[:3] == [True, False, True]
            await _wait_for(lambda: (2, 100, True) in updates)
            assert device.ws_connects == 2
        finally:
            await listener.stop()


async def test_silent_dead_device_is_detected(fake_zapp) -> None:
    """No frames and no HTTP answer: the liveness check drops the connection."""
    device, host = fake_zapp
    connection: list[bool] = []
    async with aiohttp.ClientSession() as session:
        client = ZeptrionAirApiClient(host, session, request_timeout=0.2, base_delay=0.01)
        listener = ZeptrionAirWebsocketListener(
            client,
            on_channel_update=lambda *args: None,
            on_connection=lambda up, err: connection.append(up),
            liveness_interval=0.3,
            backoff_min=0.05,
        )
        await listener.start()
        try:
            await _wait_for(lambda: connection == [True])
            device.http_down = True
            await _wait_for(lambda: False in connection)
            assert listener.liveness_checks >= 1
            assert "device stopped answering" in (listener.last_error or "")
            device.http_down = False
            await _wait_for(lambda: connection[-1] is True and len(connection) >= 3, timeout=10)
        finally:
            await listener.stop()
