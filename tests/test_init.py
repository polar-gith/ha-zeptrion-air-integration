"""Integration tests: setup, wall keys, Smartfront sensor, availability."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.zeptrion_air.const import CONF_HOSTNAME, DOMAIN


async def _wait_for(predicate, timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


async def _setup(hass: HomeAssistant, host: str, serial: str) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"zapp-{serial}",
        unique_id=serial,
        data={CONF_HOSTNAME: host},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _entity_id(hass: HomeAssistant, unique_id: str, domain: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)
    assert entity_id, f"no {domain} entity with unique_id {unique_id}"
    return entity_id


async def test_setup_creates_entities_and_wall_keys(hass: HomeAssistant, fake_zapp) -> None:
    device, host = fake_zapp
    entry = await _setup(hass, host, device.serial)
    runtime = entry.runtime_data
    await _wait_for(lambda: runtime.websocket_listener and runtime.websocket_listener.connected)

    cover = _entity_id(hass, f"zapp_{device.serial}_ch1", "cover")
    light = _entity_id(hass, f"zapp_{device.serial}_ch2_light", "light")
    keys = _entity_id(hass, f"zapp_{device.serial}_wall_keys", "event")
    temp = _entity_id(hass, f"zapp_{device.serial}_smartfront_temp", "sensor")
    assert er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"zapp_{device.serial}_smartfront_lux"
    ) is None  # "none" on this Smartfront

    assert hass.states.get(cover).state != STATE_UNAVAILABLE
    assert hass.states.get(temp).state == "23.5"

    key_state = hass.states.get(keys)
    assert "key_5_press" in key_state.attributes["event_types"]
    assert "key_1_press" not in key_state.attributes["event_types"]  # unused key

    # A wall key press becomes an event with type and attributes
    await device.press(5)
    await _wait_for(
        lambda: hass.states.get(keys).attributes.get("event_type") == "key_5_release"
    )
    attrs = hass.states.get(keys).attributes
    assert attrs["function"] == "channel"
    assert attrs["channels"] == [1]
    assert attrs["direction"] == "up"

    # Smartbutton key
    await device.press(3)
    await _wait_for(
        lambda: hass.states.get(keys).attributes.get("event_type") == "key_3_release"
    )
    assert hass.states.get(keys).attributes["function"] == "smartbutton"

    # Light follows a pushed change
    await device.push({"eid1": {"ch": 2, "val": 100}})
    await _wait_for(lambda: hass.states.get(light).state == "on")

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_short_outage_keeps_entities_available(hass: HomeAssistant, fake_zapp) -> None:
    device, host = fake_zapp
    entry = await _setup(hass, host, device.serial)
    runtime = entry.runtime_data
    listener = runtime.websocket_listener
    await _wait_for(lambda: listener.connected)
    cover = _entity_id(hass, f"zapp_{device.serial}_ch1", "cover")

    await device.drop_websockets()
    await _wait_for(lambda: runtime.stream_down)
    await _wait_for(lambda: not runtime.stream_down, timeout=10)
    await hass.async_block_till_done()
    assert hass.states.get(cover).state != STATE_UNAVAILABLE
    assert runtime.short_outages == 1

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_long_outage_makes_entities_unavailable(hass: HomeAssistant, fake_zapp) -> None:
    device, host = fake_zapp
    entry = await _setup(hass, host, device.serial)
    runtime = entry.runtime_data
    listener = runtime.websocket_listener
    await _wait_for(lambda: listener.connected)
    cover = _entity_id(hass, f"zapp_{device.serial}_ch1", "cover")
    light = _entity_id(hass, f"zapp_{device.serial}_ch2_light", "light")
    # keep reconnect attempts short so the test does not depend on backoff growth
    listener._backoff_min, listener._backoff_max = 0.1, 0.5

    device.http_down = True
    await device.drop_websockets()
    await _wait_for(lambda: runtime.stream_down)

    # Still available inside the grace period
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done()
    assert hass.states.get(cover).state != STATE_UNAVAILABLE

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    assert hass.states.get(cover).state == STATE_UNAVAILABLE
    assert hass.states.get(light).state == STATE_UNAVAILABLE

    # Device comes back: reconnect, resync, available again
    device.values[2] = 100
    device.http_down = False
    await _wait_for(lambda: runtime.available, timeout=15)
    await hass.async_block_till_done()
    assert hass.states.get(cover).state != STATE_UNAVAILABLE
    await _wait_for(lambda: hass.states.get(light).state == "on")

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_zapp_without_smartfront_has_no_key_entity(hass: HomeAssistant, fake_zapp) -> None:
    device, host = fake_zapp
    device.btfu = None
    entry = await _setup(hass, host, device.serial)
    assert er.async_get(hass).async_get_entity_id(
        "event", DOMAIN, f"zapp_{device.serial}_wall_keys"
    ) is None
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_retries_when_device_offline(hass: HomeAssistant, fake_zapp) -> None:
    device, host = fake_zapp
    entry = MockConfigEntry(
        domain=DOMAIN, title="zapp-x", unique_id=device.serial, data={CONF_HOSTNAME: "127.0.0.1:1"}
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    from homeassistant.config_entries import ConfigEntryState

    assert entry.state is ConfigEntryState.SETUP_RETRY
