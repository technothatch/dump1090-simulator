"""The simulation loop: determinism, emission coherence and stats windows."""

from __future__ import annotations

import json
import re

import pytest

from conftest import START, base_config
from dump1090_simulator.beast import FrameParser
from dump1090_simulator.physics import NM_TO_KM, great_circle_km
from dump1090_simulator.simulation import MAX_EVENT_WINDOW_S, Simulation
from dump1090_simulator.transponder import syndrome

_SQUAWK_RE = re.compile(r"[0-7]{4}")


def test_step_requires_a_positive_dt() -> None:
    sim = Simulation(base_config())
    with pytest.raises(ValueError, match="must be positive"):
        sim.step(0.0)
    with pytest.raises(ValueError, match="must be positive"):
        sim.step(-1.5)


def test_step_returns_escaped_beast_frames_with_valid_parity() -> None:
    sim = Simulation(base_config())
    parser = FrameParser()
    emitted = 0
    for _ in range(30):
        frames = sim.step(1.0)
        emitted += len(frames)
        assert len(frames) <= len(sim.fleet)
        for wire in frames:
            assert wire[0] == 0x1A
            assert chr(wire[1]) == "3"
        for frame in parser.feed(b"".join(frames)):
            assert len(frame.payload) == 14
            assert frame.payload[0] == 0x8D
            assert syndrome(frame.payload) == 0
    doc = sim.aircraft_doc()
    assert emitted == doc["messages"] == sum(entry["messages"] for entry in doc["aircraft"])
    assert emitted > 0


def test_clock_advances_exactly_by_the_step_size() -> None:
    sim = Simulation(base_config())
    assert sim.now == START
    assert sim.aircraft_doc()["now"] == START
    sim.step(1.0)
    assert sim.now == START + 1.0
    assert sim.aircraft_doc()["now"] == pytest.approx(START + 1.0)
    sim.step(2.5)
    assert sim.now == START + 3.5
    assert sim.aircraft_doc()["now"] == pytest.approx(START + 3.5)


def _run(seed: int, steps: int = 90) -> tuple[list[bytes], str]:
    sim = Simulation(base_config(seed=seed))
    frames: list[bytes] = []
    for _ in range(steps):
        frames.extend(sim.step(1.0))
    return frames, json.dumps(sim.aircraft_doc(), sort_keys=True)


def test_same_seed_reproduces_frames_and_documents() -> None:
    first_frames, first_doc = _run(42)
    second_frames, second_doc = _run(42)
    assert first_frames
    assert first_frames == second_frames
    assert first_doc == second_doc


def test_a_different_seed_diverges() -> None:
    assert _run(42)[0] != _run(43)[0]


def test_scheduled_squawk_change_fires_exactly_once() -> None:
    sim = Simulation(base_config())
    aircraft = sim.fleet[0]
    aircraft.squawk = "1200"
    aircraft.squawk_change_at = sim.now + 0.5
    sim.step(1.0)
    assert aircraft.squawk_change_at is None
    assert _SQUAWK_RE.fullmatch(aircraft.squawk)
    assert sim.aircraft_doc()["aircraft"][0]["squawk"] == aircraft.squawk
    unchanged = aircraft.squawk
    sim.step(1.0)
    assert aircraft.squawk == unchanged


@pytest.mark.parametrize(
    ("squawk", "emergency"),
    [("7500", "unlawful"), ("7600", "radio"), ("7700", "emergency"), ("1200", "none")],
)
def test_emergency_squawk_reaches_aircraft_json(squawk: str, emergency: str) -> None:
    sim = Simulation(base_config())
    aircraft = sim.fleet[0]
    aircraft.squawk = squawk
    aircraft.squawk_change_at = None
    sim.step(1.0)
    entry = sim.aircraft_doc()["aircraft"][0]
    assert entry["squawk"] == squawk
    assert entry["emergency"] == emergency


def test_reported_position_never_lags_beyond_the_message_interval() -> None:
    sim = Simulation(base_config())
    for _ in range(60):
        sim.step(1.0)
    entries = {entry["hex"]: entry for entry in sim.aircraft_doc()["aircraft"]}
    for aircraft in sim.fleet:
        assert sim.now - aircraft.last_pos_t < aircraft.pos_interval
        lag_km = great_circle_km(aircraft.lat, aircraft.lon, aircraft.r_lat, aircraft.r_lon)
        budget_km = 520.0 * aircraft.pos_interval * NM_TO_KM / 3600.0
        assert lag_km <= budget_km + 1e-6
        assert entries[aircraft.hex]["seen_pos"] <= aircraft.pos_interval + 0.1


def test_an_empty_fleet_emits_nothing_but_still_serves_documents() -> None:
    sim = Simulation(base_config(count=0))
    assert sim.step(1.0) == []
    doc = sim.aircraft_doc()
    assert doc["aircraft"] == []
    assert doc["messages"] == 0
    total = sim.stats_doc()["total"]["local"]
    assert total["blocks_processed"] == 0
    assert total["signal"] == 0.0
    assert total["peak_signal"] == 0.0
    assert total["strong_signals"] == 0


def test_stats_total_matches_the_aircraft_counter_after_traffic() -> None:
    sim = Simulation(base_config())
    for _ in range(120):
        sim.step(1.0)
    total = sim.stats_doc()["total"]["local"]
    doc = sim.aircraft_doc()
    assert total["blocks_processed"] == doc["messages"]
    assert total["modes"] == doc["messages"]
    assert total["accepted"][0] == doc["messages"]
    assert total["blocks_dropped"] == 0
    assert total["bad"] == 0
    assert total["unknown_icao"] == 0


def test_event_window_is_pruned_while_totals_grow_forever() -> None:
    count = 8
    sim = Simulation(base_config(count=count))
    for _ in range(2000):
        sim.step(1.0)
    retained = len(sim._events)
    assert retained <= count * (int(MAX_EVENT_WINDOW_S) + 2)
    assert sim.stats_doc()["total"]["local"]["blocks_processed"] > retained
