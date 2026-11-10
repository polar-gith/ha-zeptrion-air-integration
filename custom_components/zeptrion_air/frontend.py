"""Frontend support for the Zeptrion Air integration (blinds card)."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from aiohttp import web

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CARD_URL = "/api/zeptrion_air/zeptrion-air-blinds-card.js"

# Key in hass.data[DOMAIN] that marks the frontend as set up for this HA run.
# The integration is set up once per config entry (one per zApp device), but
# the view, the extra JS URL and the Lovelace resource must exist only once.
_FRONTEND_SETUP_KEY = "_frontend_setup_done"


class ZeptrionAirCardView(HomeAssistantView):
    """Serve the custom card."""

    url = CARD_URL
    name = "api:zeptrion_air:card"
    requires_auth = False

    async def get(self, request):
        """Serve the card JS file."""
        card_path = os.path.join(os.path.dirname(__file__), "www", "zeptrion-air-blinds-card.js")

        if not os.path.exists(card_path):
            return web.Response(status=404)

        def _read_file():
            """Read file in executor."""
            with open(card_path, "r", encoding="utf-8") as f:
                return f.read()

        try:
            content = await request.app["hass"].async_add_executor_job(_read_file)
        except Exception as e:  # noqa: BLE001
            _LOGGER.error("Error reading card file: %s", e)
            return web.Response(status=500)

        return web.Response(
            text=content,
            content_type="text/javascript",
            headers={
                "Cache-Control": "no-cache",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
            },
        )


def _base_url(url: Any) -> str:
    """Return a resource URL without query string (e.g. a ?v= cache buster)."""
    return str(url or "").split("?", 1)[0]


async def _async_ensure_lovelace_resource(hass: HomeAssistant) -> None:
    """Make sure the card is registered exactly once as a Lovelace resource.

    The resource collection is loaded lazily by Home Assistant. Reading
    ``resources.data`` before it is loaded returns an empty dict, which made
    earlier versions add a new resource on every start. Load it first, then
    create the resource if missing and remove duplicates left behind by
    earlier versions.
    """
    lovelace_data = hass.data.get("lovelace")
    resources = getattr(lovelace_data, "resources", None)
    if resources is None:
        _LOGGER.debug("Lovelace resources not available; skipping resource registration")
        return

    # YAML-mode resources cannot be edited from an integration.
    if not hasattr(resources, "async_create_item"):
        _LOGGER.debug("Lovelace resources are in YAML mode; add %s manually if needed", CARD_URL)
        return

    # Force the storage collection to load (async_get_info loads it if needed).
    if not getattr(resources, "loaded", True):
        if hasattr(resources, "async_get_info"):
            await resources.async_get_info()
        else:
            await resources.async_load()
            resources.loaded = True

    matches = [
        item
        for item in resources.async_items()
        if _base_url(item.get("url")) == CARD_URL
    ]

    if not matches:
        await resources.async_create_item({"url": CARD_URL, "res_type": "module"})
        _LOGGER.info("Added Zeptrion Air card to Lovelace resources")
        return

    # Keep the first entry, remove all duplicates.
    for duplicate in matches[1:]:
        try:
            await resources.async_delete_item(duplicate["id"])
            _LOGGER.info(
                "Removed duplicate Zeptrion Air card resource %s", duplicate["id"]
            )
        except Exception as e:  # noqa: BLE001
            _LOGGER.warning(
                "Could not remove duplicate card resource %s: %s", duplicate.get("id"), e
            )


async def async_setup_frontend(hass: HomeAssistant, entry) -> None:
    """Set up frontend components once per Home Assistant run."""
    domain_data: dict[str, Any] = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_FRONTEND_SETUP_KEY):
        return
    domain_data[_FRONTEND_SETUP_KEY] = True

    _LOGGER.info("Setting up Zeptrion Air frontend")
    hass.http.register_view(ZeptrionAirCardView())
    add_extra_js_url(hass, CARD_URL)

    lock = asyncio.Lock()

    async def delayed_setup(_now) -> None:
        async with lock:
            try:
                await _async_ensure_lovelace_resource(hass)
            except Exception as e:  # noqa: BLE001
                _LOGGER.warning("Could not register Lovelace resource: %s", e)

    # Give Lovelace a moment to finish its own setup.
    async_call_later(hass, 5, delayed_setup)
