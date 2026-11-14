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


async def test_http_service_of_other_device_is_ignored(hass: HomeAssistant) -> None:
    from ipaddress import ip_address

    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=ZeroconfServiceInfo(
            ip_address=ip_address("192.168.20.60"),
            ip_addresses=[ip_address("192.168.20.60")],
            hostname="printer.local.",
            name="printer._http._tcp.local.",
            port=80,
            properties={},
            type="_http._tcp.local.",
        ),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_zeptrion_device"


async def test_reconfigure_changes_the_address(hass: HomeAssistant, fake_zapp) -> None:
    device, host = fake_zapp
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=device.serial, minor_version=2,
        data={CONF_HOSTNAME: "192.168.20.40"},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOSTNAME: host}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOSTNAME] == host
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_reconfigure_refuses_another_zapp(hass: HomeAssistant, fake_zapp) -> None:
    _device, host = fake_zapp
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="99999999", minor_version=2,
        data={CONF_HOSTNAME: "192.168.20.40"},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOSTNAME: host}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_device"
    assert entry.data[CONF_HOSTNAME] == "192.168.20.40"
