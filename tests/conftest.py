"""Fixtures for Zeptrion Air tests."""

from __future__ import annotations

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


@pytest.fixture(autouse=True)
def device_registry_compat():
    """Test-only shim: the test environment runs an older Home Assistant (2026.2,
    the newest available for Python 3.13) than the integration requires
    (2026.8, for ``async_get_device_by_identifier`` and ``via_device_id``)."""
    from homeassistant.helpers import device_registry as dr

    if hasattr(dr.DeviceRegistry, "async_get_device_by_identifier"):
        yield
        return

    def _by_identifier(self, identifier, config_entry_id=None):
        return self.async_get_device(identifiers={identifier})

    original_get_or_create = dr.DeviceRegistry.async_get_or_create

    def _get_or_create(self, *args, via_device_id=None, **kwargs):
        return original_get_or_create(self, *args, **kwargs)

    dr.DeviceRegistry.async_get_device_by_identifier = _by_identifier
    dr.DeviceRegistry.async_get_or_create = _get_or_create
    try:
        yield
    finally:
        del dr.DeviceRegistry.async_get_device_by_identifier
        dr.DeviceRegistry.async_get_or_create = original_get_or_create
