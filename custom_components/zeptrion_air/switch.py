"""Learning mode switch (one per zApp with blinds, all coupled)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .learning import async_get_learning_manager

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the learning mode switch if the zApp has a blind channel."""
    platform_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    hub_serial: str | None = platform_data.get("hub_serial")
    has_blinds = any(
        channel.get("device_type") == "cover"
        for channel in platform_data.get("identified_channels", [])
    )
    if hub_serial and has_blinds:
        async_add_entities(
            [ZeptrionAirLearningSwitch(hub_serial), ZeptrionAirApplyToAllSwitch(hub_serial)]
        )


class ZeptrionAirLearningSwitch(SwitchEntity):
    """Switches the integration-wide blind learning mode on and off."""

    _attr_has_entity_name = True
    _attr_translation_key = "blind_learning"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:school-outline"
    _attr_should_poll = False

    def __init__(self, hub_serial: str) -> None:
        self._attr_unique_id = f"zapp_{hub_serial}_blind_learning"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, hub_serial)})

    @property
    def is_on(self) -> bool:
        return async_get_learning_manager(self.hass).active

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_get_learning_manager(self.hass).async_add_listener(self.async_write_ha_state)
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        async_get_learning_manager(self.hass).async_start()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await async_get_learning_manager(self.hass).async_stop()


class ZeptrionAirApplyToAllSwitch(SwitchEntity, RestoreEntity):
    """When on, learned travel times are transferred to all blinds (coupled across hubs).

    For installations where all blinds have the same height and drive: measuring
    one blind is then enough.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "blind_learning_apply_all"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:content-copy"
    _attr_should_poll = False

    def __init__(self, hub_serial: str) -> None:
        self._attr_unique_id = f"zapp_{hub_serial}_blind_learning_apply_all"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, hub_serial)})

    @property
    def is_on(self) -> bool:
        return async_get_learning_manager(self.hass).apply_to_all

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        manager = async_get_learning_manager(self.hass)
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state == "on" and not manager.apply_to_all:
            manager.apply_to_all = True
            manager.notify()
        self.async_on_remove(manager.async_add_listener(self.async_write_ha_state))

    async def async_turn_on(self, **kwargs: Any) -> None:
        manager = async_get_learning_manager(self.hass)
        manager.apply_to_all = True
        manager.notify()

    async def async_turn_off(self, **kwargs: Any) -> None:
        manager = async_get_learning_manager(self.hass)
        manager.apply_to_all = False
        manager.notify()
