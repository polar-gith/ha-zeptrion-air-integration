"""Smartfront LEDs: shared state of the background colours of one zApp.

The light and the two selects (colour, brightness) of an LED all work on this
state, so they always show the same. Only ``bg`` is written (Feller API 5.1.3).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .api import ZeptrionAirApiClientError

if TYPE_CHECKING:
    from .api import ZeptrionAirApiClient

_LOGGER = logging.getLogger(__name__)

SIGNAL_LED = "zeptrion_air_led_{}"

# Colour presets for the colour select (full brightness; brightness is separate)
LED_COLORS: dict[str, tuple[int, int, int]] = {
    "red": (255, 0, 0),
    "orange": (255, 100, 0),
    "yellow": (255, 200, 0),
    "green": (0, 255, 0),
    "cyan": (0, 255, 255),
    "blue": (0, 0, 255),
    "violet": (140, 0, 255),
    "magenta": (255, 0, 160),
    "white": (255, 255, 255),
}
COLOR_OFF = "off"
COLOR_CUSTOM = "custom"  # shown when the colour was set otherwise (light, zeptrion app)
# Brightness steps of the brightness select, in percent
LED_BRIGHTNESS_STEPS: tuple[int, ...] = (5, 10, 25, 50, 75, 100)
LED_DEFAULT_BRIGHTNESS = 64  # 25 %; the LEDs are bright, Feller's examples use #220000
LED_DEFAULT_COLOR = LED_COLORS["white"]
UNDO_DEPTH = 20


def parse_hex(color: str) -> tuple[int, int, int]:
    try:
        value = color.strip().lstrip("#")
        return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except (ValueError, IndexError, AttributeError):
        return (0, 0, 0)


@dataclass
class LedState:
    """One LED: colour at full brightness, brightness 1-255, on/off."""

    rgb: tuple[int, int, int] = LED_DEFAULT_COLOR
    brightness: int = LED_DEFAULT_BRIGHTNESS
    is_on: bool = False

    @classmethod
    def from_bg(cls, bg: str, previous: LedState | None = None) -> LedState:
        r, g, b = parse_hex(bg)
        peak = max(r, g, b)
        if not peak:
            prev = previous or cls()
            return cls(rgb=prev.rgb, brightness=prev.brightness, is_on=False)
        return cls(
            rgb=(round(r * 255 / peak), round(g * 255 / peak), round(b * 255 / peak)),
            brightness=peak,
            is_on=True,
        )

    def to_bg(self) -> str:
        if not self.is_on:
            return "#000000"
        r, g, b = (round(c * self.brightness / 255) for c in self.rgb)
        if max(r, g, b) == 0:
            r = g = b = 1
        return f"#{r:02x}{g:02x}{b:02x}"

    @property
    def color_name(self) -> str:
        if not self.is_on:
            return COLOR_OFF
        for name, preset in LED_COLORS.items():
            if all(abs(a - b) <= 24 for a, b in zip(self.rgb, preset)):
                return name
        return COLOR_CUSTOM

    @property
    def brightness_step(self) -> str:
        percent = self.brightness * 100 / 255
        return str(min(LED_BRIGHTNESS_STEPS, key=lambda step: abs(step - percent)))


class SmartfrontLeds:
    """Background colours of the Smartfront LEDs of one zApp."""

    def __init__(self, hass: HomeAssistant, entry_id: str, client: ZeptrionAirApiClient, leds: list[int]) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.client = client
        self.leds = leds
        self.states: dict[int, LedState] = {led: LedState() for led in leds}
        # Undo: states before each change (newest last), at most UNDO_DEPTH
        self.history: list[dict[int, LedState]] = []

    async def async_load(self) -> None:
        """Read the current colours once (setup)."""
        try:
            current = {int(d.get("id", 0)): str(d.get("bg", "")) for d in await self.client.async_get_leds()}
        except (ZeptrionAirApiClientError, ValueError) as err:
            _LOGGER.debug("Smartfront LEDs not readable: %s", err)
            return
        for led in self.leds:
            if led in current:
                self.states[led] = LedState.from_bg(current[led])

    async def async_set(
        self,
        led: int,
        *,
        on: bool = True,
        rgb: tuple[int, int, int] | None = None,
        brightness: int | None = None,
    ) -> None:
        """Change one LED; unspecified values keep their last setting."""
        old = self.states[led]
        new = LedState(
            rgb=tuple(int(c) for c in rgb) if rgb is not None else old.rgb,  # type: ignore[arg-type]
            brightness=max(1, min(255, int(brightness))) if brightness is not None else old.brightness,
            is_on=on,
        )
        if new == old:
            return
        try:
            await self.client.async_set_led_backgrounds({led: new.to_bg()})
        except ZeptrionAirApiClientError as err:
            raise HomeAssistantError(f"Smartfront LED {led}: setting the colour failed: {err}") from err
        self.history.append({led: old})
        del self.history[:-UNDO_DEPTH]
        self.states[led] = new
        async_dispatcher_send(self.hass, SIGNAL_LED.format(self.entry_id), led)

    @property
    def can_undo(self) -> bool:
        return bool(self.history)

    async def async_undo(self) -> bool:
        """Restore the LEDs as they were before the last change. False if nothing to undo."""
        if not self.history:
            return False
        previous = self.history[-1]
        try:
            await self.client.async_set_led_backgrounds({led: st.to_bg() for led, st in previous.items()})
        except ZeptrionAirApiClientError as err:
            raise HomeAssistantError(f"Smartfront LEDs: undo failed: {err}") from err
        self.history.pop()
        for led, state in previous.items():
            self.states[led] = state
            async_dispatcher_send(self.hass, SIGNAL_LED.format(self.entry_id), led)
        return True
