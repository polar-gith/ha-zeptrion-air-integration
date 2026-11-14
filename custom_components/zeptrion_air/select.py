"""Selects for the Smartfront LEDs: colour and brightness from a list."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_AVAILABILITY
from .leds import (
    COLOR_CUSTOM,
    COLOR_OFF,
    LED_BRIGHTNESS_STEPS,
    LED_COLORS,
    SIGNAL_LED,
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """A colour and a brightness select per Smartfront LED."""
    runtime = entry.runtime_data
    if runtime is None or runtime.leds is None:
        return
    serial = str(entry.unique_id)
    entities: list[SelectEntity] = []
    for led in runtime.leds.leds:
        entities.append(LedColorSelect(entry, serial, led))
        entities.append(LedBrightnessSelect(entry, serial, led))
    async_add_entities(entities)


class _LedSelect(SelectEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, serial: str, led: int, kind: str) -> None:
        self.config_entry = entry
        self._led = led
        self._attr_unique_id = f"zapp_{serial}_led_{led}_{kind}"
        self._attr_translation_placeholders = {"led": str(led)}
        self._attr_device_info = {"identifiers": {(DOMAIN, serial)}}

    @property
    def _leds(self):
        return self.config_entry.runtime_data.leds

    @property
    def _state(self):
        return self._leds.states[self._led]

    @property
    def available(self) -> bool:
        runtime = self.config_entry.runtime_data
        return runtime is not None and getattr(runtime, "available", True)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        @callback
        def _changed(led: int) -> None:
            if led == self._led:
                self.async_write_ha_state()

        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_LED.format(self.config_entry.entry_id), _changed)
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_AVAILABILITY.format(self.config_entry.entry_id), self.async_write_ha_state
            )
        )


class LedColorSelect(_LedSelect):
    """Colour of an LED: off or a preset; "custom" when set otherwise."""

    _attr_translation_key = "smartfront_led_color"
    _attr_icon = "mdi:palette"

    def __init__(self, entry: ConfigEntry, serial: str, led: int) -> None:
        super().__init__(entry, serial, led, "color")
        self._attr_options = [COLOR_OFF, *LED_COLORS, COLOR_CUSTOM]

    @property
    def current_option(self) -> str:
        return self._state.color_name

    async def async_select_option(self, option: str) -> None:
        if option == COLOR_OFF:
            await self._leds.async_set(self._led, on=False)
        elif option in LED_COLORS:
            await self._leds.async_set(self._led, rgb=LED_COLORS[option])
        # "custom" only displays a colour set elsewhere; choosing it changes nothing


class LedBrightnessSelect(_LedSelect):
    """Brightness of an LED in steps (percent)."""

    _attr_translation_key = "smartfront_led_brightness"
    _attr_icon = "mdi:brightness-6"

    def __init__(self, entry: ConfigEntry, serial: str, led: int) -> None:
        super().__init__(entry, serial, led, "brightness")
        self._attr_options = [str(step) for step in LED_BRIGHTNESS_STEPS]

    @property
    def current_option(self) -> str:
        return self._state.brightness_step

    async def async_select_option(self, option: str) -> None:
        brightness = round(int(option) * 255 / 100)
        # choosing a brightness also switches the LED on (with its last colour)
        await self._leds.async_set(self._led, brightness=brightness)
