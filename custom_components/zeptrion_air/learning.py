"""Storing blind travel times in a hub's options."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant, callback


@callback
def async_store_travel_times(
    hass: HomeAssistant, entry: Any, values: dict[str, float]
) -> None:
    """Merge travel times into a hub's options (blinds take them over without a reload)."""
    if all(entry.options.get(key) == value for key, value in values.items()):
        return
    hass.config_entries.async_update_entry(entry, options={**entry.options, **values})
