"""Blind edge cases: long moves, refused actions, aborted calibration and scene storing."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    mock_restore_cache,
)

from custom_components.zeptrion_air.api import ZeptrionAirApiClientError
from custom_components.zeptrion_air.const import DOMAIN

from .test_cover import _eid, _key, _run_after_command, _setup, _value, _wait_for

TRAVEL = {"travel_up_ch1": 50.0, "travel_down_ch1": 50.0}
CLIENT = "custom_components.zeptrion_air.api.ZeptrionAirApiClient"


async def _at_bottom(hass, device, clock, at: float = 9000.0) -> None:
    await _key(hass, device, clock, at, 6)
    await _value(hass, device, clock, at + 0.1, 100)
    await _value(hass, device, clock, at + 50.1, 0)


async def _unload(hass, entry) -> None:
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_positions_outside_and_unknown(hass: HomeAssistant, fake_zapp, clock) -> None:
    device, host = fake_zapp
    entry = await _setup(hass, device, host, TRAVEL)
    cover = _eid(hass, "cover", f"zapp_{device.serial}_ch1")
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call("cover", "set_cover_position", {"entity_id": cover, "position": 50}, blocking=True)
    assert err.value.translation_key == "blind_position_unknown"
    await hass.services.async_call("cover", "set_cover_position", {"entity_id": cover, "position": 100}, blocking=True)
    assert device.commands[-1] == (1, "open")
    await hass.services.async_call("cover", "set_cover_position", {"entity_id": cover, "position": 0}, blocking=True)
    assert device.commands[-1] == (1, "close")
    await _unload(hass, entry)


async def test_long_move_is_stopped_by_timer(hass: HomeAssistant, fake_zapp, clock) -> None:
    """80 % of 50 s does not fit a timed command: open, then stop after 40 s."""
    device, host = fake_zapp
    entry = await _setup(hass, device, host, TRAVEL)
    cover = _eid(hass, "cover", f"zapp_{device.serial}_ch1")
    await _at_bottom(hass, device, clock)
    clock.now = 9100.0
    await hass.services.async_call("cover", "set_cover_position", {"entity_id": cover, "position": 80}, blocking=True)
    await hass.services.async_call("cover", "set_cover_position", {"entity_id": cover, "position": 75}, blocking=True)
    assert device.commands[-1] == (1, "open")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=45))
    await _wait_for(lambda: device.commands[-1] == (1, "stop"))
    await _unload(hass, entry)


async def test_refused_while_moving_or_calibrating(hass: HomeAssistant, fake_zapp, clock) -> None:
    device, host = fake_zapp
    entry = await _setup(hass, device, host, TRAVEL)
    cover = _eid(hass, "cover", f"zapp_{device.serial}_ch1")
    await _value(hass, device, clock, 9000.0, 100)  # moves, direction unknown
    for service, data, key in (
        ("blind_calibrate", {}, "blind_moving_calibrate"),
        ("blind_store_scene", {"scene": 1}, "blind_moving_store"),
    ):
        with pytest.raises(HomeAssistantError) as err:
            await hass.services.async_call(DOMAIN, service, {"entity_id": cover, **data}, blocking=True)
        assert err.value.translation_key == key
    await _value(hass, device, clock, 9010.0, 0)

    clock.now = 9100.0
    await hass.services.async_call(DOMAIN, "blind_calibrate", {"entity_id": cover}, blocking=True)
    for service, data, key in (
        ("blind_calibrate", {}, "calibration_running"),
        ("blind_store_scene", {"scene": 1}, "blind_busy"),
    ):
        with pytest.raises(HomeAssistantError) as err:
            await hass.services.async_call(DOMAIN, service, {"entity_id": cover, **data}, blocking=True)
        assert err.value.translation_key == key
    await _unload(hass, entry)


async def test_calibration_aborts(hass: HomeAssistant, fake_zapp, clock) -> None:
    """Runs too short, a run that never starts, a run that never ends."""
    device, host = fake_zapp
    entry = await _setup(hass, device, host, TRAVEL)
    cover = _eid(hass, "cover", f"zapp_{device.serial}_ch1")

    # too short
    clock.now = 9100.0
    await hass.services.async_call(DOMAIN, "blind_calibrate", {"entity_id": cover}, blocking=True)
    await _run_after_command(hass, device, clock, "open", 9100.5, 3.0)
    await _run_after_command(hass, device, clock, "close", 9104.0, 2.0)
    await _run_after_command(hass, device, clock, "open", 9107.0, 2.0)
    await hass.async_block_till_done()
    assert entry.options["travel_down_ch1"] == 50.0

    # the down run does not start
    with patch("custom_components.zeptrion_air.cover.CALIBRATION_START_TIMEOUT_S", 0.2):
        clock.now = 9200.0
        await hass.services.async_call(DOMAIN, "blind_calibrate", {"entity_id": cover}, blocking=True)
        await _run_after_command(hass, device, clock, "open", 9200.5, 30.0)
        await _wait_for(lambda: device.commands[-1] == (1, "close"))
        await _wait_for(lambda: "calibration" not in hass.states.get(cover).attributes)

    # the up run does not end
    with patch("custom_components.zeptrion_air.cover.CALIBRATION_RUN_TIMEOUT_S", 0.2):
        clock.now = 9300.0
        await hass.services.async_call(DOMAIN, "blind_calibrate", {"entity_id": cover}, blocking=True)
        await _wait_for(lambda: device.commands[-1] == (1, "open"))
        await _value(hass, device, clock, 9300.5, 100)
        await _wait_for(lambda: "calibration" not in hass.states.get(cover).attributes)
    await _value(hass, device, clock, 9400.0, 0)

    # the zApp refuses the command
    with patch(f"{CLIENT}.async_channel_open", AsyncMock(side_effect=ZeptrionAirApiClientError("x"))):
        await hass.services.async_call(DOMAIN, "blind_calibrate", {"entity_id": cover}, blocking=True)
        await _wait_for(lambda: "calibration" not in hass.states.get(cover).attributes)
    assert entry.options["travel_up_ch1"] == 50.0
    await _unload(hass, entry)


async def test_store_scene_from_top_fails(hass: HomeAssistant, fake_zapp, clock) -> None:
    device, host = fake_zapp
    entry = await _setup(hass, device, host, TRAVEL)
    cover = _eid(hass, "cover", f"zapp_{device.serial}_ch1")
    # at the top: stored directly; the zApp refuses
    await _key(hass, device, clock, 9000.0, 5)
    await _value(hass, device, clock, 9000.1, 100)
    await _value(hass, device, clock, 9050.1, 0)
    with patch(f"{CLIENT}.async_channel_store_scene", AsyncMock(side_effect=ZeptrionAirApiClientError("x"))):
        with pytest.raises(HomeAssistantError) as err:
            await hass.services.async_call(DOMAIN, "blind_store_scene", {"entity_id": cover, "scene": 2}, blocking=True)
    assert err.value.translation_key == "blind_store_scene_failed"

    # from the bottom: up to the top, back to the position; the blind does not stop in time
    await _at_bottom(hass, device, clock, 9100.0)
    with patch("custom_components.zeptrion_air.cover.CALIBRATION_RUN_TIMEOUT_S", 0.3):
        clock.now = 9200.0
        await hass.services.async_call(DOMAIN, "blind_store_scene", {"entity_id": cover, "scene": 2}, blocking=True)
        await _run_after_command(hass, device, clock, "open", 9200.5, 50.0)
        await _wait_for(lambda: "calibration" not in hass.states.get(cover).attributes)
    assert not any(cmd == "store_s2" for _ch, cmd in device.commands)
    await _unload(hass, entry)


async def test_restored_state_and_tilt_option(hass: HomeAssistant, fake_zapp, clock) -> None:
    device, host = fake_zapp
    er.async_get(hass).async_get_or_create(
        "cover", DOMAIN, f"zapp_{device.serial}_ch1", suggested_object_id="living_blind"
    )
    mock_restore_cache(hass, [State("cover.living_blind", "open", {
        "current_position": 60, "end_stop": "top", "current_tilt_position": 30,
    })])
    entry = await _setup(hass, device, host, {**TRAVEL, "tilt_time_ch1": 1.5})
    cover = _eid(hass, "cover", f"zapp_{device.serial}_ch1")
    assert hass.states.get(cover).attributes["current_position"] == 100  # end stop wins
    hass.config_entries.async_update_entry(entry, options={**TRAVEL, "tilt_time_ch1": 2.0})
    await hass.async_block_till_done()
    assert hass.states.get(cover).attributes["current_position"] == 100
    await hass.services.async_call(DOMAIN, "blind_recall_s3", {"entity_id": cover}, blocking=True)
    await hass.services.async_call(DOMAIN, "blind_recall_s4", {"entity_id": cover}, blocking=True)
    assert device.commands[-2:] == [(1, "recall_s3"), (1, "recall_s4")]
    await _unload(hass, entry)


async def test_missed_stop_found_by_channel_check(hass: HomeAssistant, fake_zapp, clock) -> None:
    device, host = fake_zapp
    entry = await _setup(hass, device, host, TRAVEL)
    cover = _eid(hass, "cover", f"zapp_{device.serial}_ch1")
    await _value(hass, device, clock, 9000.0, 100)
    await _value(hass, device, clock, 9000.1, 55)  # odd value: ignored
    # channel check fails once (still moving), then reads the stand still
    with patch(f"{CLIENT}.async_get_channel_scan_info", AsyncMock(side_effect=ZeptrionAirApiClientError("x"))):
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=120))
        await hass.async_block_till_done()
    device.values[1] = 0
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=300))
    await hass.async_block_till_done()
    await _wait_for(lambda: hass.states.get(cover).state not in ("opening", "closing"))
    await _unload(hass, entry)
