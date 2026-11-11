"""Cover platform for Zeptrion Air."""
from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_CURRENT_TILT_POSITION,
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, Event, callback
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_platform
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import ZeptrionAirApiClientCommunicationError, ZeptrionAirApiClientError
from .const import (
    DOMAIN,
    SERVICE_BLIND_RECALL_S1,
    SERVICE_BLIND_RECALL_S2,
    SERVICE_BLIND_RECALL_S3,
    SERVICE_BLIND_RECALL_S4,
    CONF_STEP_DURATION_MS,
    DEFAULT_STEP_DURATION_MS,
    SIGNAL_AVAILABILITY,
    SIGNAL_KEY_EVENT,
    SIGNAL_TRAVEL_TIMES,
    ZEPTRION_AIR_WEBSOCKET_MESSAGE,
    CONF_AUTO_TRAVEL_TIMES,
    DEFAULT_AUTO_TRAVEL_TIMES,
    tilt_option_key,
    travel_option_key,
)
from .cover_motion import CoverMotion, Run, TravelTimes, TravelTimeSamples
from .learning import async_get_learning_manager, async_store_travel_times

AUTO_SAMPLES_KEPT = 5
from .smartfront import KeyEvent

# Timed commands move_open_<ms> / move_close_<ms> accept 100-32000 ms.
MAX_TIMED_MOVE_MS = 32000
# Safety net for a missed "motor stopped": re-read the channel after the
# longest travel time (or this default) plus a margin.
MOVING_CHECK_DEFAULT_S = 120.0
MOVING_CHECK_MARGIN_S = 15.0

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> bool:
    """Set up Zeptrion Air cover entities from a config entry."""
    platform_data: dict[str, Any] | None = hass.data[DOMAIN].get(entry.entry_id)

    if not platform_data:
        _LOGGER.error("No platform_data found for entry ID %s", entry.entry_id)
        return False

    _LOGGER.debug("Received platform_data: %s", platform_data)

    main_hub_device_info: dict[str, Any] = platform_data.get("hub_device_info", {})
    identified_channels_list: list[dict[str, Any]] = platform_data.get("identified_channels", [])
    hub_entry_title: str = platform_data.get("entry_title", "Zeptrion Air Hub")

    hub_serial_for_blinds_maybe: str | None = platform_data.get("hub_serial")
    if not hub_serial_for_blinds_maybe:
        _LOGGER.error("Hub serial not found in platform_data.")
        return False
    hub_serial_for_blinds: str = hub_serial_for_blinds_maybe

    # The hub device itself is registered in __init__.py before platform setup
    # is forwarded, so we can resolve its registry device_id here and link
    # child entities via the modern `via_device_id` field instead of the
    # deprecated `via_device` identifiers-tuple field.
    dev_reg = dr.async_get(hass)
    hub_device_entry = dev_reg.async_get_device_by_identifier(
        (DOMAIN, hub_serial_for_blinds), entry.entry_id
    )
    hub_device_id: str | None = hub_device_entry.id if hub_device_entry else None
    if hub_device_id is None:
        _LOGGER.warning(
            "Could not resolve hub device registry entry for serial %s; "
            "cover entities will be created without a via_device_id link.",
            hub_serial_for_blinds,
        )

    new_entities: list[ZeptrionAirBlind] = []

    panel_type_mapping: dict[int, str] = {
        5: "Blinds",
        6: "Markise"
    }

    if identified_channels_list:
        for channel_info_dict in identified_channels_list:
            channel_id_maybe: int | None = channel_info_dict.get('id')
            channel_cat_maybe: int | None = channel_info_dict.get('cat')
            device_type: str | None = channel_info_dict.get('device_type')

            if channel_id_maybe is None or channel_cat_maybe is None or device_type != "cover":
                if device_type != "cover" and channel_id_maybe is not None:
                     _LOGGER.debug("Skipping channel %s (Cat: %s). Not a cover device_type ('%s').",
                                   channel_id_maybe, channel_cat_maybe, device_type)
                else:
                     _LOGGER.warning("Skipping channel due to missing id, cat or not being a cover: %s",
                                     channel_info_dict)
                continue

            channel_id: int = channel_id_maybe
            channel_cat: int = channel_cat_maybe

            entity_base_name: str | None = channel_info_dict.get("entity_base_name")
            desired_name: str = entity_base_name if entity_base_name is not None else f"Channel {channel_id}"

            entity_base_slug = desired_name.lower().replace(' ', '_').replace('-', '_').replace('.', '_').replace(':', '_')
            entity_base_slug = '_'.join(filter(None, entity_base_slug.split('_')))

            _LOGGER.debug("Channel %s (Cat: %s). Type is cover. Using Name: '%s'. Entity base slug: '%s'. Creating entity.",
                          channel_id, channel_cat, desired_name, entity_base_slug)

            hub_manufacturer: str = main_hub_device_info.get("manufacturer", "Feller AG")
            hub_sw_version: str | None = main_hub_device_info.get("sw_version")

            blind_device_info: dict[str, Any] = {
                "identifiers": {(DOMAIN, f"{hub_serial_for_blinds}_ch{channel_id}")},
                "name": desired_name,
                "via_device_id": hub_device_id,
                "manufacturer": hub_manufacturer,
                "sw_version": hub_sw_version,
            }
            panel_type_string: str = panel_type_mapping.get(channel_cat, "Unknown Panel")
            blind_device_info["model"] = f"Zeptrion Air Channel {channel_id} - {panel_type_string}"

            new_entities.append(
                ZeptrionAirBlind(
                    config_entry=entry,
                    device_info_for_blind_entity=blind_device_info,
                    channel_id=channel_id,
                    hub_serial=hub_serial_for_blinds,
                    entry_title=hub_entry_title,
                    entity_base_slug=entity_base_slug
                )
            )

    if new_entities:
        for entity in new_entities:
            _LOGGER.debug("Preparing to add cover entity: Name: %s, Unique ID: %s",
                          entity.name, entity.unique_id)
        _LOGGER.info("Adding %s ZeptrionAirBlind cover entities.", len(new_entities))
        async_add_entities(new_entities)
    else:
        _LOGGER.info("No Zeptrion Air cover entities to add.")

    return True


