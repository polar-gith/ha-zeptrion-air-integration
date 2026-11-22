"""Card resource registration (once, duplicates removed) and the diagnostics download."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.zeptrion_air import frontend
from custom_components.zeptrion_air.const import CONF_HOSTNAME, DOMAIN
from custom_components.zeptrion_air.diagnostics import async_get_config_entry_diagnostics


class FakeResources:
    """Lovelace resource collection in storage mode, loaded lazily."""

    def __init__(self, items: list[dict]) -> None:
        self.items = list(items)
        self.loaded = False
        self.deleted: list[str] = []

    async def async_get_info(self) -> dict:
        self.loaded = True
        return {}

    def async_items(self) -> list[dict]:
        assert self.loaded, "read before load"
        return self.items

    async def async_create_item(self, data: dict) -> dict:
        item = {"id": f"id{len(self.items)}", **data}
        self.items.append(item)
        return item

    async def async_delete_item(self, item_id: str) -> None:
        self.deleted.append(item_id)
        self.items = [i for i in self.items if i["id"] != item_id]


async def test_resource_created_once(hass: HomeAssistant) -> None:
    resources = FakeResources([{"id": "other", "url": "/local/other.js"}])
    hass.data["lovelace"] = SimpleNamespace(resources=resources)
    await frontend._async_ensure_lovelace_resource(hass)
    await frontend._async_ensure_lovelace_resource(hass)
    urls = [i["url"] for i in resources.items]
    assert urls.count(frontend.CARD_URL) == 1
    assert resources.items[-1]["res_type"] == "module"


async def test_resource_duplicates_removed(hass: HomeAssistant) -> None:
    resources = FakeResources(
        [
            {"id": "a", "url": frontend.CARD_URL},
            {"id": "b", "url": f"{frontend.CARD_URL}?v=1"},
            {"id": "c", "url": frontend.CARD_URL},
        ]
    )
    hass.data["lovelace"] = SimpleNamespace(resources=resources)
    await frontend._async_ensure_lovelace_resource(hass)
    assert resources.deleted == ["b", "c"]
    assert [i["id"] for i in resources.items] == ["a"]


async def test_resource_yaml_mode_and_missing_lovelace(hass: HomeAssistant) -> None:
    hass.data.pop("lovelace", None)
    await frontend._async_ensure_lovelace_resource(hass)  # no Lovelace: nothing to do
    hass.data["lovelace"] = SimpleNamespace(resources=SimpleNamespace())  # YAML mode
    await frontend._async_ensure_lovelace_resource(hass)


async def test_setup_frontend_once(hass: HomeAssistant) -> None:
    resources = FakeResources([])
    hass.data["lovelace"] = SimpleNamespace(resources=resources)
    entry = MockConfigEntry(domain=DOMAIN)
    with (
        patch.object(hass, "http", SimpleNamespace(async_register_static_paths=AsyncMock()), create=True),
        patch.object(frontend, "add_extra_js_url") as extra,
    ):
        await frontend.async_setup_frontend(hass, entry)
        await frontend.async_setup_frontend(hass, entry)  # second zApp: no second registration
        assert hass.http.async_register_static_paths.await_count == 1
        extra.assert_called_once_with(hass, frontend.CARD_URL)
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
        await hass.async_block_till_done()
    assert [i["url"] for i in resources.items] == [frontend.CARD_URL]


async def test_diagnostics_redacted(hass: HomeAssistant, fake_zapp) -> None:
    device, host = fake_zapp
    entry = MockConfigEntry(
        domain=DOMAIN, title=f"zapp-{device.serial}", unique_id=device.serial, minor_version=4,
        data={CONF_HOSTNAME: host},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    async with asyncio.timeout(5):
        while not entry.runtime_data.websocket_listener.connected:
            await asyncio.sleep(0.02)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["entry"]["data"]["hostname"] == "**REDACTED**"
    assert diagnostics["entry"]["unique_id"] == "**REDACTED**"
    assert diagnostics["connection"]["available"] is True
    assert diagnostics["connection"]["event_stream"]["connected"] is True
    assert len(diagnostics["channels"]) == 2
    assert diagnostics["keys"]  # Smartfront keys decoded
    assert device.serial not in str(diagnostics)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
