"""ZeptrionAirEntity class."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ZeptrionAirDataUpdateCoordinator


def channel_device_identifier(
    hub_serial: str, channel_id: int, single_channel: bool
) -> tuple[str, str]:
    """Return the device registry identifier for a channel's entities.

    Devices with more than one usable channel get one sub-device per channel,
    linked to the wall device via ``via_device_id``. On single-channel devices
    such a sub-device only duplicates the wall device, so the channel's
    entities are attached to the wall (hub) device directly.
    """
    if single_channel:
        return (DOMAIN, hub_serial)
    return (DOMAIN, f"{hub_serial}_ch{channel_id}")


class ZeptrionAirEntity(CoordinatorEntity[ZeptrionAirDataUpdateCoordinator]):
    """ZeptrionAirEntity class."""

    def __init__(self, coordinator: ZeptrionAirDataUpdateCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_unique_id = coordinator.config_entry.entry_id

        # Helper to safely access potentially nested data
        hub_data = coordinator.data.get('id', {}) if coordinator.data else {}
        
        self._attr_device_info = DeviceInfo(
            identifiers={
                (
                    coordinator.config_entry.domain,
                    coordinator.config_entry.unique_id, # Corrected: use unique_id (serial number)
                ),
            },
            name=coordinator.config_entry.title, # Use the hub's name
            model=hub_data.get('type'), # Get model from API data
            sw_version=hub_data.get('sw'), # Get software version from API data
            manufacturer="Feller AG", # Set manufacturer to "Feller AG"
        )
