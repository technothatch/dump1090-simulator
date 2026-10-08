"""Model contract: phases, emergency mapping and the per-type table."""

from __future__ import annotations

import dataclasses
import re
import time

import pytest

from conftest import START, base_config, make_state
from dump1090_simulator.models import (
    AIRCRAFT_TYPES,
    EMERGENCY_BY_SQUAWK,
    MAX_ALT_FT,
    MAX_TURN_RATE_DEG_S,
    AircraftType,
    Phase,
    SimConfig,
    emergency_for_squawk,
)


def test_phase_values_are_dump1090_vocabulary() -> None:
    assert [phase.value for phase in Phase] == [
        "ground",
        "takeoff",
        "climb",
        "cruise",
        "descent",
        "landing",
    ]


def test_emergency_squawk_mapping() -> None:
    assert emergency_for_squawk("7500") == "unlawful"
    assert emergency_for_squawk("7600") == "radio"
    assert emergency_for_squawk("7700") == "emergency"
    assert emergency_for_squawk("1200") == "none"
    assert emergency_for_squawk("0000") == "none"
    assert set(EMERGENCY_BY_SQUAWK) == {"7500", "7600", "7700"}


def test_type_table_is_non_empty_and_self_consistent() -> None:
    assert len(AIRCRAFT_TYPES) >= 15
    for key, spec in AIRCRAFT_TYPES.items():
        assert isinstance(spec, AircraftType)
        assert key == spec.icao
        assert re.fullmatch(r"[A-Z0-9]{4}", spec.icao)
        assert spec.min_ft <= spec.max_ft
        assert spec.max_ft <= MAX_ALT_FT
        assert spec.min_ft >= 0
        assert spec.climb_fpm > 0.0
        assert spec.descent_fpm > 0.0
        assert 0.0 < spec.rotate_kt < spec.cruise_kt
        assert re.fullmatch(r"A[0-7]", spec.category)


def test_every_type_turns_within_the_global_bound() -> None:
    fastest = max(spec.turn_deg_s for spec in AIRCRAFT_TYPES.values())
    assert fastest <= MAX_TURN_RATE_DEG_S
    assert fastest == pytest.approx(3.0)


def test_sim_config_defaults() -> None:
    config = SimConfig()
    assert config.seed is None
    assert config.count == 12
    assert config.interval == 1.0
    assert config.lat == 0.0
    assert config.lon == 0.0
    assert abs(config.start - time.time()) < 30.0


def test_sim_config_is_frozen() -> None:
    config = base_config()
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.count = 99  # type: ignore[misc]


def test_pinned_start_keeps_documents_reproducible() -> None:
    assert base_config().start == START
    assert base_config(start=123.0).start == 123.0


def test_on_ground_tracks_the_reported_altitude() -> None:
    assert not make_state().on_ground
    assert make_state(r_alt_ft=0.0).on_ground
    assert make_state(r_alt_ft=0.9).on_ground
    assert not make_state(r_alt_ft=1.0).on_ground
