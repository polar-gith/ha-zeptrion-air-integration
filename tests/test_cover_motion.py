"""Unit tests of the blind movement model."""

from __future__ import annotations

from custom_components.zeptrion_air.cover_motion import (
    CoverMotion,
    TravelTimes,
    TravelTimeSamples,
)

TRAVEL = TravelTimes(up=50.0, down=40.0)


def test_full_run_snaps_to_end_stop_without_travel_times() -> None:
    motion = CoverMotion()
    motion.hint("down", 0.0)
    motion.on_value(100, 0.5)
    assert motion.moving and motion.direction == "down"
    run = motion.on_value(0, 60.5)  # relay timer
    assert run.direction == "down" and not run.stopped_by_hand
    assert motion.end_stop == "bottom"
    assert motion.position == 0.0


def test_unknown_direction_makes_position_unknown() -> None:
    motion = CoverMotion(TRAVEL, position=100.0)
    motion.on_value(100, 0.0)  # scene / other device: no hint
    assert motion.direction is None
    motion.on_value(0, 60.0)
    assert motion.position is None and motion.end_stop is None


def test_stale_hint_is_ignored() -> None:
    motion = CoverMotion(TRAVEL, position=0.0)
    motion.hint("up", 0.0)
    motion.on_value(100, 10.0)
    assert motion.direction is None


def test_partial_run_estimates_position() -> None:
    motion = CoverMotion(TRAVEL, position=0.0)
    motion.hint("up", 0.0)
    motion.on_value(100, 0.1)
    assert motion.current_position(25.1) == 50.0
    motion.stop_requested(25.1)
    run = motion.on_value(0, 25.1)
    assert run.stopped_by_hand
    assert motion.position == 50.0
    assert motion.end_stop is None


def test_long_run_snaps_even_when_stopped() -> None:
    motion = CoverMotion(TRAVEL, position=50.0)
    motion.hint("down", 0.0)
    motion.on_value(100, 0.0)
    motion.stop_requested(39.0)  # 39 s >= 95 % of 40 s
    motion.on_value(0, 39.0)
    assert motion.position == 0.0 and motion.end_stop == "bottom"


def test_seconds_for_target() -> None:
    motion = CoverMotion(TRAVEL, position=0.0)
    assert motion.seconds_for(50.0) == ("up", 25.0)
    motion.position = 80.0
    assert motion.seconds_for(20.0) == ("down", 24.0)
    motion.position = None
    assert motion.seconds_for(50.0) is None


def test_learning_samples_need_known_start_and_agreement() -> None:
    samples = TravelTimeSamples()
    motion = CoverMotion()

    # full run down: now at the bottom
    motion.hint("down", 0.0)
    motion.on_value(100, 0.0)
    motion.on_value(0, 60.0)

    # up, stopped at the top after 48.2 s: a sample
    motion.hint("up", 100.0)
    motion.on_value(100, 100.0)
    motion.stop_requested(148.2)
    assert samples.add(motion.on_value(0, 148.2))
    assert motion.position is None  # no travel times: no estimate
    assert motion.end_stop is None

    # partial run not starting from an end stop: no sample
    motion.hint("down", 200.0)
    motion.on_value(100, 200.0)
    motion.stop_requested(210.0)
    assert not samples.add(motion.on_value(0, 210.0))

    assert samples.result("up") is None  # only one sample
    samples.up.append(48.9)
    assert samples.result("up") == 48.5
    samples.down.extend([40.0, 45.0])
    assert samples.result("down") is None  # not within 1 s


def test_timed_move_is_neither_full_run_nor_sample() -> None:
    samples = TravelTimeSamples()
    motion = CoverMotion(TRAVEL, position=0.0)
    motion.end_stop = "bottom"
    motion.hint("up", 0.0, target=50.0, timed=True)
    motion.on_value(100, 0.0)
    run = motion.on_value(0, 25.0)  # stops by itself
    assert motion.position == 50.0
    assert not samples.add(run)
