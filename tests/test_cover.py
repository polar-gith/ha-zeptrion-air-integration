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
    # the unstopped full runs down were taken over from the zApp automatically
    assert entry.options["travel_down_ch1"] == 60.0

    await _wait_for(lambda: entry.runtime_data is not None and entry.runtime_data.websocket_listener)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_learning_result_transferred_to_all_blinds(hass: HomeAssistant, fake_zapp, clock) -> None:
    device, host = fake_zapp
    device.channels[2] = {"name": "Blind 2", "group": "Living", "icon": "1443", "type": "00", "cat": "5"}
    entry = await _setup(hass, device, host)
    learning = _eid(hass, "switch", f"zapp_{device.serial}_blind_learning")
    apply_all = _eid(hass, "switch", f"zapp_{device.serial}_blind_learning_apply_all")

    await hass.services.async_call("switch", "turn_on", {"entity_id": apply_all}, blocking=True)
    await hass.services.async_call("switch", "turn_on", {"entity_id": learning}, blocking=True)

    # only blind 1 is measured: down full run, then up/down stopped by hand, twice
    t = 5000.0
    await _key(hass, device, clock, t, 6)
    await _value(hass, device, clock, t + 0.2, 100)
    await _value(hass, device, clock, t + 60.2, 0)
    for up_s, down_s in ((48.2, 41.0), (48.6, 41.4)):
        t += 100
        await _key(hass, device, clock, t, 5)
        await _value(hass, device, clock, t + 0.2, 100)
        await _key(hass, device, clock, t + 0.2 + up_s, 5)
        await _value(hass, device, clock, t + 0.2 + up_s, 0)
        t += 100
        await _key(hass, device, clock, t, 6)
        await _value(hass, device, clock, t + 0.2, 100)
        await _key(hass, device, clock, t + 0.2 + down_s, 6)
        await _value(hass, device, clock, t + 0.2 + down_s, 0)

    await hass.services.async_call("switch", "turn_off", {"entity_id": learning}, blocking=True)
    await hass.async_block_till_done()
    assert entry.options["travel_up_ch1"] == pytest.approx(48.4, abs=0.05)
    assert entry.options["travel_down_ch1"] == pytest.approx(41.2, abs=0.05)
    assert entry.options["travel_up_ch2"] == pytest.approx(48.4, abs=0.05)
    assert entry.options["travel_down_ch2"] == pytest.approx(41.2, abs=0.05)

    await _wait_for(lambda: entry.runtime_data is not None and entry.runtime_data.websocket_listener)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_reversing_with_opposite_key_keeps_direction(hass: HomeAssistant, fake_zapp, clock) -> None:
    """Seen on a real zApp: key up while moving down -> 0, then 100 for the up run."""
    device, host = fake_zapp
    entry = await _setup(hass, device, host)
    cover = _eid(hass, "cover", f"zapp_{device.serial}_ch1")

    await _key(hass, device, clock, 7000.0, 6)
    await _value(hass, device, clock, 7000.2, 100)
    await _key(hass, device, clock, 7059.0, 5)  # reverse
    await _value(hass, device, clock, 7059.2, 0)
    await _value(hass, device, clock, 7059.4, 100)
    assert hass.states.get(cover).state == "opening"
    await _value(hass, device, clock, 7120.0, 0)  # ends by itself
    assert hass.states.get(cover).attributes["end_stop"] == "top"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_travel_times_taken_over_from_full_runs(hass: HomeAssistant, fake_zapp, clock) -> None:
    device, host = fake_zapp
    entry = await _setup(hass, device, host)
    cover = _eid(hass, "cover", f"zapp_{device.serial}_ch1")
    runtime = entry.runtime_data

    # full run down (start unknown): end stop bottom, no sample yet
    await _key(hass, device, clock, 8000.0, 6)
    await _value(hass, device, clock, 8000.2, 100)
    await _value(hass, device, clock, 8059.2, 0)
    assert "travel_down_ch1" not in entry.options
    # full run up from the bottom, ended by the zApp: travel time up taken over
    await _key(hass, device, clock, 8100.0, 5)
    await _value(hass, device, clock, 8100.2, 100)
    await _value(hass, device, clock, 8161.2, 0)
    await hass.async_block_till_done()
    assert entry.options["travel_up_ch1"] == 61.0
    # and down
    await _key(hass, device, clock, 8200.0, 6)
    await _value(hass, device, clock, 8200.2, 100)
    await _value(hass, device, clock, 8259.3, 0)
    await hass.async_block_till_done()
    assert entry.options["travel_down_ch1"] == 59.1

    # taken over live, without reloading the hub
    assert entry.runtime_data is runtime
    state = hass.states.get(cover)
    assert state.attributes["travel_time_up"] == 61.0
    assert state.attributes["current_position"] == 0

    # a single deviating run does not change a known value
    await _key(hass, device, clock, 8300.0, 5)
    await _value(hass, device, clock, 8300.2, 100)
    await _value(hass, device, clock, 8363.2, 0)
    await hass.async_block_till_done()
    assert entry.options["travel_up_ch1"] == 61.0

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_tilt_time_option(hass: HomeAssistant, fake_zapp, clock) -> None:
    device, host = fake_zapp
    entry = await _setup(
        hass, device, host,
        {"travel_up_ch1": 61.0, "travel_down_ch1": 59.0, "tilt_time_ch1": 1.2},
    )
    cover = _eid(hass, "cover", f"zapp_{device.serial}_ch1")

    # full run down: closed, slats closed
    await _key(hass, device, clock, 9000.0, 6)
    await _value(hass, device, clock, 9000.2, 100)
    await _value(hass, device, clock, 9059.2, 0)
    state = hass.states.get(cover)
    assert state.attributes["current_position"] == 0
    assert state.attributes["current_tilt_position"] == 0

    # tilt button: one step (default step duration 250 ms) up
    clock.now = 9090.0
    await hass.services.async_call("cover", "open_cover_tilt", {"entity_id": cover}, blocking=True)
    assert device.commands[-1] == (1, "move_open_250")
    await _value(hass, device, clock, 9090.1, 100)
    await _value(hass, device, clock, 9090.4, 0)
    state = hass.states.get(cover)
    assert 0 < state.attributes["current_tilt_position"] < 100
    assert state.attributes["current_position"] == 0

    # step back down, then open the slats fully: tilt position 100
    clock.now = 9095.0
    await hass.services.async_call("cover", "close_cover_tilt", {"entity_id": cover}, blocking=True)
    assert device.commands[-1] == (1, "move_close_250")
    await _value(hass, device, clock, 9095.1, 100)
    await _value(hass, device, clock, 9095.4, 0)
    clock.now = 9100.0
    await hass.services.async_call(
        "cover", "set_cover_tilt_position", {"entity_id": cover, "tilt_position": 100}, blocking=True
    )
    assert device.commands[-1] == (1, "move_open_1200")
    await _value(hass, device, clock, 9100.1, 100)
    await _value(hass, device, clock, 9101.3, 0)
    state = hass.states.get(cover)
    assert state.attributes["current_tilt_position"] == 100
    assert state.attributes["current_position"] == 0

    # set position 50: no slat turn needed any more
    clock.now = 9200.0
    await hass.services.async_call(
        "cover", "set_cover_position", {"entity_id": cover, "position": 50}, blocking=True
    )
    assert device.commands[-1] == (1, f"move_open_{round(0.5 * (61.0 - 1.2) * 1000)}")

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_missed_stop_is_recovered(hass: HomeAssistant, fake_zapp, clock) -> None:
    from datetime import timedelta

    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    device, host = fake_zapp
    entry = await _setup(hass, device, host, {"travel_up_ch1": 61.0, "travel_down_ch1": 59.0})
    cover = _eid(hass, "cover", f"zapp_{device.serial}_ch1")

    await _key(hass, device, clock, 10000.0, 5)
    await _value(hass, device, clock, 10000.2, 100)
    assert hass.states.get(cover).state == "opening"
    # the "0" never arrives; the channel stands
    device.values[1] = 0
    clock.now = 10080.0
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=80))
    await hass.async_block_till_done()
    await _wait_for(lambda: hass.states.get(cover).state != "opening")
    assert hass.states.get(cover).state == "open"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_renamed_blind_is_re_added_with_services(hass: HomeAssistant, fake_zapp, clock) -> None:
    """Renaming the entity_id re-adds the entity outside platform setup."""
    device, host = fake_zapp
    entry = await _setup(hass, device, host, {"travel_up_ch1": 61.0, "travel_down_ch1": 59.0})
    old = _eid(hass, "cover", f"zapp_{device.serial}_ch1")
    er.async_get(hass).async_update_entity(old, new_entity_id="cover.renamed_blind")
    await hass.async_block_till_done()
    assert hass.states.get("cover.renamed_blind") is not None

    await hass.services.async_call(
        DOMAIN, "blind_recall_s1", {"entity_id": "cover.renamed_blind"}, blocking=True
    )
    assert device.commands[-1][1] == "recall_s1"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_store_scene_by_action_and_button(hass: HomeAssistant, fake_zapp, clock) -> None:
    """S1-S4 can be stored from HA; a moving blind is refused."""
    from homeassistant.exceptions import HomeAssistantError

    device, host = fake_zapp
    entry = await _setup(hass, device, host, {"travel_up_ch1": 61.0, "travel_down_ch1": 59.0})
    cover = _eid(hass, "cover", f"zapp_{device.serial}_ch1")
    button = _eid(hass, "button", f"zapp_{device.serial}_ch1_blind_store_s2")
    assert er.async_get(hass).async_get(button).entity_category == "config"

    await hass.services.async_call(
        DOMAIN, "blind_store_scene", {"entity_id": cover, "scene": 3}, blocking=True
    )
    assert device.commands[-1] == (1, "store_s3")

    await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
    assert device.commands[-1] == (1, "store_s2")

    # while the blind moves nothing is stored
    await _key(hass, device, clock, 1000.0, 5)
    await _value(hass, device, clock, 1000.1, 100)
    sent = len(device.commands)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
    assert len(device.commands) == sent

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def _run_after_command(hass, device, clock, command: str, start: float, seconds: float) -> None:
    """Wait for the command, then simulate a run that the zApp ends by itself."""
    await _wait_for(lambda: device.commands and device.commands[-1] == (1, command))
    device.commands.append((0, "seen"))  # marker, so the next wait sees a new command
    await _value(hass, device, clock, start, 100)
    await _value(hass, device, clock, start + seconds, 0)


