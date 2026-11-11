"""Blind direction, position and learning mode against the simulated zApp."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zeptrion_air.const import CONF_HOSTNAME, DOMAIN


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


@pytest.fixture
def clock():
    clock = Clock()
    fake_time = SimpleNamespace(monotonic=clock.monotonic)
    with patch("custom_components.zeptrion_air.cover.time", fake_time):
        yield clock


async def _wait_for(predicate, timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


async def _setup(hass: HomeAssistant, device, host: str, options: dict | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"zapp-{device.serial}",
        unique_id=device.serial,
        data={CONF_HOSTNAME: host},
        options=options or {},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await _wait_for(lambda: entry.runtime_data.websocket_listener.connected)
    return entry


def _eid(hass: HomeAssistant, domain: str, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)
    assert entity_id
    return entity_id


async def _value(hass, device, clock, at: float, value: int, channel: int = 1) -> None:
    clock.now = at
    await device.push({"eid1": {"ch": channel, "val": value}})
    await asyncio.sleep(0.05)
    await hass.async_block_till_done()


async def _key(hass, device, clock, at: float, key: int) -> None:
    clock.now = at
    await device.press(key, release_after=0.01)
    await asyncio.sleep(0.05)
    await hass.async_block_till_done()


async def test_direction_from_wall_key_and_full_run(hass: HomeAssistant, fake_zapp, clock) -> None:
    device, host = fake_zapp
    entry = await _setup(hass, device, host)
    cover = _eid(hass, "cover", f"zapp_{device.serial}_ch1")

    await _key(hass, device, clock, 1000.0, 6)  # key 6 = ch1 down
    await _value(hass, device, clock, 1000.2, 100)
    assert hass.states.get(cover).state == "closing"

    await _value(hass, device, clock, 1060.2, 0)  # relay timer
    state = hass.states.get(cover)
    assert state.state == "closed"
    assert state.attributes["end_stop"] == "bottom"

    # scene: direction unknown, end stop forgotten
    await _value(hass, device, clock, 1100.0, 100)
    await _value(hass, device, clock, 1160.0, 0)
    assert hass.states.get(cover).attributes["end_stop"] is None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_position_and_set_position(hass: HomeAssistant, fake_zapp, clock) -> None:
    device, host = fake_zapp
    entry = await _setup(
        hass, device, host, {"travel_up_ch1": 50.0, "travel_down_ch1": 40.0}
    )
    cover = _eid(hass, "cover", f"zapp_{device.serial}_ch1")

    clock.now = 2000.0
    await hass.services.async_call("cover", "open_cover", {"entity_id": cover}, blocking=True)
    await _value(hass, device, clock, 2000.1, 100)
    await _value(hass, device, clock, 2060.1, 0)
    state = hass.states.get(cover)
    assert state.state == "open"
    assert state.attributes["current_position"] == 100

    clock.now = 2100.0
    await hass.services.async_call(
        "cover", "set_cover_position", {"entity_id": cover, "position": 50}, blocking=True
    )
    assert device.commands[-1] == (1, "move_close_20000")
    await _value(hass, device, clock, 2100.1, 100)
    await _value(hass, device, clock, 2120.1, 0)  # timed command ends by itself
    # a timed move is not stopped by hand, so it counts as a full run? No: it must
    # end at the estimate, not at the end stop.
    assert hass.states.get(cover).attributes["current_position"] == 50

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_learning_mode_stores_travel_time(hass: HomeAssistant, fake_zapp, clock) -> None:
    device, host = fake_zapp
    entry = await _setup(hass, device, host)
    switch = _eid(hass, "switch", f"zapp_{device.serial}_blind_learning")

    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    assert hass.states.get(switch).state == "on"

    for start in (3000.0, 3200.0):
        # full run down: at the bottom
        await _key(hass, device, clock, start, 6)
        await _value(hass, device, clock, start + 0.2, 100)
        await _value(hass, device, clock, start + 60.2, 0)
        # up, stopped at the top with the up key
        await _key(hass, device, clock, start + 100.0, 5)
        await _value(hass, device, clock, start + 100.2, 100)
        await _key(hass, device, clock, start + 148.4 + (0.4 if start > 3000 else 0.0), 5)
        await _value(hass, device, clock, start + 148.4 + (0.4 if start > 3000 else 0.0), 0)

    await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    await hass.async_block_till_done()
    assert entry.options["travel_up_ch1"] == pytest.approx(48.4, abs=0.05)
    assert "travel_down_ch1" not in entry.options

    await _wait_for(lambda: entry.runtime_data is not None and entry.runtime_data.websocket_listener)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
