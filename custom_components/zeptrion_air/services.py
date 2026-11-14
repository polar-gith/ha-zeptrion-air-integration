"""Integration-wide actions: program a Smartbutton, write names back to the zApp."""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol
from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.service import async_extract_entity_ids

from .api import (
    CHDES_TEXT_BYTES,
    ZeptrionAirApiClientError,
    ZeptrionAirApiClientNotFoundError,
    compact_smartbutton_requests,
    truncate_utf8,
)
from .const import (
    CONF_HOSTNAME,
    CONF_IP_ADDRESS,
    DOMAIN,
    SERVICE_PROGRAM_SMARTBUTTON,
    SERVICE_WRITE_NAME,
    SIGNAL_CHANNEL_DESCRIPTOR,
)

_LOGGER = logging.getLogger(__name__)

# Commands a Smartbutton may send to a channel (Feller API 3.6.3, without delete_s)
_PLAIN_COMMANDS = {
    "on", "off", "toggle", "stop", "open", "close", "dim_up", "dim_down",
    "move_open", "move_close", "recall_s1", "recall_s2", "recall_s3", "recall_s4",
}
_TIMED_COMMAND = re.compile(r"(move_open|move_close|dim_up|dim_down|dim)_(\d{3,5})")
_CHANNEL_UID = re.compile(r"zapp_(\d+)_ch(\d)(?:_light)?$")
_REQUEST_KEYS = ("req", "loc", "pth", "typ", "hdr", "prt", "bdy")


def valid_command(value: Any) -> str:
    """A channel command, timed commands within 100-32000 ms."""
    command = str(value).strip()
    if command in _PLAIN_COMMANDS:
        return command
    match = _TIMED_COMMAND.fullmatch(command)
    if match and 100 <= int(match.group(2)) <= 32000:
        return command
    raise vol.Invalid(f"unknown command {command!r}")


PROGRAM_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Required("device_id"): cv.string,
            vol.Optional("actions"): vol.All(
                cv.ensure_list,
                [vol.Schema({vol.Required("entity_id"): cv.entity_id, vol.Required("command"): valid_command})],
            ),
            vol.Optional("requests"): vol.All(
                cv.ensure_list,
                [vol.Schema({vol.Optional(k): cv.string for k in _REQUEST_KEYS})],
            ),
            vol.Optional("wait", default=60): vol.All(vol.Coerce(int), vol.Range(min=10, max=120)),
        }
    ),
    cv.has_at_least_one_key("actions", "requests"),
)


def _loaded_entry(hass: HomeAssistant, entry_id: str):
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED:
        return None
    return entry


def _entry_of_device(hass: HomeAssistant, device_id: str):
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise ServiceValidationError(f"Device {device_id} not found")
    for entry_id in device.config_entries:
        if (entry := _loaded_entry(hass, entry_id)) is not None:
            return entry
    raise ServiceValidationError(f"{device.name_by_user or device.name} is not a loaded zApp")


def _host_of(entry) -> str:
    """Address another zApp is reached at: the IP address when known."""
    return str(entry.data.get(CONF_IP_ADDRESS) or entry.data.get(CONF_HOSTNAME))


def _request_for_action(hass: HomeAssistant, entity_id: str, command: str) -> dict[str, str]:
    reg_entry = er.async_get(hass).async_get(entity_id)
    if reg_entry is None or reg_entry.platform != DOMAIN or not reg_entry.config_entry_id:
        raise ServiceValidationError(f"{entity_id} is not a Zeptrion Air blind or light")
    match = _CHANNEL_UID.fullmatch(reg_entry.unique_id)
    target = _loaded_entry(hass, reg_entry.config_entry_id)
    if match is None or target is None:
        raise ServiceValidationError(f"{entity_id} is not a Zeptrion Air blind or light")
    return {
        "req": "POST",
        "loc": _host_of(target),
        "pth": f"/zrap/chctrl/ch{match.group(2)}",
        "typ": "application/x-www-form-urlencoded",
        "bdy": f"cmd={command}",
    }


