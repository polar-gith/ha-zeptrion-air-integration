"""Diagnostics download for Zeptrion Air (host, serial and names redacted)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .data import ZeptrionAirData

TO_REDACT = {
    "hostname",
    "ip_address",
    "ip",
    "sn",
    "serial",
    "hub_serial",
    "unique_id",
    "title",
    "name",
    "group",
    "api_name",
    "api_group",
    "entity_base_name",
    "entry_title",
    "identifiers",
    "connections",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime_data: ZeptrionAirData | None = entry.runtime_data
    platform_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})

    diagnostics: dict[str, Any] = {
        "entry": {
            "title": entry.title,
            "unique_id": entry.unique_id,
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "channels": platform_data.get("identified_channels", []),
    }
    if runtime_data is not None:
        diagnostics["device"] = runtime_data.coordinator.data
        diagnostics["smartfront"] = runtime_data.smartfront
        diagnostics["keys"] = [key.as_attributes() for key in runtime_data.keys]
        diagnostics["connection"] = {
            "available": runtime_data.available,
            "stream_down": runtime_data.stream_down,
            "short_outages": runtime_data.short_outages,
            "event_stream": (
                runtime_data.websocket_listener.get_stats()
                if runtime_data.websocket_listener
                else None
            ),
        }
    return async_redact_data(diagnostics, TO_REDACT)
