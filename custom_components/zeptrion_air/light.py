"""Light platform for Zeptrion Air."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
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
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import (
    ZeptrionAirApiClientCommunicationError,
    ZeptrionAirApiClientError,
)
from .entity import channel_device_identifier
from .const import (
    CONF_LIGHT_POLLING,
    DEFAULT_LIGHT_POLLING,
    DOMAIN,
    ZEPTRION_AIR_WEBSOCKET_MESSAGE,
)

_LOGGER = logging.getLogger(__name__)

DIMMER_FULL_TRAVEL_MS = 3400

# Optional polling (option "light_polling", off by default): when enabled, every
# light channel is polled periodically so that state changes missed on the
# websocket (e.g. after a reconnect) are corrected. One request per hub at a time.
SCAN_INTERVAL = timedelta(seconds=60)
PARALLEL_UPDATES = 1


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

    single_channel: bool = bool(platform_data.get("single_channel", False))
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

        light_device_info: dict[str, Any]
        if single_channel:
            # Single-channel device: attach the entity to the wall device.
            light_device_info = {
                "identifiers": {channel_device_identifier(hub_serial, channel_id, True)},
            }
        else:
            light_device_info = {
                "identifiers": {channel_device_identifier(hub_serial, channel_id, False)},
                "name": desired_name,
                "via_device_id": hub_device_id,
                "manufacturer": hub_manufacturer,
                "sw_version": hub_sw_version,
                "model": f"Zeptrion Air Channel {channel_id} - {'Dimmer' if is_dimmer else 'Switch'}",
            }

        new_entities.append(
            ZeptrionAirLight(
                config_entry=entry,
                device_info_for_light_entity=light_device_info,
                channel_id=channel_id,
                hub_serial=hub_serial,
                is_dimmer=is_dimmer,
                entity_name=desired_name,
            )
        )

    if new_entities:
        _LOGGER.info("Adding %s ZeptrionAirLight entities.", len(new_entities))
        async_add_entities(new_entities)
    else:
        _LOGGER.info("No Zeptrion Air light entities to add.")

    return True


class ZeptrionAirLight(LightEntity):
    """Representation of a Zeptrion Air light."""

    def __init__(
        self,
        config_entry: ConfigEntry,
        device_info_for_light_entity: dict[str, Any],
        channel_id: int,
        hub_serial: str,
        is_dimmer: bool,
        entity_name: str | None = None,
    ) -> None:
        """Initialize the light."""
        self.config_entry = config_entry
        self._channel_id = channel_id
        self._is_dimmer = is_dimmer

        self._attr_device_info = device_info_for_light_entity
        self._attr_name = str(
            entity_name or device_info_for_light_entity.get("name") or f"Channel {channel_id}"
        )
        self._attr_unique_id = f"zapp_{hub_serial}_ch{channel_id}_light"

        self._attr_is_on = False
        self._attr_brightness = 255 if is_dimmer else None
        self._last_nonzero_brightness = 255
        self._pending_refresh_task: asyncio.Task | None = None

        # Home Assistant only calls async_update when polling is enabled.
        # Changing the option reloads the config entry, so this is re-read.
        self._attr_should_poll = bool(
            config_entry.options.get(CONF_LIGHT_POLLING, DEFAULT_LIGHT_POLLING)
        )

        if is_dimmer:
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
            self._attr_color_mode = ColorMode.BRIGHTNESS
        else:
            self._attr_supported_color_modes = {ColorMode.ONOFF}
            self._attr_color_mode = ColorMode.ONOFF

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

        await self._async_refresh_from_scan()

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
