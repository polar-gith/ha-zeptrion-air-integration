"""Zeptrion Air API Client.

Request rules, following the zApp protocol notes of diaznet/zeptrionair-api
(docs/protocol.md, sections 2 and 4):

* Reads (GET) are retried on timeouts, connection errors and HTTP 500.
* Commands (POST) are sent exactly once. A command like ``move_open_5000`` or
  ``dim_down_800`` that reached the device before a timeout would otherwise
  act twice.
* The zApp confirms commands with HTTP 302. The redirect is not followed.
* At most ``MAX_PARALLEL_REQUESTS`` requests run against one device at a time.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any
from urllib.parse import urlencode

import aiohttp
import xmltodict

from .const import MAX_PARALLEL_REQUESTS

_LOGGER = logging.getLogger(__name__)


class ZeptrionAirApiClientError(Exception):
    """Exception to indicate a general API error."""


class ZeptrionAirApiClientCommunicationError(
    ZeptrionAirApiClientError,
):
    """Exception to indicate a communication error."""


class ZeptrionAirApiClientNotFoundError(
    ZeptrionAirApiClientCommunicationError,
):
    """The device answered 404 (e.g. no Smartfront fitted)."""


class ZeptrionAirApiClient:
    """Zeptrion Air API Client."""

    def __init__(
        self,
        hostname: str,
        session: aiohttp.ClientSession,
        max_retries: int = 3,
        base_delay: float = 0.5,
        enable_jitter: bool = True,
        request_timeout: float = 10.0,
        max_parallel_requests: int = MAX_PARALLEL_REQUESTS,
    ) -> None:
        self._hostname = hostname
        self._baseurl = f"http://{hostname}"
        self._session = session
        self._max_retries = max(1, max_retries)
        self._base_delay = base_delay
        self._enable_jitter = enable_jitter
        self._request_timeout = request_timeout
        self._semaphore = asyncio.Semaphore(max(1, max_parallel_requests))

    @property
    def hostname(self) -> str:
        """Return the host name or IP address of the device."""
        return self._hostname

    @property
    def session(self) -> aiohttp.ClientSession:
        """Return the aiohttp session used for this device."""
        return self._session

    @property
    def websocket_url(self) -> str:
        """Return the URL of the device's event WebSocket."""
        return f"ws://{self._hostname}/zrap/ws"

    # ------------------------------------------------------------------ core

    def _calculate_retry_delay(self, attempt: int) -> float:
        """Exponential backoff with optional jitter."""
        delay = self._base_delay * (2**attempt)
        if self._enable_jitter:
            delay += random.uniform(0, 0.1)
        return delay

    async def _request(
        self,
        method: str,
        path: str,
        *,
        data: str | None = None,
        headers: dict[str, str] | None = None,
        retry: bool,
    ) -> tuple[int, str]:
        """Perform one HTTP request and return (status, body).

        Raises ZeptrionAirApiClientNotFoundError on 404 and
        ZeptrionAirApiClientCommunicationError on any other failure.
        """
        url = f"{self._baseurl}{path}"
        attempts = self._max_retries if retry else 1
        last_error: Exception | None = None

        for attempt in range(attempts):
            retryable = False
            try:
                async with self._semaphore:
                    async with asyncio.timeout(self._request_timeout):
                        async with self._session.request(
                            method,
                            url,
                            data=data,
                            headers=headers,
                            allow_redirects=False,
                        ) as response:
                            body = await response.text(errors="replace")
                            if response.status == 404:
                                raise ZeptrionAirApiClientNotFoundError(
                                    f"{method.upper()} {path} on {self._hostname}: not found (404)"
                                )
                            if response.status >= 400:
                                retryable = response.status >= 500
                                raise ZeptrionAirApiClientCommunicationError(
                                    f"{method.upper()} {path} on {self._hostname}: HTTP {response.status}"
                                )
                            return response.status, body
            except ZeptrionAirApiClientNotFoundError:
                raise
            except ZeptrionAirApiClientCommunicationError as err:
                last_error = err
            except TimeoutError as err:
                retryable = True
                last_error = ZeptrionAirApiClientCommunicationError(
                    f"{method.upper()} {path} on {self._hostname}: timeout after {self._request_timeout:.0f} s"
                )
                last_error.__cause__ = err
            except (aiohttp.ClientError, OSError) as err:
                retryable = True
                last_error = ZeptrionAirApiClientCommunicationError(
                    f"{method.upper()} {path} on {self._hostname}: {type(err).__name__}: {err}"
                )
                last_error.__cause__ = err

            if not retryable or attempt == attempts - 1:
                break
            delay = self._calculate_retry_delay(attempt)
            _LOGGER.debug(
                "%s (attempt %s/%s), retrying in %.1f s", last_error, attempt + 1, attempts, delay
            )
            await asyncio.sleep(delay)

        assert last_error is not None
        raise last_error

    @staticmethod
    def _parse_xml(body: str, what: str) -> dict[str, Any]:
        if not body.strip():
            return {}
        try:
            return xmltodict.parse(body)  # type: ignore[no-any-return]
        except Exception as err:  # xmltodict raises expat errors
            raise ZeptrionAirApiClientError(
                f"Failed to parse XML response of {what}: {err}. Response: {body[:200]}"
            ) from err

    async def _api_xml_wrapper(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Read an XML resource (GET, retried)."""
        _status, body = await self._request(method, path, headers=headers, retry=True)
        return self._parse_xml(body, path)

    async def _api_json_wrapper(self, path: str) -> Any:
        """Read a JSON resource (GET, retried)."""
        _status, body = await self._request("get", path, retry=True)
        try:
            return json.loads(body)
        except ValueError as err:
            raise ZeptrionAirApiClientError(
                f"Failed to parse JSON response of {path}: {err}. Response: {body[:200]}"
            ) from err

    async def _api_post_url_encoded_wrapper(
        self,
        path: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Send a command (POST, form-encoded, never retried, redirect not followed)."""
        _status, body = await self._request(
            "post",
            path,
            data=urlencode(data),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            retry=False,
        )
        if not body.strip():
            return {}
        try:
            return xmltodict.parse(body)  # type: ignore[no-any-return]
        except Exception:  # the 302 answer may carry a small HTML page
            return {"non_xml_response": body}

    # ------------------------------------------------------------ device info

    async def async_get_device_identification(self) -> dict[str, Any]:
        """Get the device identification from the API."""
        return await self._api_xml_wrapper(method="get", path="/zrap/id")

    async def async_get_rssi(self) -> int | None:
        """Read the Wi-Fi signal in dBm.

        Raises ZeptrionAirApiClientCommunicationError if the device does not
        answer; returns None if the answer cannot be parsed.
        """
        response_data = await self._api_xml_wrapper(method="get", path="/zrap/rssi")
        try:
            return int(response_data["rssi"]["dbm"])
        except (KeyError, TypeError, ValueError):
            _LOGGER.debug(
                "Unexpected RSSI answer from %s: %s", self._hostname, response_data
            )
            return None

    async def async_get_smartfront_info(self) -> dict[str, Any] | None:
        """Read /zapi/smartfront/id; None if no Smartfront is fitted (404)."""
        try:
            data = await self._api_json_wrapper("/zapi/smartfront/id")
        except ZeptrionAirApiClientNotFoundError:
            return None
        return data if isinstance(data, dict) else None

    async def async_get_smartfront_sensors(self) -> dict[str, Any] | None:
        """Read /zapi/smartfront/sensors (plural; the PDF's singular path is 404)."""
        try:
            data = await self._api_json_wrapper("/zapi/smartfront/sensors")
        except ZeptrionAirApiClientNotFoundError:
            return None
        return data if isinstance(data, dict) else None

    # ---------------------------------------------------------------- channels

    async def async_get_channel_scan_info(self, channel: int) -> dict[str, Any]:
        """Get the scan info for a specific channel."""
        return await self._api_xml_wrapper(
            method="get",
            path=f"/zrap/chscan/ch{channel}",
        )

    async def async_get_all_channels_scan_info(self) -> dict[str, Any]:
        """Get the scan info for all channels."""
        return await self._api_xml_wrapper(
            method="get",
            path="/zrap/chscan",
        )

    async def async_channel_open(self, channel: int) -> dict[str, Any]:
        """Send 'open' command to a channel."""
        return await self._api_post_url_encoded_wrapper(
            path=f"/zrap/chctrl/ch{channel}",
            data={"cmd": "open"},
        )

    async def async_channel_close(self, channel: int) -> dict[str, Any]:
        """Send 'close' command to a channel."""
        return await self._api_post_url_encoded_wrapper(
            path=f"/zrap/chctrl/ch{channel}",
            data={"cmd": "close"},
        )

    async def async_channel_stop(self, channel: int) -> dict[str, Any]:
        """Send 'stop' command to a channel."""
        return await self._api_post_url_encoded_wrapper(
            path=f"/zrap/chctrl/ch{channel}",
            data={"cmd": "stop"},
        )

    async def async_channel_move_open(self, channel: int, time_ms: int) -> dict[str, Any]:
        """Send 'move_open_{time_ms}' command to a channel."""
        return await self._api_post_url_encoded_wrapper(
            path=f"/zrap/chctrl/ch{channel}",
            data={"cmd": f"move_open_{time_ms}"},
        )

    async def async_channel_move_close(self, channel: int, time_ms: int) -> dict[str, Any]:
        """Send 'move_close_{time_ms}' command to a channel."""
        return await self._api_post_url_encoded_wrapper(
            path=f"/zrap/chctrl/ch{channel}",
            data={"cmd": f"move_close_{time_ms}"},
        )

    async def async_channel_recall_s1(self, channel: int) -> dict[str, Any]:
        """Send 'recall_s1' command to a channel."""
        return await self._api_post_url_encoded_wrapper(
            path=f"/zrap/chctrl/ch{channel}",
            data={"cmd": "recall_s1"},
        )

    async def async_channel_recall_s2(self, channel: int) -> dict[str, Any]:
        """Send 'recall_s2' command to a channel."""
        return await self._api_post_url_encoded_wrapper(
            path=f"/zrap/chctrl/ch{channel}",
            data={"cmd": "recall_s2"},
        )

    async def async_channel_recall_s3(self, channel: int) -> dict[str, Any]:
        """Send 'recall_s3' command to a channel."""
        return await self._api_post_url_encoded_wrapper(
            path=f"/zrap/chctrl/ch{channel}",
            data={"cmd": "recall_s3"},
        )

    async def async_channel_recall_s4(self, channel: int) -> dict[str, Any]:
        """Send 'recall_s4' command to a channel."""
        return await self._api_post_url_encoded_wrapper(
            path=f"/zrap/chctrl/ch{channel}",
            data={"cmd": "recall_s4"},
        )

    async def async_get_channel_descriptions(self) -> dict[str, Any]:
        """Fetch channel descriptions from /zrap/chdes."""
        _LOGGER.debug(f"Fetching channel descriptions from /zrap/chdes for {self._hostname}")
        try:
            response_data = await self._api_xml_wrapper(
                method="get",
                path="/zrap/chdes",
            )
            return response_data
        except ZeptrionAirApiClientCommunicationError as e:
            _LOGGER.error(f"Communication error fetching channel descriptions from {self._hostname}: {e}")
            raise
        except ZeptrionAirApiClientError as e:  # Catch other client errors
            _LOGGER.error(f"API client error fetching channel descriptions from {self._hostname}: {e}")
            raise

    async def async_channel_on(self, channel: int) -> dict[str, Any]:
        """Send 'on' command to a channel for light control."""
        _LOGGER.debug(f"Sending 'on' command to channel {channel} on {self._hostname}")
        return await self._api_post_url_encoded_wrapper(
            path=f"/zrap/chctrl/ch{channel}",
            data={"cmd": "on"},
        )

    async def async_channel_off(self, channel: int) -> dict[str, Any]:
        """Send 'off' command to a channel for light control."""
        _LOGGER.debug(f"Sending 'off' command to channel {channel} on {self._hostname}")
        return await self._api_post_url_encoded_wrapper(
            path=f"/zrap/chctrl/ch{channel}",
            data={"cmd": "off"},
        )

    async def async_channel_dim_down_for(self, channel: int, time_ms: int) -> dict[str, Any]:
        """Dim light down for a specified time in milliseconds."""
        time_ms = max(100, min(32000, int(time_ms)))
        _LOGGER.debug(
            f"Sending 'dim_down_({time_ms})' command to channel {channel} on {self._hostname}"
        )
        return await self._api_post_url_encoded_wrapper(
            path=f"/zrap/chctrl/ch{channel}",
            data={"cmd": f"dim_down_{time_ms}"},
        )

    async def async_channel_dim_up_for(self, channel: int, time_ms: int) -> dict[str, Any]:
        """Dim light up for a specified time in milliseconds."""
        time_ms = max(100, min(32000, int(time_ms)))
        _LOGGER.debug(
            f"Sending 'dim_up_({time_ms})' command to channel {channel} on {self._hostname}"
        )
        return await self._api_post_url_encoded_wrapper(
            path=f"/zrap/chctrl/ch{channel}",
            data={"cmd": f"dim_up_{time_ms}"},
        )

    async def async_channel_set_brightness(self, channel: int, brightness_0_255: int) -> dict[str, Any]:
        """Set brightness using a calibrated press-and-hold strategy."""
        brightness_0_255 = max(0, min(255, int(brightness_0_255)))

        if brightness_0_255 == 0:
            return await self.async_channel_off(channel)

        if brightness_0_255 == 255:
            return await self.async_channel_on(channel)

        full_travel_ms = 3400
        dim_down_ms = round((255 - brightness_0_255) / 255 * full_travel_ms)
        dim_down_ms = max(100, min(full_travel_ms, dim_down_ms))

        _LOGGER.debug(
            f"Setting brightness on channel {channel}: "
            f"HA brightness={brightness_0_255}, dim_down_ms={dim_down_ms}"
        )

        await self.async_channel_on(channel)
        await asyncio.sleep(0.25)
        return await self.async_channel_dim_down_for(channel, dim_down_ms)
