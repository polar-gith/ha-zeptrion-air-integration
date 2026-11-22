"""API client against a zApp that answers nonsense."""

from __future__ import annotations

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from custom_components.zeptrion_air.api import (
    ZeptrionAirApiClient,
    ZeptrionAirApiClientCommunicationError,
    ZeptrionAirApiClientError,
    ZeptrionAirApiClientNotFoundError,
)


@pytest.fixture
async def odd_zapp(socket_enabled):
    """A zApp with broken answers: XML/JSON that does not parse, missing paths."""
    answers = {
        "/zrap/id": "<id><sn>1</sn",  # broken XML
        "/zrap/rssi": "<rssi><dbm>n/a</dbm></rssi>",
        "/zrap/chdes": "<chdes",
        "/zapi/smartfront/id": "{nope",
        "/zapi/smartfront/led": '{"not": "a list"}',
        "/zrap/chctrl/ch1": "<broken",
    }

    async def handle(request: web.Request) -> web.StreamResponse:
        if request.path in answers:
            return web.Response(text=answers[request.path])
        raise web.HTTPNotFound()

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handle)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    try:
        yield answers, f"127.0.0.1:{server.port}"
    finally:
        await server.close()


async def test_broken_answers(odd_zapp) -> None:
    answers, host = odd_zapp
    async with aiohttp.ClientSession() as session:
        client = ZeptrionAirApiClient(host, session, base_delay=0.01)
        with pytest.raises(ZeptrionAirApiClientError):
            await client.async_get_device_identification()
        assert await client.async_get_rssi() is None
        with pytest.raises(ZeptrionAirApiClientError):
            await client.async_get_channel_descriptions()
        assert await client.async_get_leds() == []
        assert await client.async_channel_on(1) == {"non_xml_response": "<broken"}
        with pytest.raises(ValueError):
            await client.async_channel_store_scene(1, 5)
        with pytest.raises(ZeptrionAirApiClientNotFoundError):
            await client.async_smartbutton_program([{"req": "POST"}], wait_s=1)
        answers["/zapi/smartfront/led"] = ""
        with pytest.raises(ZeptrionAirApiClientError):
            await client.async_get_leds()


async def test_unreachable_channel_descriptions(socket_enabled) -> None:
    async with aiohttp.ClientSession() as session:
        client = ZeptrionAirApiClient("127.0.0.1:1", session, max_retries=1, base_delay=0.01)
        with pytest.raises(ZeptrionAirApiClientCommunicationError):
            await client.async_get_channel_descriptions()
