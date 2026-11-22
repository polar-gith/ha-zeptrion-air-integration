"""Switching from heldchen/ha-zeptrion-air: entities, devices and settings are kept.

The registry and config entry below are what heldchen/ha-zeptrion-air (2025.6)
leaves behind: same domain, config entry version 1.1 with the step duration in
its data, a hub device per zApp, a channel device per blind and the unique IDs
zapp_<serial>_ch<n>, zapp_<serial>_ch<n>_blind_recall_s<1-4> and
zapp_<serial>_ch<n>_<name|group|icon_id>. Users renamed entity IDs and use them
in automations, so they must survive the switch.
"""

from __future__ import annotations

import asyncio

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zeptrion_air.const import DOMAIN


async def _wait_for(predicate, timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


async def test_switch_from_heldchen_keeps_entities(hass: HomeAssistant, fake_zapp) -> None:
    device, host = fake_zapp
    serial = device.serial
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"zapp-{serial}",
        unique_id=serial,
        version=1,
        minor_version=1,
        data={
            "name": f"zapp-{serial}",
            "hostname": host,
            "ip_address": "127.0.0.1",
            "step_duration_ms": 400,
        },
    )
    entry.add_to_hass(hass)

    devices = dr.async_get(hass)
    hub = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, serial)},
        manufacturer="Feller AG",
        name=f"zapp-{serial}",
    )
    channel = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{serial}_ch1")},
        name="Blind",
        via_device_id=hub.id,
    )

    entities = er.async_get(hass)
    kept = {
        "cover": (f"zapp_{serial}_ch1", "living_blind"),
        "button": (f"zapp_{serial}_ch1_blind_recall_s1", "living_blind_morning"),
    }
    for n in (2, 3, 4):
        kept[f"button_s{n}"] = (f"zapp_{serial}_ch1_blind_recall_s{n}", f"living_blind_s{n}")
    created: dict[str, str] = {}
    for key, (unique_id, object_id) in kept.items():
        domain = key.split("_")[0]
        reg = entities.async_get_or_create(
            domain,
            DOMAIN,
            unique_id,
            config_entry=entry,
            device_id=channel.id,
            suggested_object_id=object_id,
        )
        created[unique_id] = reg.entity_id
    sensor = entities.async_get_or_create(
        "sensor",
        DOMAIN,
        f"zapp_{serial}_ch1_name",
        config_entry=entry,
        device_id=channel.id,
        suggested_object_id="living_blind_name",
    )
    created[sensor.unique_id] = sensor.entity_id
    before = {e.entity_id for e in er.async_entries_for_config_entry(entities, entry.entry_id)}

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await _wait_for(lambda: entry.runtime_data.websocket_listener.connected)

    # migrated to the current config entry version
    assert (entry.version, entry.minor_version) == (1, 4)

    # every entity ID from heldchen points to the same unique ID and is in use
    for unique_id, entity_id in created.items():
        domain = entity_id.split(".")[0]
        assert entities.async_get_entity_id(domain, DOMAIN, unique_id) == entity_id
        assert hass.states.get(entity_id) is not None, entity_id

    # no second cover or scene button for the same channel
    covers = [
        e for e in er.async_entries_for_config_entry(entities, entry.entry_id) if e.domain == "cover"
    ]
    assert [c.entity_id for c in covers] == ["cover.living_blind"]
    recall_buttons = [
        e
        for e in er.async_entries_for_config_entry(entities, entry.entry_id)
        if "_blind_recall_s" in e.unique_id
    ]
    assert len(recall_buttons) == 4
    assert before <= {e.entity_id for e in er.async_entries_for_config_entry(entities, entry.entry_id)}

    # the existing devices are reused, not duplicated
    assert devices.async_get_device_by_identifier((DOMAIN, serial), entry.entry_id).id == hub.id
    assert devices.async_get_device_by_identifier((DOMAIN, f"{serial}_ch1"), entry.entry_id).id == channel.id
    assert entities.async_get("cover.living_blind").device_id == channel.id

    # heldchen's actions work on the kept entity IDs, with the stored step duration
    await hass.services.async_call(
        DOMAIN, "blind_up_step", {"entity_id": "cover.living_blind"}, blocking=True
    )
    assert device.commands[-1] == (1, "move_open_400")
    await hass.services.async_call(
        DOMAIN, "blind_recall_s1", {"entity_id": "cover.living_blind"}, blocking=True
    )
    assert device.commands[-1] == (1, "recall_s1")
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.living_blind_morning"}, blocking=True
    )
    assert device.commands[-1] == (1, "recall_s1")

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
