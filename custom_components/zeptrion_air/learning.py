"""Learning mode for blind travel times, shared by all Zeptrion Air hubs.

One switch per hub with blinds; the switches are coupled: switching one on or
off switches the learning mode for all hubs. While it is on, every blind
collects travel time measurements (see cover_motion.Run.is_travel_sample).
Switching it off stores the agreed times in the options of each hub and shows
a summary as a persistent notification. It switches itself off after
LEARNING_TIMEOUT.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .const import DOMAIN, travel_option_key
from .cover_motion import Run, TravelTimeSamples

_LOGGER = logging.getLogger(__name__)

LEARNING_TIMEOUT = timedelta(minutes=60)
NOTIFICATION_ID = "zeptrion_air_learning"
_DATA_KEY = "learning_manager"


@dataclass
class _Blind:
    entry_id: str
    channel: int
    name: str
    samples: TravelTimeSamples = field(default_factory=TravelTimeSamples)


class LearningManager:
    """Integration-wide learning mode."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.active = False
        # Transfer the learned times to all blinds without an own result
        # (for installations where all blinds are the same). Set by a coupled switch.
        self.apply_to_all = False
        self.started_at = None
        self._blinds: dict[tuple[str, int], _Blind] = {}
        self._listeners: list[Callable[[], None]] = []
        self._timeout_cancel: CALLBACK_TYPE | None = None

    # ------------------------------------------------------------ listeners

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        self._listeners.append(listener)

        @callback
        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    @callback
    def notify(self) -> None:
        """Tell all switches and blinds that the learning state changed."""
        self._notify()

    @callback
    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    # ------------------------------------------------------------ lifecycle

    @callback
    def async_start(self) -> None:
        if self.active:
            return
        self.active = True
        self.started_at = dt_util.utcnow()
        self._blinds.clear()
        self._timeout_cancel = async_call_later(
            self.hass, LEARNING_TIMEOUT.total_seconds(), self._timeout
        )
        _LOGGER.info("Blind learning mode started")
        persistent_notification.async_create(
            self.hass,
            _text(self.hass, "start"),
            title=_text(self.hass, "start_title"),
            notification_id=NOTIFICATION_ID,
        )
        self._notify()

    @callback
    def _timeout(self, _now: Any) -> None:
        self._timeout_cancel = None
        self.hass.async_create_task(self.async_stop())

    async def async_stop(self) -> None:
        if not self.active:
            return
        self.active = False
        if self._timeout_cancel:
            self._timeout_cancel()
            self._timeout_cancel = None
        self._notify()
        await self._async_apply_results()

    # -------------------------------------------------------------- samples

    @callback
    def async_record(self, entry_id: str, channel: int, name: str, run: Run) -> bool:
        """Record a finished run of a blind; returns True if it was a measurement."""
        if not self.active:
            return False
        blind = self._blinds.setdefault((entry_id, channel), _Blind(entry_id, channel, name))
        added = blind.samples.add(run)
        if added:
            _LOGGER.info(
                "Learning: %s %s %.1f s (%s samples)",
                name,
                run.direction,
                run.duration,
                len(blind.samples.up if run.direction == "up" else blind.samples.down),
            )
        return added

    def status(self, entry_id: str, channel: int) -> dict[str, Any] | None:
        """Learning progress of one blind (for its state attributes)."""
        if not self.active:
            return None
        blind = self._blinds.get((entry_id, channel))
        samples = blind.samples if blind else TravelTimeSamples()
        return {
            "up_samples": list(samples.up),
            "down_samples": list(samples.down),
            "up_result": samples.result("up"),
            "down_result": samples.result("down"),
        }

    # -------------------------------------------------------------- results

    async def _async_apply_results(self) -> None:
        lines: list[str] = []
        by_entry: dict[str, dict[str, float]] = {}
        for blind in sorted(self._blinds.values(), key=lambda b: b.name):
            up = blind.samples.result("up")
            down = blind.samples.result("down")
            for direction, value in (("up", up), ("down", down)):
                if value is not None:
                    by_entry.setdefault(blind.entry_id, {})[
                        travel_option_key(direction, blind.channel)
                    ] = value
            lines.append(
                _text(self.hass, "line").format(
                    name=blind.name,
                    up=_fmt(self.hass, up, blind.samples.up),
                    down=_fmt(self.hass, down, blind.samples.down),
                )
            )

        if self.apply_to_all:
            transferred = self._transfer_to_all(by_entry)
            if transferred:
                lines.append("")
                lines.append(_text(self.hass, "transferred") + transferred)

        for entry_id, values in by_entry.items():
            entry = self.hass.config_entries.async_get_entry(entry_id)
            if entry is None:
                continue
            options = {**entry.options, **values}
            self.hass.config_entries.async_update_entry(entry, options=options)

        missing = [
            name
            for (entry_id, channel), name in _all_blinds(self.hass).items()
            if not any(
                travel_option_key(direction, channel) in by_entry.get(entry_id, {})
                for direction in ("up", "down")
            )
        ]
        if missing:
            lines.append("")
            lines.append(_text(self.hass, "missing") + ", ".join(sorted(missing)))
        message = "\n".join(lines) if lines else _text(self.hass, "none")
        persistent_notification.async_create(
            self.hass,
            _text(self.hass, "done") + message,
            title=_text(self.hass, "done_title"),
            notification_id=NOTIFICATION_ID,
        )
        _LOGGER.info("Blind learning mode finished: %s", message.replace("\n", "; "))

    def _transfer_to_all(self, by_entry: dict[str, dict[str, float]]) -> str:
        """Pool all measurements and give the agreed time to every blind without its own."""
        pooled = TravelTimeSamples()
        for blind in self._blinds.values():
            pooled.up.extend(blind.samples.up)
            pooled.down.extend(blind.samples.down)
        results = {direction: pooled.result(direction) for direction in ("up", "down")}
        count = 0
        for (entry_id, channel), _name in _all_blinds(self.hass).items():
            own = by_entry.setdefault(entry_id, {})
            for direction, value in results.items():
                key = travel_option_key(direction, channel)
                if value is not None and key not in own:
                    own[key] = value
                    count += 1
        if not count:
            return ""
        return _text(self.hass, "transferred_values").format(
            up=_fmt(self.hass, results["up"], pooled.up),
            down=_fmt(self.hass, results["down"], pooled.down),
            count=count,
        )


