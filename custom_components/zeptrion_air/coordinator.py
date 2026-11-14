"""DataUpdateCoordinator for zeptrion_air."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    ZeptrionAirApiClientError,
)
from .const import (
    DOMAIN,
    LOGGER,
    WEAK_WIFI_CLEAR_DBM,
    WEAK_WIFI_DBM,
    WEAK_WIFI_POLLS,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    
    from .api import ZeptrionAirApiClient
    from .data import ZeptrionAirConfigEntry


# https://developers.home-assistant.io/docs/integration_fetching_data#coordinated-single-api-poll-for-data-for-all-entities
class ZeptrionAirDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the API."""

    config_entry: ZeptrionAirConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        client: ZeptrionAirApiClient,
    ) -> None:
        """Initialize."""
        self.client = client
        self.weak_wifi_polls = 0
        super().__init__(
            hass=hass,
            logger=LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=1),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Update data via library, including device identification and RSSI."""
        if self.config_entry.runtime_data is None:
            LOGGER.error("Coordinator: Cannot update data, runtime_data is not available. Integration might not have been set up correctly or has been unloaded.")
            raise UpdateFailed("runtime_data not available")
        if self.client is None:
            LOGGER.error("Coordinator: Cannot update data, API client is not available. Integration might not have been set up correctly.")
            raise UpdateFailed("API client not available")
        try:
            # Fetch device identification data
            data: dict[str, Any] = await self.client.async_get_device_identification()
            
            # Fetch RSSI data
            # async_get_rssi is expected to return int | None
            # It will raise ZeptrionAirApiClientCommunicationError on communication issues,
            # or return None on parsing issues/other non-communication API errors.
            # A zApp whose /zrap/rssi answers HTTP 500 (seen on firmware 2018)
            # must not make the other diagnostic sensors unavailable.
            try:
                rssi_value: int | None = await self.client.async_get_rssi()
            except ZeptrionAirApiClientError as err:
                LOGGER.debug("Coordinator: RSSI not readable: %s", err)
                rssi_value = None
            self._check_wifi(rssi_value)
            
            # Add RSSI to the data dictionary
            # Storing None if RSSI could not be fetched/parsed, allows entities to handle it.
            data['rssi_dbm'] = rssi_value

            # Smartfront sensors (temperature, light, humidity) if a Smartfront is fitted
            if self.config_entry.runtime_data.smartfront:
                try:
                    data['smartfront_sensors'] = await self.client.async_get_smartfront_sensors()
                except ZeptrionAirApiClientError as err:
                    LOGGER.debug("Coordinator: Smartfront sensors not readable: %s", err)
                    data['smartfront_sensors'] = None

            LOGGER.debug("Coordinator data update: %s", data)
            return data
        except ZeptrionAirApiClientError as exception:
            # This will catch errors from both async_get_device_identification and async_get_rssi
            raise UpdateFailed(f"Error communicating with API: {exception}") from exception

    def _check_wifi(self, rssi: int | None) -> None:
        """Raise a repair issue after a weak signal for WEAK_WIFI_POLLS polls.

        Feller recommends warning below -75 dBm (API 3.8.1). The issue clears
        once the signal is back at WEAK_WIFI_CLEAR_DBM or better (hysteresis).
        """
        if rssi is None:
            return
        entry = self.config_entry
        issue_id = f"weak_wifi_{entry.unique_id or entry.entry_id}"
        if rssi < WEAK_WIFI_DBM:
            self.weak_wifi_polls += 1
            if self.weak_wifi_polls == WEAK_WIFI_POLLS:
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    issue_id,
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key="weak_wifi",
                    translation_placeholders={
                        "name": entry.title,
                        "rssi": str(rssi),
                        "limit": str(WEAK_WIFI_DBM),
                    },
                )
        elif rssi >= WEAK_WIFI_CLEAR_DBM:
            if self.weak_wifi_polls >= WEAK_WIFI_POLLS:
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            self.weak_wifi_polls = 0
