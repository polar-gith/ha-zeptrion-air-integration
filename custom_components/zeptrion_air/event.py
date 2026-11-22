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

from .const import (
    CONF_LEGACY_KEYS,
    DOMAIN,
    EVENT_PRESS,
    EVENT_RELEASE,
    SIGNAL_AVAILABILITY,
    SIGNAL_KEY_EVENT,
    SIGNAL_LEGACY_KEYS,
)
from .data import ZeptrionAirData
from .smartfront import KeyEvent, KeyFunction, legacy_keys

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
    """Set up the wall key event entity.

    With a Smartfront the keys come from ``btfu`` (/zapi/smartfront/id). Older
    button faces lack that endpoint; their keys are learnt from the event stream
    (first press) and kept in the config entry.
    """
    runtime_data: ZeptrionAirData | None = entry.runtime_data
    platform_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    hub_serial: str | None = platform_data.get("hub_serial")
    if runtime_data is None or not hub_serial:
        return

    if runtime_data.smartfront:
        keys = [key for key in runtime_data.keys if key.kind != "unused"]
        if not keys:
            _LOGGER.debug("Smartfront of %s has no used keys", hub_serial)
            return
        async_add_entities([ZeptrionAirKeysEvent(entry, hub_serial, keys)])
        return

    # Older button face (or none): keys learnt from key presses
    entity: ZeptrionAirKeysEvent | None = None
    known = int(entry.data.get(CONF_LEGACY_KEYS, 0))
    if known:
        entity = ZeptrionAirKeysEvent(entry, hub_serial, list(legacy_keys(known)), legacy=True)
        async_add_entities([entity])

    @callback
    def _keys_learnt(count: int, first: KeyEvent) -> None:
        nonlocal entity
        keys = list(legacy_keys(count))
        if entity is None:
            entity = ZeptrionAirKeysEvent(entry, hub_serial, keys, legacy=True, pending=first)
            async_add_entities([entity])
        else:
            entity.set_keys(keys, pending=first)

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_LEGACY_KEYS.format(entry.entry_id), _keys_learnt)
    )


class ZeptrionAirKeysEvent(EventEntity):
    """All Smartfront keys of one zApp."""

    _attr_has_entity_name = True
    _attr_translation_key = "wall_keys"
    _attr_device_class = EventDeviceClass.BUTTON
    _attr_should_poll = False

    def __init__(
        self,
        entry: ConfigEntry,
        hub_serial: str,
        keys: list[KeyFunction],
        *,
        legacy: bool = False,
        pending: KeyEvent | None = None,
    ) -> None:
        self._entry = entry
        self._legacy = legacy
        self._pending = pending
        self._attr_unique_id = f"zapp_{hub_serial}_wall_keys"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, hub_serial)})
        self._apply_keys(keys)

    def _apply_keys(self, keys: list[KeyFunction]) -> None:
        self._keys = {key.key: key for key in keys}
        self._attr_event_types = [
            event_type(key.key, action)
            for key in keys
            for action in (EVENT_PRESS, EVENT_RELEASE)
        ]
        smartfront = self._entry.runtime_data.smartfront or {}
        self._attr_extra_state_attributes = {
            "smartfront_type": smartfront.get("type"),
            "btfu": smartfront.get("btfu"),
            # True: older button face without key functions (/zapi/smartfront/id)
            "keys_learnt": self._legacy,
            "keys": {str(key.key): key.as_attributes() for key in keys},
        }

    @callback
    def set_keys(self, keys: list[KeyFunction], pending: KeyEvent | None = None) -> None:
        """More keys learnt (older button face): extend the event types."""
        self._apply_keys(keys)
        if pending is not None:
            self._handle_key(pending)
        else:
            self.async_write_ha_state()

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
        if self._pending is not None:
            pending, self._pending = self._pending, None
            self._handle_key(pending)

    @callback
    def _handle_key(self, event: KeyEvent) -> None:
        function = self._keys.get(event.key)
        if function is None:
            _LOGGER.debug("Ignoring event of unconfigured key %s (%s)", event.key, event.raw)
            return
        action = EVENT_PRESS if event.pressed else EVENT_RELEASE
        self._trigger_event(event_type(event.key, action), function.as_attributes())
        self.async_write_ha_state()