class ZeptrionAirBlind(CoverEntity, RestoreEntity):
    """Representation of a Zeptrion Air Blind.

    Direction comes from Home Assistant commands and Smartfront wall keys; with
    travel times configured (options, or learned in learning mode) the position
    is estimated and "set position" is supported. See cover_motion.py.
    """

    _attr_should_poll = False

    def __init__(
        self,
        config_entry: ConfigEntry,
        device_info_for_blind_entity: dict[str, Any],
        channel_id: int,
        hub_serial: str,
        entry_title: str,
        entity_base_slug: str,
    ) -> None:
        """Initialize the Zeptrion Air blind."""
        self.config_entry: ConfigEntry = config_entry
        self._channel_id: int = channel_id

        self._attr_device_info: dict[str, Any] = device_info_for_blind_entity

        name_val = device_info_for_blind_entity.get("name")
        self._attr_name: str = str(name_val) if name_val is not None else f"Channel {channel_id}"
        self._attr_unique_id = f"zapp_{hub_serial}_ch{self._channel_id}"
        self._attr_device_class = CoverDeviceClass.SHUTTER

        options = config_entry.options
        self._travel = TravelTimes(
            up=_positive(options.get(travel_option_key("up", channel_id))),
            down=_positive(options.get(travel_option_key("down", channel_id))),
        )
        self._motion = CoverMotion(self._travel, tilt_time=self._tilt_time_option())
        self._tick_cancel: CALLBACK_TYPE | None = None
        self._moving_check_cancel: CALLBACK_TYPE | None = None
        self._stop_timer_cancel: CALLBACK_TYPE | None = None
        self._last_run: dict[str, Any] | None = None

        self._auto_samples: dict[str, list[float]] = {}
        self._attr_supported_features = self._features()

    def _features(self) -> CoverEntityFeature:
        features = (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
            | CoverEntityFeature.OPEN_TILT
            | CoverEntityFeature.CLOSE_TILT
        )
        if self._travel.complete:
            features |= CoverEntityFeature.SET_POSITION
        if self._motion.tilt_time:
            features |= CoverEntityFeature.SET_TILT_POSITION
        return features

    def _tilt_time_option(self) -> float | None:
        return _positive(self.config_entry.options.get(tilt_option_key(self._channel_id)))

    # ------------------------------------------------------------- state

    @property
    def available(self) -> bool:
        """Unavailable only after the event stream has been down for the grace period."""
        runtime_data = self.config_entry.runtime_data
        return runtime_data is not None and getattr(runtime_data, "available", True)

    @property
    def is_opening(self) -> bool:
        return self._motion.moving and self._motion.direction == "up" and not self._at_end_now()

    @property
    def is_closing(self) -> bool:
        return self._motion.moving and self._motion.direction == "down" and not self._at_end_now()

    @property
    def current_cover_tilt_position(self) -> int | None:
        tilt = self._motion.current_tilt(time.monotonic())
        return None if tilt is None else int(round(tilt))

    @property
    def current_cover_position(self) -> int | None:
        if not self._travel.complete:
            return None
        position = self._motion.current_position(time.monotonic())
        return None if position is None else int(round(position))

    @property
    def is_closed(self) -> bool | None:
        """Closed only when known: after a full run down (or an estimate of 0 %)."""
        if self._motion.moving:
            return None
        if self._motion.end_stop == "bottom":
            return True
        if self._motion.end_stop == "top":
            return False
        if self._travel.complete and self._motion.position is not None:
            return self._motion.position <= 0
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attributes: dict[str, Any] = {
            "travel_time_up": self._travel.up,
            "travel_time_down": self._travel.down,
            "end_stop": self._motion.end_stop,
        }
        if self._motion.tilt_time:
            attributes["tilt_time"] = self._motion.tilt_time
        if self._last_run:
            attributes["last_run"] = self._last_run
        learning = async_get_learning_manager(self.hass).status(
            self.config_entry.entry_id, self._channel_id
        ) if self.hass else None
        if learning is not None:
            attributes["learning"] = learning
        return attributes

    def _at_end_now(self) -> bool:
        return self._motion.reached_end(time.monotonic())

    # ---------------------------------------------------------- lifecycle

    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state is not None:
            position = last_state.attributes.get(ATTR_CURRENT_POSITION)
            end_stop = last_state.attributes.get("end_stop")
            if self._travel.complete and isinstance(position, (int, float)):
                self._motion.position = float(position)
            if end_stop in ("top", "bottom"):
                self._motion.end_stop = end_stop
                self._motion.position = 100.0 if end_stop == "top" else 0.0
            tilt = last_state.attributes.get(ATTR_CURRENT_TILT_POSITION)
            if self._motion.tilt_time and isinstance(tilt, (int, float)):
                self._motion.tilt = float(tilt)
            self._motion.tilt_from_end_stop()

        self.async_on_remove(
            self.hass.bus.async_listen(
                ZEPTRION_AIR_WEBSOCKET_MESSAGE, self.async_handle_websocket_message
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_AVAILABILITY.format(self.config_entry.entry_id),
                self.async_write_ha_state,
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_KEY_EVENT.format(self.config_entry.entry_id),
                self._handle_key_event,
            )
        )
        self.async_on_remove(
            async_get_learning_manager(self.hass).async_add_listener(self.async_write_ha_state)
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_TRAVEL_TIMES.format(self.config_entry.entry_id),
                self._reload_travel_times,
            )
        )
        self.async_on_remove(self._cancel_timers)

        platform: entity_platform.EntityPlatform | None = entity_platform.async_get_current_platform()
        if platform:
            platform.async_register_entity_service(SERVICE_BLIND_RECALL_S1, {}, self.async_blind_recall_s1.__name__)
            platform.async_register_entity_service(SERVICE_BLIND_RECALL_S2, {}, self.async_blind_recall_s2.__name__)
            platform.async_register_entity_service(SERVICE_BLIND_RECALL_S3, {}, self.async_blind_recall_s3.__name__)
            platform.async_register_entity_service(SERVICE_BLIND_RECALL_S4, {}, self.async_blind_recall_s4.__name__)

    @callback
    def _cancel_timers(self) -> None:
        if self._moving_check_cancel:
            self._moving_check_cancel()
            self._moving_check_cancel = None
        if self._tick_cancel:
            self._tick_cancel()
            self._tick_cancel = None
        if self._stop_timer_cancel:
            self._stop_timer_cancel()
            self._stop_timer_cancel = None

    # ------------------------------------------------------------ commands

    async def _send(self, label: str, coro_factory) -> None:
        try:
            await coro_factory()
        except (ZeptrionAirApiClientCommunicationError, ZeptrionAirApiClientError) as e:
            _LOGGER.error("API error while %s blind %s (Channel %s): %s", label, self._attr_name, self._channel_id, e)
            raise HomeAssistantError(f"Failed {label} blind {self.name} (Channel {self._channel_id}): {e}") from e

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        now = time.monotonic()
        self._motion.stop_requested(now)  # reversing: the current run does not reach the end
        self._motion.hint("up", now)
        client = self.config_entry.runtime_data.client
        await self._send("opening", lambda: client.async_channel_open(self._channel_id))

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""
        now = time.monotonic()
        self._motion.stop_requested(now)  # reversing: the current run does not reach the end
        self._motion.hint("down", now)
        client = self.config_entry.runtime_data.client
        await self._send("closing", lambda: client.async_channel_close(self._channel_id))

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover movement."""
        self._motion.stop_requested(time.monotonic())
        client = self.config_entry.runtime_data.client
        await self._send("stopping", lambda: client.async_channel_stop(self._channel_id))

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move to a position (needs travel times and a known position)."""
        target = float(kwargs[ATTR_POSITION])
        if target >= 100:
            await self.async_open_cover()
            return
        if target <= 0:
            await self.async_close_cover()
            return
        plan = self._motion.seconds_for(target)
        if plan is None:
            if self._motion.position is None:
                raise HomeAssistantError(
                    f"Position of {self.name} is unknown; open or close it fully once first."
                )
            return
        direction, seconds = plan
        milliseconds = int(round(seconds * 1000))
        client = self.config_entry.runtime_data.client
        timed = milliseconds <= MAX_TIMED_MOVE_MS
        self._motion.hint(direction, time.monotonic(), target=target, timed=timed)
        if timed:
            command = client.async_channel_move_open if direction == "up" else client.async_channel_move_close
            await self._send("positioning", lambda: command(self._channel_id, max(100, milliseconds)))
            return
        # Longer than a timed command allows: start, then stop from here.
        start = client.async_channel_open if direction == "up" else client.async_channel_close
        await self._send("positioning", lambda: start(self._channel_id))

        @callback
        def _stop(_now: Any) -> None:
            self._stop_timer_cancel = None
            self.hass.async_create_task(self.async_stop_cover())

        if self._stop_timer_cancel:
            self._stop_timer_cancel()
        self._stop_timer_cancel = async_call_later(self.hass, seconds, _stop)

    async def async_open_cover_tilt(self, **kwargs: Any) -> None:
        """Turn the slats one step up (option "step duration").

        Full open/close of the slats: set_cover_tilt_position.
        """
        self._motion.hint("up", time.monotonic(), timed=True)
        step_duration_ms = self._step_duration_ms()
        client = self.config_entry.runtime_data.client
        await self._send("tilting open", lambda: client.async_channel_move_open(self._channel_id, time_ms=step_duration_ms))

    async def async_close_cover_tilt(self, **kwargs: Any) -> None:
        """Turn the slats one step down (option "step duration").

        Full open/close of the slats: set_cover_tilt_position.
        """
        self._motion.hint("down", time.monotonic(), timed=True)
        step_duration_ms = self._step_duration_ms()
        client = self.config_entry.runtime_data.client
        await self._send("tilting close", lambda: client.async_channel_move_close(self._channel_id, time_ms=step_duration_ms))

    async def async_set_cover_tilt_position(self, **kwargs: Any) -> None:
        """Turn the slats to a tilt position (needs a tilt time)."""
        plan = self._motion.seconds_for_tilt(float(kwargs[ATTR_TILT_POSITION]))
        if plan is None:
            return
        direction, seconds = plan
        milliseconds = max(100, min(MAX_TIMED_MOVE_MS, int(round(seconds * 1000))))
        self._motion.hint(direction, time.monotonic(), timed=True)
        client = self.config_entry.runtime_data.client
        command = client.async_channel_move_open if direction == "up" else client.async_channel_move_close
        await self._send("tilting", lambda: command(self._channel_id, milliseconds))

    def _step_duration_ms(self) -> int:
        return int(
            self.config_entry.options.get(
                CONF_STEP_DURATION_MS,
                self.config_entry.data.get(CONF_STEP_DURATION_MS, DEFAULT_STEP_DURATION_MS),
            )
        )

    # ------------------------------------------------------------- events

    @callback
    def _handle_key_event(self, event: KeyEvent) -> None:
        """A Smartfront key of this zApp was pressed."""
        function = event.function
        if not event.pressed or function is None or function.kind != "channel":
            return
        if self._channel_id not in function.channels:
            return
        now = time.monotonic()
        if self._motion.moving:
            # A key press while the motor runs ends the current run. With the
            # opposite key the zApp may start the other direction right away
            # (seen on 01.08.28): keep the key's direction as hint for that start.
            self._motion.stop_requested(now)
        self._motion.hint(function.direction, now)

    async def async_handle_websocket_message(self, event: Event) -> None:
        """Handle channel values of this blind (100 = motor runs, 0 = stands)."""
        message_data = event.data
        if message_data.get("channel") != self._channel_id or message_data.get("source") != "eid1":
            return
        if message_data.get("hub_unique_id") != self.config_entry.unique_id:
            return

        value = message_data.get("value")
        now = time.monotonic()

        if message_data.get("snapshot"):
            # Read after a (re)connect: only use it to end a movement whose stop was missed.
            if value == 0 and self._motion.moving:
                self._motion.stop_requested(now)  # duration unknown: do not snap to an end
                self._finish_run(self._motion.on_value(0, now))
            return

        if value == 100:
            self._motion.on_value(100, now)
            if self._motion.direction is None:
                _LOGGER.debug("Blind %s moves, direction unknown (scene or other device)", self._attr_name)
            self._start_tick()
            self._arm_moving_check()
        elif value == 0:
            self._finish_run(self._motion.on_value(0, now))
        else:
            _LOGGER.debug("Blind %s: ignoring value %s", self._attr_name, value)
            return
        self.async_write_ha_state()

    @callback
    def _finish_run(self, run: Run | None) -> None:
        self._stop_tick()
        if run is None:
            return
        self._last_run = {
            "direction": run.direction,
            "duration": round(run.duration, 1),
            "stopped_by_hand": run.stopped_by_hand,
        }
        if async_get_learning_manager(self.hass).async_record(
            self.config_entry.entry_id, self._channel_id, self._attr_name, run
        ):
            # In learning mode a measurement is stopped exactly at the other end
            # stop, so the next run starts from a known end.
            self._motion.mark_end_stop("top" if run.direction == "up" else "bottom")
        elif run.is_auto_sample and run.direction is not None and self._auto_travel_times():
            self._auto_learn(run.direction, run.duration)
        self.async_write_ha_state()

    # ------------------------------------------------- automatic travel times

    def _auto_travel_times(self) -> bool:
        return bool(
            self.config_entry.options.get(CONF_AUTO_TRAVEL_TIMES, DEFAULT_AUTO_TRAVEL_TIMES)
        )

    @callback
    def _auto_learn(self, direction: str, duration: float) -> None:
        """Take over the travel time the zApp ran a full run with.

        The first full run sets an unknown travel time; a known one is only
        changed when two of the last runs agree within 1 s and differ from it
        by more than 0.5 s.
        """
        samples = self._auto_samples.setdefault(direction, [])
        samples.append(round(duration, 2))
        del samples[:-AUTO_SAMPLES_KEPT]
        current = self._travel.for_direction(direction)  # type: ignore[arg-type]
        if current is None:
            value = round(duration, 1)
        else:
            pool = TravelTimeSamples()
            (pool.up if direction == "up" else pool.down).extend(samples)
            value = pool.result(direction)  # type: ignore[arg-type]
            if value is None or abs(value - current) <= 0.5:
                return
        _LOGGER.info(
            "Blind %s: travel time %s learned from the zApp: %.1f s (was %s)",
            self._attr_name, direction, value, current,
        )
        async_store_travel_times(
            self.hass,
            self.config_entry,
            {travel_option_key(direction, self._channel_id): value},
        )

    @callback
    def _reload_travel_times(self) -> None:
        options = self.config_entry.options
        self._travel = TravelTimes(
            up=_positive(options.get(travel_option_key("up", self._channel_id))),
            down=_positive(options.get(travel_option_key("down", self._channel_id))),
        )
        self._motion.travel = self._travel
        tilt_time = self._tilt_time_option()
        if tilt_time != self._motion.tilt_time:
            self._motion.tilt_time = tilt_time
            self._motion.tilt = None
            self._motion.tilt_from_end_stop()
        self._attr_supported_features = self._features()
        self.async_write_ha_state()

    # ------------------------------------------------- live position updates

    @callback
    def _start_tick(self) -> None:
        if not self._travel.complete or self._tick_cancel is not None:
            return
        self._tick_cancel = async_track_time_interval(
            self.hass, self._tick, timedelta(seconds=1)
        )

    @callback
    def _stop_tick(self) -> None:
        if self._tick_cancel:
            self._tick_cancel()
            self._tick_cancel = None
        if self._moving_check_cancel:
            self._moving_check_cancel()
            self._moving_check_cancel = None

    # ------------------------------------- safety net for a missed "stopped"

    @callback
    def _arm_moving_check(self, delay: float | None = None) -> None:
        """Re-read the channel if no "motor stopped" arrives in the expected time.

        A run never lasts longer than the relay timer (about the travel time);
        if the 0 was missed (lost frame, two values in one burst), the blind
        would otherwise show "opening"/"closing" until its next movement.
        """
        if self._moving_check_cancel:
            self._moving_check_cancel()
        if delay is None:
            longest = max(
                [t for t in (self._travel.up, self._travel.down) if t] or [MOVING_CHECK_DEFAULT_S]
            )
            delay = longest + MOVING_CHECK_MARGIN_S
        self._moving_check_cancel = async_call_later(self.hass, delay, self._moving_check)

    @callback
    def _moving_check(self, _now: Any) -> None:
        self._moving_check_cancel = None
        if self._motion.moving:
            self.hass.async_create_task(self._async_moving_check())

    async def _async_moving_check(self) -> None:
        client = self.config_entry.runtime_data.client if self.config_entry.runtime_data else None
        value = None
        if client is not None:
            try:
                data = await client.async_get_channel_scan_info(self._channel_id)
                value = int(data["chscan"][f"ch{self._channel_id}"]["val"])
            except (ZeptrionAirApiClientError, KeyError, TypeError, ValueError) as err:
                _LOGGER.debug("Blind %s: channel check failed: %s", self._attr_name, err)
        if not self._motion.moving:
            return
        if value == 0:
            _LOGGER.debug("Blind %s: missed the stop, the channel stands; ending the run", self._attr_name)
            self._finish_run(self._motion.on_value(0, time.monotonic()))
            return
        self._arm_moving_check(MOVING_CHECK_MARGIN_S)

    @callback
    def _tick(self, _now: Any) -> None:
        if not self._motion.moving:
            self._stop_tick()
        self.async_write_ha_state()

    async def async_blind_recall_s1(self) -> None:
        """Recall scene S1 for the blind."""
        _LOGGER.debug("Recalling S1 for blind %s (Channel %s)", self.name, self._channel_id)
        self._motion.hint(None, time.monotonic())  # scene: direction unknown
        try:
            await self.config_entry.runtime_data.client.async_channel_recall_s1(self._channel_id)
        except (ZeptrionAirApiClientCommunicationError, ZeptrionAirApiClientError) as e:
            _LOGGER.error("API error while recalling S1 for blind %s (Channel %s): %s", self.name, self._channel_id, e)
            raise HomeAssistantError(f"Failed to recall S1 for blind {self.name} (Channel {self._channel_id}): An API error occurred. {e}") from e
        except Exception as e:
            _LOGGER.error("Unexpected error while recalling S1 for blind %s (Channel %s): %s", self.name, self._channel_id, e)
            raise HomeAssistantError(f"Failed to recall S1 for blind {self.name} (Channel {self._channel_id}): An unexpected error occurred. {e}") from e

    async def async_blind_recall_s2(self) -> None:
        """Recall scene S2 for the blind."""
        _LOGGER.debug("Recalling S2 for blind %s (Channel %s)", self.name, self._channel_id)
        self._motion.hint(None, time.monotonic())  # scene: direction unknown
        try:
            await self.config_entry.runtime_data.client.async_channel_recall_s2(self._channel_id)
        except (ZeptrionAirApiClientCommunicationError, ZeptrionAirApiClientError) as e:
            _LOGGER.error("API error while recalling S2 for blind %s (Channel %s): %s", self.name, self._channel_id, e)
            raise HomeAssistantError(f"Failed to recall S2 for blind {self.name} (Channel {self._channel_id}): An API error occurred. {e}") from e
        except Exception as e:
            _LOGGER.error("Unexpected error while recalling S2 for blind %s (Channel %s): %s", self.name, self._channel_id, e)
            raise HomeAssistantError(f"Failed to recall S2 for blind {self.name} (Channel {self._channel_id}): An unexpected error occurred. {e}") from e

    async def async_blind_recall_s3(self) -> None:
        """Recall scene S3 for the blind."""
        _LOGGER.debug("Recalling S3 for blind %s (Channel %s)", self.name, self._channel_id)
        self._motion.hint(None, time.monotonic())  # scene: direction unknown
        try:
            await self.config_entry.runtime_data.client.async_channel_recall_s3(self._channel_id)
        except (ZeptrionAirApiClientCommunicationError, ZeptrionAirApiClientError) as e:
            _LOGGER.error("API error while recalling S3 for blind %s (Channel %s): %s", self.name, self._channel_id, e)
            raise HomeAssistantError(f"Failed to recall S3 for blind {self.name} (Channel {self._channel_id}): An API error occurred. {e}") from e
        except Exception as e:
            _LOGGER.error("Unexpected error while recalling S3 for blind %s (Channel %s): %s", self.name, self._channel_id, e)
            raise HomeAssistantError(f"Failed to recall S3 for blind {self.name} (Channel {self._channel_id}): An unexpected error occurred. {e}") from e

    async def async_blind_recall_s4(self) -> None:
        """Recall scene S4 for the blind."""
        _LOGGER.debug("Recalling S4 for blind %s (Channel %s)", self.name, self._channel_id)
        self._motion.hint(None, time.monotonic())  # scene: direction unknown
        try:
            await self.config_entry.runtime_data.client.async_channel_recall_s4(self._channel_id)
        except (ZeptrionAirApiClientCommunicationError, ZeptrionAirApiClientError) as e:
            _LOGGER.error("API error while recalling S4 for blind %s (Channel %s): %s", self.name, self._channel_id, e)
            raise HomeAssistantError(f"Failed to recall S4 for blind {self.name} (Channel {self._channel_id}): An API error occurred. {e}") from e
        except Exception as e:
            _LOGGER.error("Unexpected error while recalling S4 for blind %s (Channel %s): %s", self.name, self._channel_id, e)
            raise HomeAssistantError(f"Failed to recall S4 for blind {self.name} (Channel {self._channel_id}): An unexpected error occurred. {e}") from e


def _positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None

