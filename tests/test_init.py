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

    # Counters: hidden diagnostic sensors, refreshed with the hub poll
    registry = er.async_get(hass)
    connects = _entity_id(hass, f"zapp_{device.serial}_connects", "sensor")
    short = _entity_id(hass, f"zapp_{device.serial}_short_outages", "sensor")
    long_ = _entity_id(hass, f"zapp_{device.serial}_long_outages", "sensor")
    assert registry.async_get(short).hidden_by == er.RegistryEntryHider.INTEGRATION
    assert registry.async_get(short).entity_category == "diagnostic"
    await runtime.coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(connects).state == "2"
    assert hass.states.get(short).state == "1"
    assert hass.states.get(long_).state == "0"

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
    assert runtime.long_outages == 1
    assert runtime.short_outages == 0

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


async def test_light_scenes_store_and_recall(hass: HomeAssistant, fake_zapp) -> None:
    device, host = fake_zapp
    device.channels[2]["cat"] = "3"  # dimmer
    entry = await _setup(hass, host, device.serial)
    await _wait_for(lambda: entry.runtime_data.websocket_listener.connected)
    light = _entity_id(hass, f"zapp_{device.serial}_ch2_light", "light")
    store_s1 = _entity_id(hass, f"zapp_{device.serial}_ch2_light_store_s1", "button")
    recall_s1 = _entity_id(hass, f"zapp_{device.serial}_ch2_light_recall_s1", "button")
    assert er.async_get(hass).async_get(store_s1).entity_category == "config"

    # S1 = dimmed to 40 %, S2 = off
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": light, "brightness": 102}, blocking=True
    )
    await hass.services.async_call("button", "press", {"entity_id": store_s1}, blocking=True)
    assert device.commands[-1] == (2, "store_s1")
    await hass.services.async_call("light", "turn_off", {"entity_id": light}, blocking=True)
    await hass.services.async_call(
        DOMAIN, "light_store_scene", {"entity_id": light, "scene": 2}, blocking=True
    )
    assert device.commands[-1] == (2, "store_s2")
    assert hass.states.get(light).attributes["scene_s1"] == 102
    assert hass.states.get(light).attributes["scene_s2"] == 0

    # Recall S1: on with the stored brightness, although the zApp reports 100
    await hass.services.async_call("button", "press", {"entity_id": recall_s1}, blocking=True)
    assert device.commands[-1] == (2, "recall_s1")
    await _wait_for(lambda: hass.states.get(light).state == "on")
    await asyncio.sleep(0.1)
    assert hass.states.get(light).attributes["brightness"] == 102

    # Recall S2: off
    await hass.services.async_call(
        DOMAIN, "light_recall_scene", {"entity_id": light, "scene": 2}, blocking=True
    )
    assert device.commands[-1] == (2, "recall_s2")
    await _wait_for(lambda: hass.states.get(light).state == "off")

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_migration_drops_light_polling_and_rooms_become_areas(
    hass: HomeAssistant, fake_zapp
) -> None:
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr

    device, host = fake_zapp
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"zapp-{device.serial}",
        unique_id=device.serial,
        data={CONF_HOSTNAME: host},
        options={"light_polling": True, "step_duration_ms": 100},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.minor_version == 4
    assert entry.options == {"step_duration_ms": 100}

    # chdes group "Living" of both channels: suggested for hub and channel devices
    area = ar.async_get(hass).async_get_area_by_name("Living")
    assert area is not None
    registry = dr.async_get(hass)
    hub = registry.async_get_device(identifiers={(DOMAIN, device.serial)})
    blind = registry.async_get_device(identifiers={(DOMAIN, f"{device.serial}_ch1")})
    assert hub.area_id == area.id
    assert blind.area_id == area.id
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_wall_keys_of_older_button_face(hass: HomeAssistant, fake_zapp) -> None:
    """No /zapi/smartfront/id (older face): keys are learnt from the first press."""
    device, host = fake_zapp
    device.btfu = None
    entry = await _setup(hass, host, device.serial)
    runtime = entry.runtime_data
    await _wait_for(lambda: runtime.websocket_listener and runtime.websocket_listener.connected)
    registry = er.async_get(hass)
    unique_id = f"zapp_{device.serial}_wall_keys"
    assert registry.async_get_entity_id("event", DOMAIN, unique_id) is None

    # First press creates the entity and is reported as event
    await device.press(4)
    await _wait_for(lambda: registry.async_get_entity_id("event", DOMAIN, unique_id) is not None)
    keys = registry.async_get_entity_id("event", DOMAIN, unique_id)
    await _wait_for(
        lambda: hass.states.get(keys) is not None
        and hass.states.get(keys).attributes.get("event_type") == "key_4_release"
    )
    attrs = hass.states.get(keys).attributes
    assert attrs["keys_learnt"] is True
    assert attrs["function"] == "unknown"
    assert "key_9_press" in attrs["event_types"]  # bta has 9 characters
    assert entry.data["legacy_keys"] == 9

    # Next press goes the normal way
    await device.press(2)
    await _wait_for(lambda: hass.states.get(keys).attributes.get("event_type") == "key_2_release")

    # After a reload the entity is there before any press
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(keys) is not None
    assert "key_1_press" in hass.states.get(keys).attributes["event_types"]

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_entity_names(hass: HomeAssistant, fake_zapp) -> None:
    """Names from the channel device and from the translations, as before the switch to translation keys."""
    device, host = fake_zapp
    entry = await _setup(hass, host, device.serial)
    serial = device.serial

    def name(domain: str, unique_id: str) -> str:
        return hass.states.get(_entity_id(hass, unique_id, domain)).name

    # blind and light take the name of their channel device, without a device class suffix
    from homeassistant.helpers import device_registry as dr

    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    for domain, unique_id in (("cover", f"zapp_{serial}_ch1"), ("light", f"zapp_{serial}_ch2_light")):
        reg = registry.async_get(_entity_id(hass, unique_id, domain))
        assert name(domain, unique_id) == devices.async_get(reg.device_id).name
    blind = name("cover", f"zapp_{serial}_ch1")
    light = name("light", f"zapp_{serial}_ch2_light")
    assert "Blind" in blind and not blind.endswith(("Shutter", "Rollladen"))
    assert registry.async_get(_entity_id(hass, f"zapp_{serial}_ch1", "cover")).has_entity_name
    # buttons and sensors: channel device name + translated name
    assert name("button", f"zapp_{serial}_ch1_blind_recall_s1") == f"{blind} Scene S1"
    assert name("button", f"zapp_{serial}_ch1_blind_store_s2") == f"{blind} Store S2"
    assert name("button", f"zapp_{serial}_ch1_blind_calibrate") == f"{blind} Calibrate"
    assert name("button", f"zapp_{serial}_ch2_light_recall_s3") == f"{light} Scene S3"
    group = registry.async_get(_entity_id(hass, f"zapp_{serial}_ch1_group", "sensor"))
    assert group.translation_key == "channel_group" and group.disabled_by is not None  # off by default
    assert name("sensor", f"zapp_{serial}_rssi").endswith("Wi-Fi Signal")

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_wall_keys_edge_cases(hass: HomeAssistant, fake_zapp) -> None:
    """Unused keys are not reported; more learnt keys extend the event types."""
    from homeassistant.helpers.dispatcher import async_dispatcher_send

    from custom_components.zeptrion_air.const import SIGNAL_LEGACY_KEYS
    from custom_components.zeptrion_air.smartfront import KeyEvent

    device, host = fake_zapp
    device.btfu = None
    entry = await _setup(hass, host, device.serial)
    registry = er.async_get(hass)
    unique_id = f"zapp_{device.serial}_wall_keys"
    async_dispatcher_send(
        hass, SIGNAL_LEGACY_KEYS.format(entry.entry_id), 4, KeyEvent(key=2, pressed=True, function=None)
    )
    await hass.async_block_till_done()
    keys = registry.async_get_entity_id("event", DOMAIN, unique_id)
    async_dispatcher_send(
        hass, SIGNAL_LEGACY_KEYS.format(entry.entry_id), 6, KeyEvent(key=6, pressed=True, function=None)
    )
    await hass.async_block_till_done()
    assert hass.states.get(keys).attributes["event_type"] == "key_6_press"
    entity = hass.data["entity_components"]["event"].get_entity(keys)
    entity.set_keys(list(entity._keys.values()))  # no pending event
    entity._handle_key(KeyEvent(key=9, pressed=True, function=None))  # unknown key: ignored
    assert hass.states.get(keys).attributes["event_type"] == "key_6_press"
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_smartfront_without_used_keys(hass: HomeAssistant, fake_zapp) -> None:
    device, host = fake_zapp
    device.btfu = "0,0,0,0,0,0,0,0"
    entry = await _setup(hass, host, device.serial)
    assert er.async_get(hass).async_get_entity_id("event", DOMAIN, f"zapp_{device.serial}_wall_keys") is None
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
