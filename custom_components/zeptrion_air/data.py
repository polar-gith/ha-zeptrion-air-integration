"""Custom types for zeptrion_air."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable
from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from homeassistant.loader import Integration

    from .api import ZeptrionAirApiClient
    from .coordinator import ZeptrionAirDataUpdateCoordinator
    from .smartfront import KeyFunction
    from .websocket_listener import ZeptrionAirWebsocketListener

@dataclass
class ZeptrionAirData:
    """Data for the ZeptrionAir integration."""

    client: ZeptrionAirApiClient
    coordinator: ZeptrionAirDataUpdateCoordinator
    integration: Integration
    websocket_listener: "ZeptrionAirWebsocketListener | None" = None
    websocket_watchdog_cancel_callback: Callable[[], None] | None = None
    # Smartfront (None if the zApp has no Smartfront fitted)
    smartfront: dict[str, Any] | None = None
    keys: tuple[KeyFunction, ...] = ()
    # Event stream state: entities become unavailable only after the grace period
    available: bool = True
    stream_down: bool = False
    stream_down_since: float | None = None
    short_outages: int = 0
    unavailable_cancel_callback: Callable[[], None] | None = None

@dataclass
class ZeptrionAirConfigEntry(ConfigEntry):
    """Typed ConfigEntry for Zeptrion Air."""
    runtime_data: ZeptrionAirData | None = None

