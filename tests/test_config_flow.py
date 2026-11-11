"""DHCP discovery tests."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zeptrion_air.const import CONF_HOSTNAME, CONF_IP_ADDRESS, DOMAIN


async def test_dhcp_new_device_asks_for_confirmation(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_DHCP},
        data=DhcpServiceInfo(ip="192.168.20.50", hostname="zapp-23040121", macaddress="aabbccddeeff"),
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"


async def test_dhcp_updates_ip_of_configured_device(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="23040121",
        data={CONF_HOSTNAME: "192.168.20.40", CONF_IP_ADDRESS: "192.168.20.40"},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_DHCP},
        data=DhcpServiceInfo(ip="192.168.20.50", hostname="zapp-23040121", macaddress="aabbccddeeff"),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOSTNAME] == "192.168.20.50"
    assert entry.data[CONF_IP_ADDRESS] == "192.168.20.50"


async def test_dhcp_keeps_mdns_hostname(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="23040121",
        data={CONF_HOSTNAME: "zapp-23040121.local", CONF_IP_ADDRESS: "192.168.20.40"},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_DHCP},
        data=DhcpServiceInfo(ip="192.168.20.50", hostname="zapp-23040121", macaddress="aabbccddeeff"),
    )
    assert entry.data[CONF_HOSTNAME] == "zapp-23040121.local"
    assert entry.data[CONF_IP_ADDRESS] == "192.168.20.50"
