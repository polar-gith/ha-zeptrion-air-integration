"""Smartfront helpers: key functions (btfu), key press tracking, sensor values.

The decoding follows the observations documented by Jeremy Diaz (diaznet) in
zeptrionair-api, docs/protocol.md sections 4.8, 5.2 and 6 (MIT licence):
https://github.com/diaznet/zeptrionair-api
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

KeyKind = Literal["unused", "smartbutton", "channel", "unknown"]
Direction = Literal["up", "down"]

# btfu channel bitmask: 8 = ch1, 4 = ch2, 2 = ch3, 1 = ch4 (ch3/ch4 presumed).
_BITMASK_CHANNELS: dict[int, int] = {8: 1, 4: 2, 2: 3, 1: 4}
_DIRECTIONS: dict[str, Direction] = {"5": "up", "6": "down"}


@dataclass(frozen=True)
class KeyFunction:
    """What one Smartfront key does, decoded from one ``btfu`` entry."""

    key: int
    raw: str
    kind: KeyKind
    channels: tuple[int, ...] = ()
    direction: Direction | None = None

    def as_attributes(self) -> dict[str, Any]:
        """Attributes passed along with a key event."""
        attributes: dict[str, Any] = {"key": self.key, "function": self.kind, "code": self.raw}
        if self.kind == "channel":
            attributes["channels"] = list(self.channels)
            attributes["direction"] = self.direction
        return attributes


@dataclass(frozen=True)
class KeyEvent:
    """A key was pressed or released."""

    key: int
    pressed: bool
    function: KeyFunction | None
    raw: str = ""


def parse_key_function(key: int, code: str) -> KeyFunction:
    """Decode one ``btfu`` entry (heuristic)."""
    code = code.strip()
    if code in ("", "0"):
        return KeyFunction(key=key, raw=code, kind="unused")
    if code == "1000":
        return KeyFunction(key=key, raw=code, kind="smartbutton")
    if re.fullmatch(r"\d{4}", code):
        mask = int(code[0])
        channels = tuple(sorted(ch for bit, ch in _BITMASK_CHANNELS.items() if mask & bit))
        direction = _DIRECTIONS.get(code[1])
        if channels and direction is not None:
            return KeyFunction(
                key=key, raw=code, kind="channel", channels=channels, direction=direction
            )
    return KeyFunction(key=key, raw=code, kind="unknown")


def parse_btfu(btfu: str | None) -> tuple[KeyFunction, ...]:
    """Decode the comma-separated ``btfu`` list (key 1 first)."""
    if not btfu or not str(btfu).strip():
        return ()
    return tuple(
        parse_key_function(index, code)
        for index, code in enumerate(str(btfu).split(","), start=1)
    )


@dataclass
class KeyStateTracker:
    """Turns successive ``bta`` strings ("......P..") into press/release events."""

    functions: dict[int, KeyFunction] = field(default_factory=dict)
    _pressed: set[int] = field(default_factory=set)

    def set_keys(self, keys: Iterable[KeyFunction]) -> None:
        self.functions = {function.key: function for function in keys}

    def reset(self) -> None:
        self._pressed.clear()

    def update(self, bta: str) -> list[KeyEvent]:
        pressed = {index + 1 for index, char in enumerate(bta) if char.upper() == "P"}
        events = [
            KeyEvent(key=key, pressed=False, function=self.functions.get(key), raw=bta)
            for key in sorted(self._pressed - pressed)
        ]
        events += [
            KeyEvent(key=key, pressed=True, function=self.functions.get(key), raw=bta)
            for key in sorted(pressed - self._pressed)
        ]
        self._pressed = pressed
        return events


def parse_sensor_value(value: Any) -> float | None:
    """'35.44C' -> 35.44, 'none' -> None."""
    if value is None:
        return None
    match = re.match(r"^\s*(-?\d+(?:\.\d+)?)", str(value))
    return float(match[1]) if match else None