_TEXTS: dict[str, dict[str, str]] = {
    "en": {
        "start_title": "Zeptrion Air: learning travel times",
        "start": (
            "Learning mode is on (switches itself off after 60 min).\n\n"
            "For every blind: close it fully and let it stop by itself. Then move it up and "
            "press stop the moment it arrives at the top; then move it down and press stop "
            "when it arrives at the bottom. Do this twice. Wall keys and Home Assistant both "
            "work. Switch learning mode off when done."
        ),
        "done_title": "Zeptrion Air: travel times",
        "done": (
            "Learning mode finished. Agreed travel times were saved; times without two "
            "measurements within 1 s were left unchanged.\n\n"
        ),
        "line": "- {name}: up {up}, down {down}",
        "missing": "No usable measurement: ",
        "none": "No measurements were recorded.",
        "not_agreed": "not agreed",
        "transferred": "Transferred to all blinds without their own result: ",
        "transferred_values": "up {up}, down {down} ({count} values)",
    },
    "de": {
        "start_title": "Zeptrion Air: Fahrzeiten einmessen",
        "start": (
            "Der Lernmodus ist eingeschaltet (schaltet sich nach 60 min selbst aus).\n\n"
            "Für jede Store: ganz schliessen und von selbst anhalten lassen. Dann hochfahren "
            "und Stopp drücken, sobald sie oben ankommt; dann runterfahren und Stopp drücken, "
            "sobald sie unten ankommt. Das Ganze zweimal. Wandtaste und Home Assistant gehen "
            "beide. Danach den Lernmodus ausschalten."
        ),
        "done_title": "Zeptrion Air: Fahrzeiten",
        "done": (
            "Lernmodus beendet. Übereinstimmende Fahrzeiten wurden gespeichert; Zeiten ohne "
            "zwei Messungen innerhalb 1 s blieben unverändert.\n\n"
        ),
        "line": "- {name}: auf {up}, ab {down}",
        "missing": "Keine brauchbare Messung: ",
        "none": "Es wurden keine Messungen aufgezeichnet.",
        "not_agreed": "nicht übereinstimmend",
        "transferred": "Auf alle Storen ohne eigenes Ergebnis übertragen: ",
        "transferred_values": "auf {up}, ab {down} ({count} Werte)",
    },
}


def _text(hass: HomeAssistant, key: str) -> str:
    language = "de" if str(hass.config.language or "").startswith("de") else "en"
    return _TEXTS[language][key]


def _fmt(hass: HomeAssistant, value: float | None, samples: list[float]) -> str:
    if value is not None:
        return f"{value:.1f} s ✓ ({len(samples)})"
    if samples:
        return _text(hass, "not_agreed") + " (" + ", ".join(f"{s:.1f}" for s in samples) + ")"
    return "–"


def _all_blinds(hass: HomeAssistant) -> dict[tuple[str, int], str]:
    blinds: dict[tuple[str, int], str] = {}
    for entry_id, data in hass.data.get(DOMAIN, {}).items():
        if not isinstance(data, dict):
            continue
        for channel in data.get("identified_channels", []):
            if channel.get("device_type") == "cover":
                blinds[(entry_id, channel["id"])] = channel.get("entity_base_name") or f"Channel {channel['id']}"
    return blinds


@callback
def async_get_learning_manager(hass: HomeAssistant) -> LearningManager:
    """Return the integration-wide learning manager."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    manager = domain_data.get(_DATA_KEY)
    if manager is None:
        manager = LearningManager(hass)
        domain_data[_DATA_KEY] = manager
    return manager
