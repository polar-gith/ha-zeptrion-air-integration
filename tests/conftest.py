"""Fixtures for Zeptrion Air tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from aiohttp.test_utils import TestServer

from .fake_zapp import FakeZapp

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations in all tests."""
    yield


@pytest.fixture
async def fake_zapp(socket_enabled):
    """Run a simulated zApp on localhost; yields (device, 'host:port')."""
    device = FakeZapp()
    server = TestServer(device.app, host="127.0.0.1")
    await server.start_server()
    try:
        yield device, f"127.0.0.1:{server.port}"
    finally:
        await device.drop_websockets()
        await server.close()


@pytest.fixture(autouse=True)
def skip_frontend():
    """The card resource needs the full frontend, which tests do not load."""
    from unittest.mock import AsyncMock, patch

    with patch("custom_components.zeptrion_air.async_setup_frontend", AsyncMock()):
        yield


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


@pytest.fixture
def clock():
    clock = Clock()
    fake_time = SimpleNamespace(monotonic=clock.monotonic)
    with patch("custom_components.zeptrion_air.cover.time", fake_time):
        yield clock
