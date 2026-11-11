"""Movement tracking of one Zeptrion blind: direction, estimated position, travel time learning.

The zApp only reports "motor runs" (100) and "motor stands" (0) for a blind
channel; the relay stays on for its configured run time (typically about 60 s),
also after the blind has reached its end stop. Direction therefore has to come
from what started the movement (a command from Home Assistant or a wall key),
and a position can only be estimated from the time the motor ran.

Rules:
* A direction hint (command or wall key) is valid for HINT_VALID_S seconds; a
  movement that starts without one has an unknown direction (scene, Smartbutton
  of another device, ...) and makes the position unknown.
* A movement that ends without a stop request (HA stop, wall key) ran into the
  relay timer, so the blind is at its end stop: position 0 or 100.
* A movement that reaches FULL_RUN_FRACTION of the travel time also snaps to the end.
* Learning: a movement that started at a known end stop, went towards the other
  end and was stopped by hand is a measurement of the travel time in that direction.

The idea of direction from wall keys and position estimation with snapping
follows diaznet/ha-zeptrionair (MIT).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Literal

Direction = Literal["up", "down"]
EndStop = Literal["top", "bottom"]

HINT_VALID_S = 3.0
FULL_RUN_FRACTION = 0.95
MIN_SAMPLE_S = 5.0
SAMPLE_AGREEMENT_S = 1.0
MIN_SAMPLES = 2


@dataclass(frozen=True)
class TravelTimes:
    """Full travel time per direction in seconds (None = not known)."""

    up: float | None = None
    down: float | None = None

    def for_direction(self, direction: Direction) -> float | None:
        value = self.up if direction == "up" else self.down
        return value if value and value > 0 else None

    @property
    def complete(self) -> bool:
        return self.for_direction("up") is not None and self.for_direction("down") is not None


@dataclass(frozen=True)
class Run:
    """A finished movement."""

    direction: Direction | None
    duration: float
    stopped_by_hand: bool
    start_end: EndStop | None
    timed: bool = False

    @property
    def is_travel_sample(self) -> bool:
        """True if this run measured the full travel time of its direction."""
        if (
            self.direction is None
            or not self.stopped_by_hand
            or self.timed
            or self.duration < MIN_SAMPLE_S
        ):
            return False
        expected_start: EndStop = "bottom" if self.direction == "up" else "top"
        return self.start_end == expected_start


    @property
    def is_auto_sample(self) -> bool:
        """True if the zApp ended this run by itself after a full travel.

        The zApp switches a blind's relay off when the blind arrives at its end
        stop (its run time is learned by the device), so a run from one end stop
        to the other that nobody stopped lasted the full travel time.
        """
        if self.direction is None or self.stopped_by_hand or self.timed or self.duration < MIN_SAMPLE_S:
            return False
        expected_start: EndStop = "bottom" if self.direction == "up" else "top"
        return self.start_end == expected_start


@dataclass
class _Hint:
    direction: Direction | None
    at: float
    timed: bool = False


class CoverMotion:
    """State machine for one blind channel."""

    def __init__(
        self,
        travel: TravelTimes | None = None,
        position: float | None = None,
        tilt_time: float | None = None,
        tilt: float | None = None,
    ) -> None:
        self.travel = travel or TravelTimes()
        self.position: float | None = position
        # Slats (venetian blinds): on a change of direction the motor first turns
        # the slats for ``tilt_time`` seconds before the height changes.
        # Tilt position: 100 = slats open (turned by moving up), 0 = closed.
        self.tilt_time: float | None = tilt_time if tilt_time and tilt_time > 0 else None
        self.tilt: float | None = tilt
        self._start_tilt: float | None = None
        self.end_stop: EndStop | None = (
            "top" if position == 100 else "bottom" if position == 0 else None
        )
        self.moving = False
        self.direction: Direction | None = None
        self._started_at: float | None = None
        self._start_position: float | None = None
        self._start_end: EndStop | None = None
        self._stop_requested = False
        self._timed = False
        self._hint: _Hint | None = None
        self._target: float | None = None

    # ---------------------------------------------------------------- inputs

    def hint(
        self,
        direction: Direction | None,
        now: float,
        target: float | None = None,
        timed: bool = False,
    ) -> None:
        """A command or wall key that will start a movement (None = direction unknown).

        ``timed``: a timed command (move_open_<ms>, ...) that stops by itself
        before the end stop, so its end is not a full run.
        """
        self._hint = _Hint(direction, now, timed)
        self._target = target

    def stop_requested(self, now: float) -> None:
        """A stop command or a wall key press while moving."""
        if self.moving:
            self._stop_requested = True

    def on_value(self, value: int, now: float) -> Run | None:
        """Feed a channel value (100 = motor runs, 0 = stands). Returns the finished run."""
        if value == 100:
            self._start(now)
            return None
        if value == 0:
            return self._finish(now)
        return None

    def mark_end_stop(self, end_stop: EndStop) -> None:
        """The blind is known to be at this end stop (e.g. stopped there in learning mode)."""
        if self.moving:
            return
        self.end_stop = end_stop
        self.position = 100.0 if end_stop == "top" else 0.0
        if self.tilt_time:
            self.tilt = 100.0 if end_stop == "top" else 0.0

    def tilt_from_end_stop(self) -> None:
        """Derive an unknown slat position from a known end stop.

        Arriving at the top the slats are open, at the bottom closed. Used after
        a restart or when a tilt time is set while the blind stands at an end.
        """
        if self.moving or self.tilt_time is None or self.tilt is not None:
            return
        if self.end_stop is not None:
            self.tilt = 100.0 if self.end_stop == "top" else 0.0

    # ---------------------------------------------------------------- output

    def current_position(self, now: float) -> float | None:
        """Estimated position, also while moving."""
        if not self.moving:
            return self.position
        return self._estimate(self._elapsed(now))

    def reached_end(self, now: float) -> bool:
        """True while moving if the estimate says the end stop has been reached."""
        if not self.moving or self.direction is None:
            return False
        travel = self.travel.for_direction(self.direction)
        if travel is None or self._start_position is None:
            return False
        position = self._estimate(self._elapsed(now))
        target_end = 100.0 if self.direction == "up" else 0.0
        return position == target_end

    def current_tilt(self, now: float) -> float | None:
        """Estimated slat position, also while moving (None without a tilt time)."""
        if self.tilt_time is None:
            return None
        if not self.moving:
            return self.tilt
        return self._estimate_tilt(self._elapsed(now))

    def seconds_for(self, target: float) -> tuple[Direction, float] | None:
        """Direction and motor time to move from the current position to target."""
        if self.position is None or self.moving:
            return None
        delta = target - self.position
        if abs(delta) < 0.5:
            return None
        direction: Direction = "up" if delta > 0 else "down"
        travel = self._height_travel(direction)
        if travel is None:
            return None
        return direction, self._tilt_needed(direction, self.tilt) + abs(delta) / 100 * travel

    def seconds_for_tilt(self, target: float) -> tuple[Direction, float] | None:
        """Direction and motor time to turn the slats from their current tilt to target."""
        if self.tilt_time is None or self.moving:
            return None
        current = self.tilt
        if current is None:
            # unknown: turn fully towards the nearer end of the request
            direction: Direction = "up" if target >= 50 else "down"
            return direction, self.tilt_time
        delta = target - current
        if abs(delta) < 0.5:
            return None
        direction = "up" if delta > 0 else "down"
        return direction, abs(delta) / 100 * self.tilt_time

    # ------------------------------------------------------------- internals

    def _start(self, now: float) -> None:
        if self.moving:
            return
        hint = self._hint
        self._hint = None
        direction = hint.direction if hint and now - hint.at <= HINT_VALID_S else None
        self.moving = True
        self.direction = direction
        self._started_at = now
        self._start_end = self.end_stop if direction is not None else None
        self._start_position = self.position if direction is not None else None
        self._start_tilt = self.tilt if direction is not None else None
        self._timed = bool(hint and direction is not None and hint.timed)
        self._stop_requested = self._timed
        if direction is None:
            self.position = None
            self.end_stop = None
            self.tilt = None

    def _finish(self, now: float) -> Run | None:
        if not self.moving:
            return None
        duration = self._elapsed(now)
        run = Run(
            direction=self.direction,
            duration=duration,
            stopped_by_hand=self._stop_requested,
            start_end=self._start_end,
            timed=self._timed,
        )
        self.moving = False
        travel = self.travel.for_direction(self.direction) if self.direction else None

        if self.direction is None:
            self.position = None
            self.end_stop = None
            self.tilt = None
        elif not run.stopped_by_hand or (travel is not None and duration >= FULL_RUN_FRACTION * travel):
            # ran into the relay timer (or long enough): at the end stop
            self.position = 100.0 if self.direction == "up" else 0.0
            self.end_stop = "top" if self.direction == "up" else "bottom"
            if self.tilt_time:
                self.tilt = 100.0 if self.direction == "up" else 0.0
        else:
            self.tilt = self._estimate_tilt(duration)
            self.position = self._estimate(duration)
            self.end_stop = None
            if self.position == 100.0:
                self.end_stop = "top"
            elif self.position == 0.0:
                self.end_stop = "bottom"

        self.direction = None
        self._started_at = None
        self._start_position = None
        self._start_tilt = None
        self._target = None
        return run

    def _elapsed(self, now: float) -> float:
        return 0.0 if self._started_at is None else now - self._started_at

    def _height_travel(self, direction: Direction) -> float | None:
        """Seconds the height takes end to end (full travel minus the slat turn)."""
        travel = self.travel.for_direction(direction)
        if travel is None:
            return None
        return max(1.0, travel - (self.tilt_time or 0.0))

    def _tilt_needed(self, direction: Direction, start_tilt: float | None) -> float:
        """Seconds the slats turn before the height changes."""
        if self.tilt_time is None:
            return 0.0
        if start_tilt is None:
            return self.tilt_time
        target = 100.0 if direction == "up" else 0.0
        return abs(target - start_tilt) / 100 * self.tilt_time

    def _estimate_tilt(self, elapsed: float) -> float | None:
        if self.tilt_time is None or self.direction is None:
            return None
        target = 100.0 if self.direction == "up" else 0.0
        start = self._start_tilt
        if start is None:
            start = 0.0 if self.direction == "up" else 100.0
        change = elapsed / self.tilt_time * 100
        value = start + (change if self.direction == "up" else -change)
        value = min(value, target) if self.direction == "up" else max(value, target)
        return round(min(100.0, max(0.0, value)), 1)

    def _estimate(self, elapsed: float) -> float | None:
        if self.direction is None or self._start_position is None:
            return None
        travel = self.travel.for_direction(self.direction)
        height_travel = self._height_travel(self.direction)
        if travel is None or height_travel is None:
            return None
        height_elapsed = max(0.0, elapsed - self._tilt_needed(self.direction, self._start_tilt))
        change = height_elapsed / height_travel * 100
        if elapsed >= FULL_RUN_FRACTION * travel:
            change = 100.0
        value = self._start_position + (change if self.direction == "up" else -change)
        return round(min(100.0, max(0.0, value)), 1)


# ------------------------------------------------------------------ learning


@dataclass
class TravelTimeSamples:
    """Measurements of one blind collected in learning mode."""

    up: list[float] = field(default_factory=list)
    down: list[float] = field(default_factory=list)

    def add(self, run: Run) -> bool:
        if not run.is_travel_sample or run.direction is None:
            return False
        (self.up if run.direction == "up" else self.down).append(round(run.duration, 2))
        return True

    def result(self, direction: Direction) -> float | None:
        """Agreed travel time: the largest group of at least MIN_SAMPLES values that
        lie within SAMPLE_AGREEMENT_S of each other, averaged."""
        values = sorted(self.up if direction == "up" else self.down)
        best: list[float] = []
        start = 0
        for end in range(len(values)):
            while values[end] - values[start] > SAMPLE_AGREEMENT_S:
                start += 1
            if end - start + 1 > len(best):
                best = values[start : end + 1]
        if len(best) < MIN_SAMPLES:
            return None
        return round(statistics.mean(best), 1)
