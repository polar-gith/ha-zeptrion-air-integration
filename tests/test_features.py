"""Restart, Smartfront LEDs, weak Wi-Fi repair, Smartbutton programming, names to the zApp."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.exceptions import HomeAssistantError

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zeptrion_air.api import compact_smartbutton_requests, truncate_utf8
from custom_components.zeptrion_air.const import CONF_HOSTNAME, DOMAIN, WEAK_WIFI_POLLS


async def _wait_for(predicate, timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


async def _setup(hass: HomeAssistant, device, host: str) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, title=f"zapp-{device.serial}", unique_id=device.serial,
        minor_version=3, data={CONF_HOSTNAME: host},
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


async def test_restart_button(hass: HomeAssistant, fake_zapp) -> None:
    device, host = fake_zapp
    entry = await _setup(hass, device, host)
    button = _eid(hass, "button", f"zapp_{device.serial}_restart")
    assert er.async_get(hass).async_get(button).entity_category == "config"
    await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
    assert device.reboots == 1
    await _unload(hass, entry)


async def test_smartfront_leds(hass: HomeAssistant, fake_zapp) -> None:
    device, host = fake_zapp
    device.leds[5] = "#220000"
    entry = await _setup(hass, device, host)
    registry = er.async_get(hass)
    # one LED per key in use (keys 1/2 unused), shown by default
    assert registry.async_get_entity_id("light", DOMAIN, f"zapp_{device.serial}_led_1") is None
    led5 = _eid(hass, "light", f"zapp_{device.serial}_led_5")
    color5 = _eid(hass, "select", f"zapp_{device.serial}_led_5_color")
    bright5 = _eid(hass, "select", f"zapp_{device.serial}_led_5_brightness")

    state = hass.states.get(led5)
    assert state.state == "on"
    assert tuple(state.attributes["rgb_color"]) == (255, 0, 0)
    assert state.attributes["brightness"] == 0x22
    assert hass.states.get(color5).state == "red"
    assert hass.states.get(bright5).state == "10"

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": led5, "rgb_color": [0, 255, 0], "brightness": 128}, blocking=True
    )
    assert device.leds[5] == "#008000"
    assert hass.states.get(color5).state == "green"
    assert hass.states.get(bright5).state == "50"

    # dropdowns: colour keeps the brightness, brightness keeps the colour
    await hass.services.async_call(
        "select", "select_option", {"entity_id": color5, "option": "blue"}, blocking=True
    )
    assert device.leds[5] == "#000080"
    await hass.services.async_call(
        "select", "select_option", {"entity_id": bright5, "option": "100"}, blocking=True
    )
    assert device.leds[5] == "#0000ff"
    assert hass.states.get(led5).attributes["brightness"] == 255

    await hass.services.async_call(
        "select", "select_option", {"entity_id": color5, "option": "off"}, blocking=True
    )
    assert device.leds[5] == "#000000"
    assert hass.states.get(led5).state == "off"
    await hass.services.async_call("light", "turn_on", {"entity_id": led5}, blocking=True)
    assert device.leds[5] == "#0000ff"  # last colour and brightness again
    assert device.leds[6] == "#000000"  # others untouched

    # Undo steps back through the changes: on -> off -> blue 50 % ...
    undo = _eid(hass, "button", f"zapp_{device.serial}_led_undo")
    await hass.services.async_call("button", "press", {"entity_id": undo}, blocking=True)
    assert device.leds[5] == "#000000"
    await hass.services.async_call("button", "press", {"entity_id": undo}, blocking=True)
    assert device.leds[5] == "#0000ff"
    await hass.services.async_call("button", "press", {"entity_id": undo}, blocking=True)
    assert device.leds[5] == "#000080"
    assert hass.states.get(color5).state == "blue"
    assert hass.states.get(bright5).state == "50"
    for _ in range(2):  # back to green 50 %, then the original #220000
        await hass.services.async_call("button", "press", {"entity_id": undo}, blocking=True)
    assert device.leds[5] == "#220000"
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("button", "press", {"entity_id": undo}, blocking=True)
    await _unload(hass, entry)


async def test_leds_disabled_by_11_16_are_enabled(hass: HomeAssistant, fake_zapp) -> None:
    device, host = fake_zapp
    entry = MockConfigEntry(
        domain=DOMAIN, title=f"zapp-{device.serial}", unique_id=device.serial,
        minor_version=2, data={CONF_HOSTNAME: host},
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "light", DOMAIN, f"zapp_{device.serial}_led_5", config_entry=entry,
        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.minor_version == 3
    assert registry.async_get(old.entity_id).disabled_by is None
    assert hass.states.get(old.entity_id) is not None
    await _unload(hass, entry)


async def test_weak_wifi_raises_and_clears_repair_issue(hass: HomeAssistant, fake_zapp) -> None:
    device, host = fake_zapp
    entry = await _setup(hass, device, host)
    coordinator = entry.runtime_data.coordinator
    issues = ir.async_get(hass)
    issue_id = f"weak_wifi_{device.serial}"

    device.rssi_dbm = -78
    for _ in range(WEAK_WIFI_POLLS - 1):
        await coordinator.async_refresh()
    assert issues.async_get_issue(DOMAIN, issue_id) is None
    await coordinator.async_refresh()
    issue = issues.async_get_issue(DOMAIN, issue_id)
    assert issue is not None and issue.translation_placeholders["rssi"] == "-78"

    device.rssi_dbm = -74  # within the hysteresis: stays
    await coordinator.async_refresh()
    assert issues.async_get_issue(DOMAIN, issue_id) is not None
    device.rssi_dbm = -65
    await coordinator.async_refresh()
    assert issues.async_get_issue(DOMAIN, issue_id) is None
    await _unload(hass, entry)


async def test_rssi_error_keeps_other_diagnostics(hass: HomeAssistant, fake_zapp) -> None:
    device, host = fake_zapp
    entry = await _setup(hass, device, host)
    device.rssi_status = 500
    coordinator = entry.runtime_data.coordinator
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert coordinator.data["rssi_dbm"] is None
    await _unload(hass, entry)


async def test_program_smartbutton(hass: HomeAssistant, fake_zapp) -> None:
    device, host = fake_zapp
    entry = await _setup(hass, device, host)
    hub = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, device.serial)})
    cover = _eid(hass, "cover", f"zapp_{device.serial}_ch1")
    light = _eid(hass, "light", f"zapp_{device.serial}_ch2_light")

    await hass.services.async_call(
        DOMAIN,
        "program_smartbutton",
        {
            "device_id": hub.id,
            "actions": [
                {"entity_id": cover, "command": "recall_s1"},
                {"entity_id": light, "command": "toggle"},
            ],
            "wait": 10,
        },
        blocking=True,
    )
    await _wait_for(lambda: device.smartbutton_programming)
    device.smartbutton_press.set()
    await _wait_for(lambda: device.smartbutton_programmes)
    await hass.async_block_till_done()
    programme = device.smartbutton_programmes[0]
    assert programme[0] == {
        "req": "POST", "loc": host, "pth": "/zrap/chctrl/ch1",
        "typ": "application/x-www-form-urlencoded", "bdy": "cmd=recall_s1",
    }
    assert programme[1] == {"pth": "/zrap/chctrl/ch2", "bdy": "cmd=toggle"}  # repeats left out
    assert not device.smartbutton_programming
    await _unload(hass, entry)


async def test_program_smartbutton_without_press_changes_nothing(hass: HomeAssistant, fake_zapp) -> None:
    device, host = fake_zapp
    device.smartbutton_prefix = "/zapi/smartfront"  # the other path named in the PDF
    entry = await _setup(hass, device, host)
    hub = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, device.serial)})
    await hass.services.async_call(
        DOMAIN, "program_smartbutton",
        {"device_id": hub.id, "requests": [{"req": "GET", "loc": "192.168.1.5", "pth": "/x"}], "wait": 10},
        blocking=True,
    )
    await _wait_for(lambda: "GET /zapi/smartfront/prgn" in device.requests)
    await _wait_for(lambda: not device.smartbutton_programming, timeout=8)
    assert device.smartbutton_programmes == []
    await _unload(hass, entry)


async def test_write_name_to_zapp(hass: HomeAssistant, fake_zapp) -> None:
    device, host = fake_zapp
    entry = await _setup(hass, device, host)
    cover = _eid(hass, "cover", f"zapp_{device.serial}_ch1")
    devices = dr.async_get(hass)
    blind = devices.async_get_device(identifiers={(DOMAIN, f"{device.serial}_ch1")})
    area = ar.async_get(hass).async_create("Wohnzimmer Süd")
    devices.async_update_device(blind.id, name_by_user="Fenster Süd", area_id=area.id)

    await hass.services.async_call(DOMAIN, "write_name_to_zapp", {"entity_id": cover}, blocking=True)
    assert device.channels[1]["name"] == "Fenster Süd"
    assert device.channels[1]["group"] == "Wohnzimmer Süd"

    long_name = "Ä" * 40  # 80 bytes in UTF-8
    await hass.services.async_call(
        DOMAIN, "write_name_to_zapp", {"entity_id": cover, "name": long_name}, blocking=True
    )
    assert device.channels[1]["name"] == "Ä" * 16
    await _unload(hass, entry)


def test_helpers() -> None:
    assert truncate_utf8("abc", 32) == "abc"
    assert truncate_utf8("éé", 3) == "é"
    assert compact_smartbutton_requests([{"req": "GET", "loc": "a"}]) == {"req": "GET", "loc": "a"}