async def test_calibration_run_measures_travel_times(hass: HomeAssistant, fake_zapp, clock) -> None:
    device, host = fake_zapp
    entry = await _setup(hass, device, host, {"travel_up_ch1": 50.0, "travel_down_ch1": 50.0})
    cover = _eid(hass, "cover", f"zapp_{device.serial}_ch1")
    button = _eid(hass, "button", f"zapp_{device.serial}_ch1_blind_calibrate")
    assert er.async_get(hass).async_get(button).entity_category == "config"

    # known position 40 % before calibrating
    await _key(hass, device, clock, 9000.0, 6)
    await _value(hass, device, clock, 9000.1, 100)
    await _value(hass, device, clock, 9050.1, 0)  # bottom
    await hass.services.async_call("cover", "set_cover_position", {"entity_id": cover, "position": 40}, blocking=True)
    await _value(hass, device, clock, 9060.0, 100)
    await _value(hass, device, clock, 9080.0, 0)
    assert hass.states.get(cover).attributes["current_position"] == 40

    clock.now = 9100.0
    await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
    await _run_after_command(hass, device, clock, "open", 9100.5, 30.0)
    await _wait_for(lambda: hass.states.get(cover).attributes.get("calibration", "").startswith("2/3"))
    await _run_after_command(hass, device, clock, "close", 9131.0, 59.2)
    await _run_after_command(hass, device, clock, "open", 9191.0, 61.4)
    await hass.async_block_till_done()

    assert entry.options["travel_down_ch1"] == 59.2
    assert entry.options["travel_up_ch1"] == 61.4
    state = hass.states.get(cover)
    assert "calibration" not in state.attributes
    # back to 40 %: a timed move down of 60 % of 59.2 s does not fit a timed command
    await _wait_for(lambda: device.commands[-1][1] == "close")
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_calibration_aborted_when_stopped(hass: HomeAssistant, fake_zapp, clock) -> None:
    device, host = fake_zapp
    entry = await _setup(hass, device, host, {"travel_up_ch1": 50.0, "travel_down_ch1": 50.0})
    cover = _eid(hass, "cover", f"zapp_{device.serial}_ch1")

    clock.now = 9100.0
    await hass.services.async_call(DOMAIN, "blind_calibrate", {"entity_id": cover}, blocking=True)
    await _run_after_command(hass, device, clock, "open", 9100.5, 30.0)
    await _wait_for(lambda: device.commands[-1] == (1, "close"))
    await _value(hass, device, clock, 9131.0, 100)
    await hass.services.async_call("cover", "stop_cover", {"entity_id": cover}, blocking=True)
    await _value(hass, device, clock, 9140.0, 0)
    await hass.async_block_till_done()

    assert entry.options["travel_down_ch1"] == 50.0
    assert "calibration" not in hass.states.get(cover).attributes
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
