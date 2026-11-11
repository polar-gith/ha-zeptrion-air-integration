"""Event entity for the Smartfront wall keys of one zApp.

One entity per zApp (not one per key) keeps the entity count low. Every key has
its own event types, ``key_<n>_press`` and ``key_<n>_release``; the event also
carries the key number, its function (channel key, Smartbutton) and, for channel
keys, the channel(s) and direction.

Key decoding follows diaznet/zeptrionair-api (MIT), docs/protocol.md section 6.
"""

from __future__ import annotations

import logging

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, EVENT_PRESS, EVENT_RELEASE, SIGNAL_AVAILABILITY, SIGNAL_KEY_EVENT
from .data import ZeptrionAirData
from .smartfront import KeyEvent, KeyFunction

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


def event_type(key: int, action: str) -> str:
    """Event type of one key and action, e.g. ``key_3_press``."""
    return f"key_{key}_{action}"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the wall key event entity if the zApp has a Smartfront."""
    runtime_data: ZeptrionAirData | None = entry.runtime_data
    platform_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    hub_serial: str | None = platform_data.get("hub_serial")
    if runtime_data is None or not runtime_data.smartfront or not hub_serial:
        return

    keys = [key for key in runtime_data.keys if key.kind != "unused"]
    if not keys:
        _LOGGER.debug("Smartfront of %s has no used keys", hub_serial)
        return

    async_add_entities([ZeptrionAirKeysEvent(entry, hub_serial, keys)])


class ZeptrionAirKeysEvent(EventEntity):
    """All Smartfront keys of one zApp."""

    _attr_has_entity_name = True
    _attr_translation_key = "wall_keys"
    _attr_device_class = EventDeviceClass.BUTTON
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, hub_serial: str, keys: list[KeyFunction]) -> None:
        self._entry = entry
        self._keys = {key.key: key for key in keys}
        self._attr_unique_id = f"zapp_{hub_serial}_wall_keys"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, hub_serial)})
        self._attr_event_types = [
            event_type(key.key, action)
            for key in keys
            for action in (EVENT_PRESS, EVENT_RELEASE)
        ]
        smartfront = entry.runtime_data.smartfront or {}
        self._attr_extra_state_attributes = {
            "smartfront_type": smartfront.get("type"),
            "btfu": smartfront.get("btfu"),
            "keys": {str(key.key): key.as_attributes() for key in keys},
        }

    @property
    def available(self) -> bool:
        runtime_data: ZeptrionAirData | None = self._entry.runtime_data
        return runtime_data is not None and runtime_data.available

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_KEY_EVENT.format(self._entry.entry_id), self._handle_key
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_AVAILABILITY.format(self._entry.entry_id),
                self.async_write_ha_state,
            )
        )

    @callback
    def _handle_key(self, event: KeyEvent) -> None:
        function = self._keys.get(event.key)
        if function is None:
            _LOGGER.debug("Ignoring event of unconfigured key %s (%s)", event.key, event.raw)
            return
        action = EVENT_PRESS if event.pressed else EVENT_RELEASE
        self._trigger_event(event_type(event.key, action), function.as_attributes())
        self.async_write_ha_state()