async def _async_program_smartbutton(hass: HomeAssistant, call: ServiceCall) -> None:
    entry = _entry_of_device(hass, call.data["device_id"])
    runtime = entry.runtime_data
    if not runtime.smartfront or not any(k.kind == "smartbutton" for k in runtime.keys):
        raise ServiceValidationError(f"{entry.title} has no Smartbutton")
    requests: list[dict[str, str]] = [
        _request_for_action(hass, a["entity_id"], a["command"]) for a in call.data.get("actions", [])
    ]
    requests += [dict(r) for r in call.data.get("requests", [])]
    try:
        compact_smartbutton_requests(requests)  # size check before anything blinks
    except ValueError as err:
        raise ServiceValidationError(str(err)) from err
    wait = int(call.data["wait"])
    notification_id = f"{DOMAIN}_smartbutton_{entry.entry_id}"
    persistent_notification.async_create(
        hass,
        f"Press the Smartbutton to program on **{entry.title}** within {wait} s "
        f"(the Smartbuttons blink). Its previous programme is replaced.",
        title="Zeptrion Air: program Smartbutton",
        notification_id=notification_id,
    )

    async def _run() -> None:
        try:
            pressed = await runtime.client.async_smartbutton_program(requests, wait_s=wait)
        except ZeptrionAirApiClientNotFoundError:
            message = f"{entry.title}: this Smartfront cannot be programmed from Home Assistant."
        except (ZeptrionAirApiClientError, ValueError) as err:
            message = f"{entry.title}: programming failed: {err}"
        else:
            message = (
                f"{entry.title}: Smartbutton programmed with {len(requests)} request(s)."
                if pressed
                else f"{entry.title}: no Smartbutton was pressed within {wait} s; nothing changed."
            )
        _LOGGER.info(message)
        persistent_notification.async_create(
            hass, message, title="Zeptrion Air: program Smartbutton", notification_id=notification_id
        )

    entry.async_create_background_task(hass, _run(), f"{DOMAIN} program smartbutton")


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration-wide actions (once, from async_setup)."""
    if hass.services.has_service(DOMAIN, SERVICE_PROGRAM_SMARTBUTTON):
        return

    async def program(call: ServiceCall) -> None:
        await _async_program_smartbutton(hass, call)

    hass.services.async_register(DOMAIN, SERVICE_PROGRAM_SMARTBUTTON, program, schema=PROGRAM_SCHEMA)

    async def write_name(call: ServiceCall) -> None:
        # One integration-wide action for blinds and lights: an entity service
        # registered by both platforms would be replaced by the later one.
        entity_ids = await async_extract_entity_ids(call)
        components = hass.data.get("entity_components", {})
        found = 0
        for entity_id in sorted(entity_ids):
            component = components.get(entity_id.split(".", 1)[0])
            entity = component.get_entity(entity_id) if component else None
            if entity is None or getattr(entity, "platform", None) is None or entity.platform.platform_name != DOMAIN:
                continue
            await entity.async_write_name_to_zapp(call.data.get("name"), call.data.get("group"))
            found += 1
        if not found:
            raise ServiceValidationError("No Zeptrion Air blind or light selected")

    hass.services.async_register(
        DOMAIN, SERVICE_WRITE_NAME, write_name, schema=cv.make_entity_service_schema(WRITE_NAME_SCHEMA)
    )


WRITE_NAME_SCHEMA = {
    vol.Optional("name"): cv.string,
    vol.Optional("group"): cv.string,
}


async def async_write_name_to_zapp(entity: Any, channel: int, name: str | None, group: str | None) -> None:
    """Write the channel name (and group = room) shown in Home Assistant into the zApp.

    Defaults: the device name as shown in Home Assistant, and its area as group.
    Both are cut to 32 bytes (UTF-8), the limit of the zApp.
    """
    hass: HomeAssistant = entity.hass
    reg_entry = er.async_get(hass).async_get(entity.entity_id)
    device = (
        dr.async_get(hass).async_get(reg_entry.device_id)
        if reg_entry is not None and reg_entry.device_id
        else None
    )
    if name is None:
        name = (device.name_by_user or device.name) if device else entity.name
    if group is None and device is not None and device.area_id:
        area = ar.async_get(hass).async_get_area(device.area_id)
        group = area.name if area else None
    if not name:
        raise ServiceValidationError("No name to write")
    name = truncate_utf8(str(name), CHDES_TEXT_BYTES)
    group = truncate_utf8(str(group), CHDES_TEXT_BYTES) if group else None
    try:
        await entity.config_entry.runtime_data.client.async_set_channel_descriptor(
            channel, name=name, group=group
        )
    except ZeptrionAirApiClientError as err:
        raise HomeAssistantError(f"{entity.name}: writing the name failed: {err}") from err
    async_dispatcher_send(
        hass, SIGNAL_CHANNEL_DESCRIPTOR.format(entity.config_entry.entry_id), channel, name, group
    )
