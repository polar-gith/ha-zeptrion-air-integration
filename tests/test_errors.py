"""Error paths: failing zApp commands, invalid actions, failing setup and updates."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.zeptrion_air.api import (
    ZeptrionAirApiClientCommunicationError,
    ZeptrionAirApiClientError,
)
from custom_components.zeptrion_air.const import CONF_HOSTNAME, DOMAIN

CLIENT = "custom_components.zeptrion_air.api.ZeptrionAirApiClient"


async def _setup(hass: HomeAssistant, device, host: str) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, title=f"zapp-{device.serial}", unique_id=device.serial,
        minor_version=4, data={CONF_HOSTNAME: host},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _eid(hass: HomeAssistant, domain: str, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)
    assert entity_id, unique_id
    return entity_id


async def _unload(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


def _boom(*_args, **_kwargs):
    raise ZeptrionAirApiClientCommunicationError("zApp gone")


async def test_light_commands_fail_with_translated_error(hass: HomeAssistant, fake_zapp) -> None:
    device, host = fake_zapp
    entry = await _setup(hass, device, host)
    light = _eid(hass, "light", f"zapp_{device.serial}_ch2_light")
    calls = [
        ("light", "turn_on", {}, "async_channel_on", "light_command_failed"),
        ("light", "turn_off", {}, "async_channel_off", "light_command_failed"),
        (DOMAIN, "light_store_scene", {"scene": 1}, "async_channel_store_scene", "light_store_scene_failed"),
        (DOMAIN, "light_recall_scene", {"scene": 1}, "async_channel_recall_s", "light_recall_scene_failed"),
    ]
    for domain, service, data, method, key in calls:
        with patch(f"{CLIENT}.{method}", AsyncMock(side_effect=_boom)):
            with pytest.raises(HomeAssistantError) as err:
                await hass.services.async_call(
                    domain, service, {"entity_id": light, **data}, blocking=True
                )
        assert err.value.translation_key == key, service
        assert err.value.translation_placeholders["entity"] == hass.states.get(light).name
    await _unload(hass, entry)


async def test_write_name_errors(hass: HomeAssistant, fake_zapp) -> None:
    device, host = fake_zapp
    entry = await _setup(hass, device, host)
    light = _eid(hass, "light", f"zapp_{device.serial}_ch2_light")
    with patch(f"{CLIENT}.async_set_channel_descriptor", AsyncMock(side_effect=_boom)):
        with pytest.raises(HomeAssistantError) as err:
            await hass.services.async_call(
                DOMAIN, "write_name_to_zapp", {"entity_id": light}, blocking=True
            )
    assert err.value.translation_key == "write_name_failed"
    # an entity of another integration is not written to
    hass.states.async_set("light.other", "on")
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, "write_name_to_zapp", {"entity_id": "light.other"}, blocking=True
        )
    assert err.value.translation_key == "nothing_selected"
    await _unload(hass, entry)


async def test_program_smartbutton_validation(hass: HomeAssistant, fake_zapp) -> None:
    device, host = fake_zapp
    entry = await _setup(hass, device, host)
    hub = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, device.serial)})
    light = _eid(hass, "light", f"zapp_{device.serial}_ch2_light")

    async def call(data):
        with pytest.raises(ServiceValidationError) as err:
            await hass.services.async_call(DOMAIN, "program_smartbutton", data, blocking=True)
        return err.value.translation_key

    assert await call({"device_id": "nope", "actions": [{"entity_id": light, "command": "on"}]}) == "device_not_found"
    other = MockConfigEntry(domain="demo")
    other.add_to_hass(hass)
    foreign = dr.async_get(hass).async_get_or_create(
        config_entry_id=other.entry_id, identifiers={("demo", "x")}, name="Foreign"
    )
    assert await call({"device_id": foreign.id, "actions": [{"entity_id": light, "command": "on"}]}) == "not_a_loaded_zapp"
    hass.states.async_set("light.other", "on")
    assert await call({"device_id": hub.id, "actions": [{"entity_id": "light.other", "command": "on"}]}) == "not_a_zeptrion_entity"
    sensor = _eid(hass, "sensor", f"zapp_{device.serial}_ch2_name")
    assert await call({"device_id": hub.id, "actions": [{"entity_id": sensor, "command": "on"}]}) == "not_a_zeptrion_entity"
    too_long = [{"req": "POST", "loc": "x", "pth": "/", "bdy": "x" * 600}]
    assert await call({"device_id": hub.id, "requests": too_long}) == "smartbutton_invalid"
    with pytest.raises(Exception):
        await hass.services.async_call(
            DOMAIN, "program_smartbutton",
            {"device_id": hub.id, "actions": [{"entity_id": light, "command": "dance"}]},
            blocking=True,
        )
    # a timed command within 100-32000 ms is accepted by the schema
    from custom_components.zeptrion_air.services import valid_command as _command

    assert _command("move_open_250") == "move_open_250"
    with pytest.raises(Exception):
        _command("move_open_50")
    await _unload(hass, entry)


async def test_program_smartbutton_without_smartbutton(hass: HomeAssistant, fake_zapp) -> None:
    device, host = fake_zapp
    device.btfu = None  # no Smartfront fitted
    entry = await _setup(hass, device, host)
    hub = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, device.serial)})
    light = _eid(hass, "light", f"zapp_{device.serial}_ch2_light")
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, "program_smartbutton",
            {"device_id": hub.id, "actions": [{"entity_id": light, "command": "on"}]},
            blocking=True,
        )
    assert err.value.translation_key == "no_smartbutton"
    await _unload(hass, entry)


async def test_cover_commands_fail_with_translated_error(hass: HomeAssistant, fake_zapp) -> None:
    device, host = fake_zapp
    entry = await _setup(hass, device, host)
    cover = _eid(hass, "cover", f"zapp_{device.serial}_ch1")
    calls = [
        ("cover", "open_cover", {}, "async_channel_open"),
        ("cover", "close_cover", {}, "async_channel_close"),
        ("cover", "stop_cover", {}, "async_channel_stop"),
        (DOMAIN, "blind_recall_s1", {}, "async_channel_recall_s1"),
    ]
    for domain, service, data, method in calls:
        with patch(f"{CLIENT}.{method}", AsyncMock(side_effect=_boom)):
            with pytest.raises(HomeAssistantError) as err:
                await hass.services.async_call(
                    domain, service, {"entity_id": cover, **data}, blocking=True
                )
        assert err.value.translation_key in (
            "blind_command_failed", "blind_recall_scene_failed"
        ), service
        await hass.async_block_till_done()
    await _unload(hass, entry)


@pytest.mark.parametrize(
    ("identification", "expected"),
    [
        ({}, ConfigEntryState.SETUP_ERROR),
        ({"id": {}}, ConfigEntryState.SETUP_ERROR),
        ({"id": {"hw": "1"}}, ConfigEntryState.SETUP_ERROR),
    ],
)
async def test_setup_with_bad_identification(
    hass: HomeAssistant, fake_zapp, identification, expected
) -> None:
    device, host = fake_zapp
    entry = MockConfigEntry(
        domain=DOMAIN, title="zapp-x", unique_id=device.serial, minor_version=4,
        data={CONF_HOSTNAME: host},
    )
    entry.add_to_hass(hass)
    with patch(f"{CLIENT}.async_get_device_identification", AsyncMock(return_value=identification)):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is expected


async def test_setup_survives_failing_rssi(hass: HomeAssistant, fake_zapp) -> None:
    device, host = fake_zapp
    for error in (
        ZeptrionAirApiClientCommunicationError("down"),
        ZeptrionAirApiClientError("bad"),
        RuntimeError("odd"),
    ):
        with patch(f"{CLIENT}.async_get_rssi", AsyncMock(side_effect=error)):
            entry = await _setup(hass, device, host)
        assert entry.state is ConfigEntryState.LOADED
        await _unload(hass, entry)
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()


async def test_setup_unexpected_error(hass: HomeAssistant, fake_zapp) -> None:
    device, host = fake_zapp
    entry = MockConfigEntry(
        domain=DOMAIN, title="zapp-x", unique_id=device.serial, minor_version=4,
        data={CONF_HOSTNAME: host},
    )
    entry.add_to_hass(hass)
    with patch(f"{CLIENT}.async_get_channel_descriptions", AsyncMock(side_effect=RuntimeError("odd"))):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_coordinator_update_fails_and_recovers(hass: HomeAssistant, fake_zapp) -> None:
    device, host = fake_zapp
    entry = await _setup(hass, device, host)
    rssi = _eid(hass, "sensor", f"zapp_{device.serial}_rssi")
    er.async_get(hass).async_update_entity(rssi, disabled_by=None)
    coordinator = entry.runtime_data.coordinator
    with patch(f"{CLIENT}.async_get_device_identification", AsyncMock(side_effect=_boom)):
        await coordinator.async_refresh()
    assert coordinator.last_update_success is False
    with patch(f"{CLIENT}.async_get_smartfront_sensors", AsyncMock(side_effect=ZeptrionAirApiClientError("x"))):
        await coordinator.async_refresh()
    assert coordinator.last_update_success is True
    assert coordinator.data["smartfront_sensors"] is None
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=2))
    await hass.async_block_till_done()
    await _unload(hass, entry)


async def test_buttons_without_their_channel_entity(hass: HomeAssistant, fake_zapp) -> None:
    """Disabled blind or light: scene buttons talk to the zApp directly."""
    device, host = fake_zapp
    entry = MockConfigEntry(
        domain=DOMAIN, title=f"zapp-{device.serial}", unique_id=device.serial,
        minor_version=4, data={CONF_HOSTNAME: host},
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    for domain, uid in (("cover", "ch1"), ("light", "ch2_light")):
        registry.async_get_or_create(
            domain, DOMAIN, f"zapp_{device.serial}_{uid}", config_entry=entry,
            disabled_by=er.RegistryEntryDisabler.USER,
        )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    async def press(uid: str) -> None:
        button = _eid(hass, "button", f"zapp_{device.serial}_{uid}")
        await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)

    for n in range(1, 5):
        await press(f"ch1_blind_recall_s{n}")
        assert device.commands[-1] == (1, f"recall_s{n}")
    await press("ch1_blind_store_s1")
    assert device.commands[-1] == (1, "store_s1")
    for uid in ("ch1_blind_calibrate", "ch2_light_store_s1"):
        with pytest.raises(HomeAssistantError) as err:
            await press(uid)
        assert err.value.translation_key == "channel_entity_not_found"
    with patch(f"{CLIENT}.async_channel_recall_s1", AsyncMock(side_effect=_boom)):
        with pytest.raises(HomeAssistantError) as err:
            await press("ch1_blind_recall_s1")
    assert err.value.translation_key == "button_failed"
    with patch(f"{CLIENT}.async_channel_recall_s2", AsyncMock(side_effect=RuntimeError("odd"))):
        with pytest.raises(HomeAssistantError) as err:
            await press("ch1_blind_recall_s2")
    assert err.value.translation_key == "button_failed"
    await _unload(hass, entry)


async def test_rssi_sensor_values(hass: HomeAssistant, fake_zapp) -> None:
    device, host = fake_zapp
    entry = await _setup(hass, device, host)
    from custom_components.zeptrion_air.sensor import ZeptrionAirRssiSensor

    sensor = ZeptrionAirRssiSensor(
        coordinator=entry.runtime_data.coordinator,
        hub_device_info={"identifiers": {(DOMAIN, device.serial)}},
        hub_serial=device.serial, hub_name="zapp", hub_name_slug="zapp",
    )
    coordinator = entry.runtime_data.coordinator
    for data, expected in ((None, None), ({}, None), ({"rssi_dbm": None}, None),
                           ({"rssi_dbm": "weak"}, None), ({"rssi_dbm": "-70"}, -70)):
        coordinator.data = data
        sensor._handle_coordinator_update()
        assert sensor.native_value == expected
    await _unload(hass, entry)


async def test_odd_channel_descriptions(hass: HomeAssistant, fake_zapp) -> None:
    """Channels without category, with odd values, or without names."""
    device, host = fake_zapp
    device.channels = {
        1: {"name": "", "group": "Living", "icon": "1443", "type": "00", "cat": "5"},
        2: {"name": "", "group": "", "icon": "1492", "type": "00", "cat": "1"},
        3: {"name": "Fan", "group": "Living", "icon": "1", "type": "00", "cat": "9"},
        4: {"name": "Odd", "group": "Living", "icon": "1", "type": "00", "cat": "x"},
        5: {"name": "None", "group": "Living", "icon": "1", "type": "00"},
    }
    device.values = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    with patch(f"{CLIENT}.async_get_smartfront_sensors", AsyncMock(side_effect=ZeptrionAirApiClientError("x"))):
        entry = await _setup(hass, device, host)
    devices = dr.async_get(hass)
    assert devices.async_get_device(identifiers={(DOMAIN, f"{device.serial}_ch1")}).name == "Living"
    assert devices.async_get_device(identifiers={(DOMAIN, f"{device.serial}_ch2")}).name == "Channel 2"
    for n in (3, 4, 5):
        assert devices.async_get_device(identifiers={(DOMAIN, f"{device.serial}_ch{n}")}) is None
    await _unload(hass, entry)


async def test_future_version_and_changed_serial(hass: HomeAssistant, fake_zapp) -> None:
    device, host = fake_zapp
    future = MockConfigEntry(domain=DOMAIN, version=2, unique_id="other", data={CONF_HOSTNAME: host})
    future.add_to_hass(hass)
    await hass.config_entries.async_setup(future.entry_id)
    await hass.async_block_till_done()
    assert future.state is ConfigEntryState.MIGRATION_ERROR

    entry = MockConfigEntry(
        domain=DOMAIN, title="zapp-x", unique_id="12345678", minor_version=4,
        data={CONF_HOSTNAME: host},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED  # warns, uses the serial of the zApp
    await _unload(hass, entry)


async def test_watchdog_restarts_the_event_stream(hass: HomeAssistant, fake_zapp) -> None:
    device, host = fake_zapp
    entry = await _setup(hass, device, host)
    listener = entry.runtime_data.websocket_listener
    with patch.object(listener, "is_alive", return_value=False), patch.object(
        listener, "start", AsyncMock()
    ) as start:
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=10))
        await hass.async_block_till_done()
    assert start.called
    await _unload(hass, entry)


async def test_smartfront_led_errors(hass: HomeAssistant, fake_zapp) -> None:
    from custom_components.zeptrion_air.leds import LedState, parse_hex

    assert parse_hex("zz") == (0, 0, 0)
    assert LedState(rgb=(0, 0, 0), brightness=1, is_on=True).to_bg() == "#010101"
    device, host = fake_zapp
    with patch(f"{CLIENT}.async_get_leds", AsyncMock(side_effect=ZeptrionAirApiClientError("x"))):
        entry = await _setup(hass, device, host)
    leds = entry.runtime_data.leds
    led = leds.leds[0]
    await leds.async_set(led, on=False)  # unchanged: nothing sent
    assert not leds.can_undo
    assert await leds.async_undo() is False
    with patch(f"{CLIENT}.async_set_led_backgrounds", AsyncMock(side_effect=_boom)):
        with pytest.raises(HomeAssistantError) as err:
            await leds.async_set(led, on=True)
    assert err.value.translation_key == "led_failed"
    await leds.async_set(led, on=True)
    with patch(f"{CLIENT}.async_set_led_backgrounds", AsyncMock(side_effect=_boom)):
        with pytest.raises(HomeAssistantError) as err:
            await leds.async_undo()
    assert err.value.translation_key == "led_undo_failed"
    await _unload(hass, entry)


async def test_coordinator_without_runtime_data(hass: HomeAssistant, fake_zapp) -> None:
    from homeassistant.helpers.update_coordinator import UpdateFailed

    device, host = fake_zapp
    entry = await _setup(hass, device, host)
    coordinator = entry.runtime_data.coordinator
    runtime, entry.runtime_data = entry.runtime_data, None
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    entry.runtime_data = runtime
    client, coordinator.client = coordinator.client, None
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    coordinator.client = client
    await _unload(hass, entry)


async def test_light_ignores_foreign_and_odd_pushes(hass: HomeAssistant, fake_zapp) -> None:
    from homeassistant.core import Event

    device, host = fake_zapp
    device.channels[2]["cat"] = "3"  # dimmer
    entry = await _setup(hass, device, host)
    light_id = _eid(hass, "light", f"zapp_{device.serial}_ch2_light")
    light = hass.data["entity_components"]["light"].get_entity(light_id)
    base = {"channel": 2, "hub_unique_id": device.serial, "source": "eid1", "value": 40}
    for change in ({"channel": 1}, {"hub_unique_id": "x"}, {"source": "eid2"}, {"value": None}, {"value": "dim"}):
        await light.async_handle_websocket_message(Event("x", {**base, **change}))
    assert hass.states.get(light_id).state == "off"
    await light.async_handle_websocket_message(Event("x", base))
    assert hass.states.get(light_id).attributes["brightness"] == 102
    assert light._set_state_from_api_percent(-1) is False
    with patch(f"{CLIENT}.async_get_channel_scan_info", AsyncMock(return_value={"chscan": {}})):
        await light._async_refresh_from_scan()
    with patch(f"{CLIENT}.async_get_channel_scan_info", AsyncMock(side_effect=_boom)):
        await light._async_refresh_from_scan()
    await _unload(hass, entry)
