"""Config flow tests: user, discovery (zeroconf, DHCP), confirm, reconfigure, options."""

from __future__ import annotations

from ipaddress import ip_address
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from custom_components.zeptrion_air.api import (
    ZeptrionAirApiClientCommunicationError,
    ZeptrionAirApiClientError,
)
from custom_components.zeptrion_air.const import (
    CONF_HOSTNAME,
    CONF_IP_ADDRESS,
    CONF_STEP_DURATION_MS,
    DOMAIN,
)


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


# ----------------------------------------------------------------- user flow


async def test_user_flow_creates_entry(hass: HomeAssistant, fake_zapp) -> None:
    device, host = fake_zapp
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    with patch("custom_components.zeptrion_air.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOSTNAME: host, CONF_STEP_DURATION_MS: 300}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == device.serial
    assert result["data"] == {CONF_HOSTNAME: host, CONF_STEP_DURATION_MS: 300}


async def test_user_flow_already_configured(hass: HomeAssistant, fake_zapp) -> None:
    device, host = fake_zapp
    MockConfigEntry(domain=DOMAIN, unique_id=device.serial, data={CONF_HOSTNAME: host}).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOSTNAME: host})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_user_flow_cannot_connect_then_recovers(hass: HomeAssistant, fake_zapp) -> None:
    device, host = fake_zapp
    device.http_down = True
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOSTNAME: host})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    device.http_down = False
    with patch("custom_components.zeptrion_air.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOSTNAME: host})
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize(
    ("side_effect", "answer", "error"),
    [
        (ZeptrionAirApiClientError("bad answer"), None, "unknown"),
        (RuntimeError("boom"), None, "unknown"),
        (None, {"id": {"type": "3340-2-B"}}, "unknown"),  # no serial number
    ],
)
async def test_user_flow_errors(hass: HomeAssistant, side_effect, answer, error) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    with patch(
        "custom_components.zeptrion_air.config_flow.ZeptrionAirApiClient.async_get_device_identification",
        side_effect=side_effect,
        return_value=answer,
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOSTNAME: "zapp-1.local"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


# ------------------------------------------------------------ discovery flow


def _zeroconf(hostname: str) -> ZeroconfServiceInfo:
    return ZeroconfServiceInfo(
        ip_address=ip_address("192.168.20.61"),
        ip_addresses=[ip_address("192.168.20.61")],
        hostname=hostname,
        name=f"{hostname.split('.')[0]}._zapp._tcp.local.",
        port=80,
        properties={},
        type="_zapp._tcp.local.",
    )


async def test_zeroconf_new_zapp_asks_for_confirmation(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=_zeroconf("zapp-23040121.local.")
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    flow = hass.config_entries.flow.async_get(result["flow_id"])
    assert flow["context"]["unique_id"] == "23040121"
    assert flow["context"]["title_placeholders"] == {"name": "zapp-23040121"}


async def test_zeroconf_updates_configured_zapp(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="23040121",
        data={CONF_HOSTNAME: "zapp-23040121.local", CONF_IP_ADDRESS: "192.168.20.40"},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=_zeroconf("zapp-23040121.local.")
    )
    assert result["type"] is FlowResultType.ABORT
    assert entry.data[CONF_IP_ADDRESS] == "192.168.20.61"


async def test_zeroconf_hostname_without_serial(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=_zeroconf("zapp-.local.")
    )
    # no serial in the host name: falls back to the host name as unique ID and still asks
    assert result["step_id"] == "confirm"


async def test_dhcp_other_host_is_ignored(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_DHCP},
        data=DhcpServiceInfo(ip="192.168.20.70", hostname="printer", macaddress="aabbccddee00"),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_zeptrion_device"


async def test_confirm_creates_entry_with_serial(hass: HomeAssistant, fake_zapp) -> None:
    device, host = fake_zapp
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_DHCP},
        data=DhcpServiceInfo(ip=host, hostname=f"zapp-{device.serial}", macaddress="aabbccddeeff"),
    )
    assert result["step_id"] == "confirm"
    with patch("custom_components.zeptrion_air.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_STEP_DURATION_MS: 500})
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == device.serial
    assert result["data"][CONF_STEP_DURATION_MS] == 500
    assert result["data"][CONF_HOSTNAME] == host


@pytest.mark.parametrize(
    ("side_effect", "answer", "error"),
    [
        (ZeptrionAirApiClientCommunicationError("down"), None, "cannot_connect"),
        (ZeptrionAirApiClientError("bad answer"), None, "unknown"),
        (None, {"id": {}}, "unknown"),
    ],
)
async def test_confirm_errors(hass: HomeAssistant, side_effect, answer, error) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_DHCP},
        data=DhcpServiceInfo(ip="192.168.20.50", hostname="zapp-23040121", macaddress="aabbccddeeff"),
    )
    with patch(
        "custom_components.zeptrion_air.config_flow.ZeptrionAirApiClient.async_get_device_identification",
        side_effect=side_effect,
        return_value=answer,
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_STEP_DURATION_MS: 250})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


async def test_reconfigure_cannot_connect(hass: HomeAssistant, fake_zapp) -> None:
    device, host = fake_zapp
    device.http_down = True
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=device.serial, minor_version=4, data={CONF_HOSTNAME: "192.168.20.40"}
    )
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOSTNAME: host})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


# --------------------------------------------------------------- options flow


async def test_options_flow_with_blind(hass: HomeAssistant, fake_zapp) -> None:
    device, host = fake_zapp
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=device.serial, minor_version=4,
        data={CONF_HOSTNAME: host, CONF_STEP_DURATION_MS: 250},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    fields = {str(key) for key in result["data_schema"].schema}
    assert {"step_duration_ms", "auto_travel_times", "travel_up_ch1", "travel_down_ch1", "tilt_time_ch1"} <= fields
    assert "travel_up_ch2" not in fields  # channel 2 is a light

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"step_duration_ms": 600, "auto_travel_times": False, "travel_up_ch1": 30.0,
         "travel_down_ch1": 29.0, "tilt_time_ch1": 1.0},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["step_duration_ms"] == 600
    assert entry.options["travel_up_ch1"] == 30.0
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.parametrize(
    ("side_effect", "answer"),
    [(ZeptrionAirApiClientError("bad answer"), None), (None, {"id": {}})],
)
async def test_reconfigure_errors(hass: HomeAssistant, side_effect, answer) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="23040121", minor_version=4, data={CONF_HOSTNAME: "192.168.20.40"}
    )
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    with patch(
        "custom_components.zeptrion_air.config_flow.ZeptrionAirApiClient.async_get_device_identification",
        side_effect=side_effect,
        return_value=answer,
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOSTNAME: "192.168.20.41"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}
