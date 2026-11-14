"""Light platform for Zeptrion Air."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import (
    ExtraStoredData,
    RestoredExtraData,
    RestoreEntity,
)
import voluptuous as vol

from .api import (
    ZeptrionAirApiClientCommunicationError,
    ZeptrionAirApiClientError,
)
from .areas import suggested_area_for_channel
from .const import (
    DOMAIN,
    SERVICE_LIGHT_RECALL_SCENE,
    SERVICE_LIGHT_STORE_SCENE,
    SIGNAL_AVAILABILITY,
    ZEPTRION_AIR_WEBSOCKET_MESSAGE,
)

_LOGGER = logging.getLogger(__name__)

DIMMER_FULL_TRAVEL_MS = 3400
SCENE_SCHEMA = {vol.Required("scene"): vol.All(vol.Coerce(int), vol.Range(min=1, max=4))}

# Lights are not polled (the event stream re-reads all channels after a reconnect).


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> bool:
    """Set up Zeptrion Air light entities from a config entry."""
    platform_data: dict[str, Any] | None = hass.data[DOMAIN].get(entry.entry_id)
    if not platform_data:
        _LOGGER.error("No platform_data found for entry ID %s", entry.entry_id)
        return False

    main_hub_device_info: dict[str, Any] = platform_data.get("hub_device_info", {})
    identified_channels_list: list[dict[str, Any]] = platform_data.get(
        "identified_channels", []
    )
    hub_serial: str | None = platform_data.get("hub_serial")

    if not hub_serial:
        _LOGGER.error("Hub serial not found in platform_data.")
        return False

    # The hub device itself is registered in __init__.py before platform setup
    # is forwarded, so we can resolve its registry device_id here and link
    # child entities via the modern `via_device_id` field instead of the
    # deprecated `via_device` identifiers-tuple field.
    dev_reg = dr.async_get(hass)
    hub_device_entry = dev_reg.async_get_device_by_identifier(
        (DOMAIN, hub_serial), entry.entry_id
    )
    hub_device_id: str | None = hub_device_entry.id if hub_device_entry else None
    if hub_device_id is None:
        _LOGGER.warning(
            "Could not resolve hub device registry entry for serial %s; "
            "light entities will be created without a via_device_id link.",
            hub_serial,
        )

    new_entities: list[ZeptrionAirLight] = []

    for channel_info_dict in identified_channels_list:
        channel_id: int | None = channel_info_dict.get("id")
        device_type: str | None = channel_info_dict.get("device_type")

        if channel_id is None or device_type not in ("light_switch", "light_dimmer"):
            continue

        desired_name: str = channel_info_dict.get("entity_base_name") or f"Channel {channel_id}"
        is_dimmer = device_type == "light_dimmer"

        hub_manufacturer: str = main_hub_device_info.get("manufacturer", "Feller AG")
        hub_sw_version: str | None = main_hub_device_info.get("sw_version")

        light_device_info: dict[str, Any] = {
            "identifiers": {(DOMAIN, f"{hub_serial}_ch{channel_id}")},
            "name": desired_name,
            "via_device_id": hub_device_id,
            "manufacturer": hub_manufacturer,
            "sw_version": hub_sw_version,
            "model": f"Zeptrion Air Channel {channel_id} - {'Dimmer' if is_dimmer else 'Switch'}",
        }
        if area := suggested_area_for_channel(channel_info_dict):
            light_device_info["suggested_area"] = area

        new_entities.append(
            ZeptrionAirLight(
                config_entry=entry,
                device_info_for_light_entity=light_device_info,
                channel_id=channel_id,
                hub_serial=hub_serial,
                is_dimmer=is_dimmer,
            )
        )

    if new_entities:
        _LOGGER.info("Adding %s ZeptrionAirLight entities.", len(new_entities))
        async_add_entities(new_entities)
    else:
        _LOGGER.info("No Zeptrion Air light entities to add.")

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_LIGHT_STORE_SCENE, SCENE_SCHEMA, "async_light_store_scene"
    )
    platform.async_register_entity_service(
        SERVICE_LIGHT_RECALL_SCENE, SCENE_SCHEMA, "async_light_recall_scene"
    )

    return True


class ZeptrionAirLight(LightEntity, RestoreEntity):
    """Representation of a Zeptrion Air light."""

    def __init__(
        self,
        config_entry: ConfigEntry,
        device_info_for_light_entity: dict[str, Any],
        channel_id: int,
        hub_serial: str,
        is_dimmer: bool,
    ) -> None:
        """Initialize the light."""
        self.config_entry = config_entry
        self._channel_id = channel_id
        self._is_dimmer = is_dimmer

        self._attr_device_info = device_info_for_light_entity
        self._attr_name = str(device_info_for_light_entity.get("name") or f"Channel {channel_id}")
        self._attr_unique_id = f"zapp_{hub_serial}_ch{channel_id}_light"

        self._attr_is_on = False
        self._attr_brightness = 255 if is_dimmer else None
        self._last_nonzero_brightness = 255
        self._pending_refresh_task: asyncio.Task | None = None
        # Scenes S1-S4 as stored through Home Assistant: 0 = off, else the
        # brightness (255 for switches). The zApp keeps the real scene; this
        # only lets a recall show the stored brightness (the zApp reports 100).
        self._scenes: dict[int, int] = {}

        # No polling: since 2026.11.3 all channel states are read again after
        # every reconnect of the event stream, which replaces the former
        # "light_polling" option.
        self._attr_should_poll = False

        if is_dimmer:
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
            self._attr_color_mode = ColorMode.BRIGHTNESS
        else:
            self._attr_supported_color_modes = {ColorMode.ONOFF}
            self._attr_color_mode = ColorMode.ONOFF

    @property
    def available(self) -> bool:
        """Unavailable only after the event stream has been down for the grace period."""
        runtime_data = self.config_entry.runtime_data
        return runtime_data is not None and getattr(runtime_data, "available", True)

    def _set_state_from_api_percent(self, value_0_100: int) -> bool:
        """Apply a Zeptrion channel value to the HA entity state.

        - 0: off
        - 1-99 (dimmers): on, brightness taken from the value. Current hubs do
          not report intermediate values for dimmers, but if one does, it is used.
        - 100: on; for dimmers the last known brightness is kept, because the
          hub reports 100 for any dimmed level.
        - negative values (unused channel) are ignored.

        Returns True if the entity state was updated.
        """
        value = int(value_0_100)
        if value < 0:
            return False
        value = min(100, value)

        if value == 0:
            self._attr_is_on = False
            return True

        self._attr_is_on = True
        if self._is_dimmer and value < 100:
            brightness = max(1, round(value * 255 / 100))
            self._attr_brightness = brightness
            self._last_nonzero_brightness = brightness
        return True

    async def _async_refresh_from_scan(
        self, delay: float = 0.0, write_state: bool = True
    ) -> None:
        """Refresh current state from /zrap/chscan/chX after an optional delay."""
        if delay > 0:
            await asyncio.sleep(delay)

        try:
            scan_data = await self.config_entry.runtime_data.client.async_get_channel_scan_info(
                self._channel_id
            )

            channel_key = f"ch{self._channel_id}"
            channel_data = (
                scan_data.get("chscan", {}).get(channel_key, {})
                if isinstance(scan_data, dict)
                else {}
            )

            raw_value = channel_data.get("val")
            if raw_value is None:
                _LOGGER.debug(
                    "No scan value returned for light %s (Channel %s): %s",
                    self._attr_name,
                    self._channel_id,
                    scan_data,
                )
                return

            if self._set_state_from_api_percent(int(raw_value)) and write_state:
                self.async_write_ha_state()

        except Exception as e:
            _LOGGER.debug(
                "Could not refresh light state from scan for %s (Channel %s): %s",
                self._attr_name,
                self._channel_id,
                e,
            )

    def _schedule_refresh(self, delay: float) -> None:
        """Schedule a delayed refresh from scan endpoint."""
        if self.hass is None:
            return

        if self._pending_refresh_task and not self._pending_refresh_task.done():
            self._pending_refresh_task.cancel()

        self._pending_refresh_task = self.hass.async_create_task(
            self._async_refresh_from_scan(delay)
        )

    async def async_update(self) -> None:
        """Poll the channel state (called by Home Assistant every SCAN_INTERVAL)."""
        if self._pending_refresh_task and not self._pending_refresh_task.done():
            # A refresh after a command is pending; let it set the state.
            return
        await self._async_refresh_from_scan(write_state=False)

    async def async_added_to_hass(self) -> None:
        """Handle entity added to HA."""
        await super().async_added_to_hass()

        self.async_on_remove(
            self.hass.bus.async_listen(
                ZEPTRION_AIR_WEBSOCKET_MESSAGE,
                self.async_handle_websocket_message,
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_AVAILABILITY.format(self.config_entry.entry_id),
                self.async_write_ha_state,
            )
        )

        if (extra := await self.async_get_last_extra_data()) is not None:
            data = extra.as_dict()
            self._scenes = {
                int(k): int(v) for k, v in (data.get("scenes") or {}).items()
            }
            if self._is_dimmer and data.get("brightness"):
                self._attr_brightness = int(data["brightness"])
                self._last_nonzero_brightness = int(data["brightness"])

        await self._async_refresh_from_scan()

    @property
    def extra_restore_state_data(self) -> ExtraStoredData:
        """Stored scenes and the last brightness survive a restart."""
        return RestoredExtraData(
            {
                "scenes": {str(k): v for k, v in self._scenes.items()},
                "brightness": self._last_nonzero_brightness if self._is_dimmer else None,
            }
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Scenes stored through Home Assistant (brightness 0-255, 0 = off)."""
        if not self._scenes:
            return None
        return {f"scene_s{k}": v for k, v in sorted(self._scenes.items())}

    async def async_light_store_scene(self, scene: int) -> None:
        """Store the current state (on/off, dim level) as scene S1-S4 on the zApp."""
        try:
            await self.config_entry.runtime_data.client.async_channel_store_scene(
                self._channel_id, scene
            )
        except (ZeptrionAirApiClientCommunicationError, ZeptrionAirApiClientError) as e:
            raise HomeAssistantError(f"{self.name}: storing scene S{scene} failed: {e}") from e
        if not self._attr_is_on:
            self._scenes[scene] = 0
        else:
            self._scenes[scene] = (self._attr_brightness or 255) if self._is_dimmer else 255
        self.async_write_ha_state()

    async def async_light_recall_scene(self, scene: int) -> None:
        """Recall scene S1-S4; the zApp sets on/off and the dim level."""
        stored = self._scenes.get(scene)
        if stored is not None:
            # Optimistic: the push that follows only says on (100) or off (0)
            self._attr_is_on = stored > 0
            if self._is_dimmer and stored > 0:
                self._attr_brightness = stored
                self._last_nonzero_brightness = stored
            self.async_write_ha_state()
        try:
            await self.config_entry.runtime_data.client.async_channel_recall_s(
                self._channel_id, scene
            )
        except (ZeptrionAirApiClientCommunicationError, ZeptrionAirApiClientError) as e:
            raise HomeAssistantError(f"{self.name}: recalling scene S{scene} failed: {e}") from e
        self._schedule_refresh(0.35 + (DIMMER_FULL_TRAVEL_MS / 1000 if self._is_dimmer else 0))

    async def async_handle_websocket_message(self, event: Event) -> None:
        """Handle websocket events for this light."""
        message_data = event.data

        if message_data.get("channel") != self._channel_id:
            return

        if message_data.get("hub_unique_id") != self.config_entry.unique_id:
            return

        if message_data.get("source") != "eid1":
            return

        raw_value = message_data.get("value")
        if raw_value is None:
            return

        try:
            # 0 = off, 100 = on (dimmer brightness kept), 1-99 = brightness.
            if self._set_state_from_api_percent(int(raw_value)):
                self.async_write_ha_state()
        except (ValueError, TypeError):
            _LOGGER.debug(
                "Ignoring non-integer websocket value for light %s (Channel %s): %s",
                self._attr_name,
                self._channel_id,
                raw_value,
            )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""
        client = self.config_entry.runtime_data.client

        try:
            if self._is_dimmer:
                brightness = kwargs.get(
                    ATTR_BRIGHTNESS,
                    self._last_nonzero_brightness or 255,
                )
                brightness = max(1, min(255, int(brightness)))

                # Optimistic HA state
                self._attr_is_on = True
                self._attr_brightness = brightness
                self._last_nonzero_brightness = brightness
                self.async_write_ha_state()

                await client.async_channel_set_brightness(self._channel_id, brightness)

                # Refresh only ON/OFF state afterwards (not brightness)
                refresh_delay = 0.35 + (
                    (255 - brightness) / 255 * (DIMMER_FULL_TRAVEL_MS / 1000)
                )
                self._schedule_refresh(refresh_delay)
            else:
                self._attr_is_on = True
                self.async_write_ha_state()

                await client.async_channel_on(self._channel_id)
                self._schedule_refresh(0.3)

        except (ZeptrionAirApiClientCommunicationError, ZeptrionAirApiClientError) as e:
            _LOGGER.error(
                "API error while turning on light %s (Channel %s): %s",
                self._attr_name,
                self._channel_id,
                e,
            )
            raise HomeAssistantError(
                f"Failed to turn on light {self.name} (Channel {self._channel_id}): {e}"
            ) from e

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        client = self.config_entry.runtime_data.client

        try:
            if self._is_dimmer and self._attr_brightness:
                self._last_nonzero_brightness = self._attr_brightness

            self._attr_is_on = False
            self.async_write_ha_state()

            await client.async_channel_off(self._channel_id)
            self._schedule_refresh(0.3)

        except (ZeptrionAirApiClientCommunicationError, ZeptrionAirApiClientError) as e:
            _LOGGER.error(
                "API error while turning off light %s (Channel %s): %s",
                self._attr_name,
                self._channel_id,
                e,
            )
            raise HomeAssistantError(
                f"Failed to turn off light {self.name} (Channel {self._channel_id}): {e}"
            ) from e
