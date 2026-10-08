"""Physics integration: finite values, bounded rates, no teleports.

The whole suite runs one long seeded simulation and checks the four
invariants the module promises: nothing goes NaN, altitude and speed stay
inside the performance envelope, the turn rate never exceeds the commanded
bound, and the distance covered per step is exactly ``gs x dt``.
"""

from __future__ import annotations

import functools
import math
from typing import NamedTuple

import pytest

from conftest import base_config
from dump1090_simulator.models import (
    AIRCRAFT_TYPES,
    MAX_ALT_FT,
    MAX_TURN_RATE_DEG_S,
    AircraftState,
    Phase,
)
from dump1090_simulator.physics import (
    NM_TO_KM,
    approach,
    destination_point,
    great_circle_km,
    initial_bearing_deg,
    normalize_degrees,
    signed_turn_deg,
    turn_toward,
)
from dump1090_simulator.simulation import Simulation

_COUNT = 8
_STEPS = 1200
_DT = 1.0

#: Fastest possible climb in the type table, feet/minute.
_MAX_CLIMB_FPM = max(spec.climb_fpm for spec in AIRCRAFT_TYPES.values())

#: Fastest possible ground speed (cruise x speed factor), knots.
_MAX_GS_KT = max(spec.cruise_kt for spec in AIRCRAFT_TYPES.values()) * 1.06


class Sample(NamedTuple):
    """One aircraft's state after a step, in fleet order."""

    hex: str
    icao_type: str
    lat: float
    lon: float
    alt_ft: float
    gs_kt: float
    track_deg: float
    track_rate: float
    vs_fpm: float
    phase: Phase


def _sample(ac: AircraftState) -> Sample:
    return Sample(
        hex=ac.hex,
        icao_type=ac.icao_type,
        lat=ac.lat,
        lon=ac.lon,
        alt_ft=ac.alt_ft,
        gs_kt=ac.gs_kt,
        track_deg=ac.track_deg,
        track_rate=ac.track_rate,
        vs_fpm=ac.vs_fpm,
        phase=ac.phase,
    )


@functools.cache
def integrate() -> tuple[tuple[Sample, ...], ...]:
    """Run the default fleet for 1200 seconds; indexed ``[step][aircraft]``."""
    sim = Simulation(base_config(count=_COUNT))
    history: list[tuple[Sample, ...]] = []
    for _ in range(_STEPS):
        sim.step(_DT)
        history.append(tuple(_sample(ac) for ac in sim.fleet))
    return tuple(history)


def _pairs() -> list[tuple[Sample, Sample]]:
    """Every consecutive pair of samples, flattened over steps and aircraft."""
    history = integrate()
    return [
        (history[step - 1][index], history[step][index])
        for step in range(1, len(history))
        for index in range(_COUNT)
    ]


def test_every_sample_is_finite() -> None:
    for history in integrate():
        for sample in history:
            assert math.isfinite(sample.lat)
            assert math.isfinite(sample.lon)
            assert math.isfinite(sample.alt_ft)
            assert math.isfinite(sample.gs_kt)
            assert math.isfinite(sample.track_deg)
            assert math.isfinite(sample.track_rate)
            assert math.isfinite(sample.vs_fpm)


def test_coordinates_stay_valid() -> None:
    for history in integrate():
        for sample in history:
            assert -90.0 <= sample.lat <= 90.0
            assert -180.0 <= sample.lon <= 180.0
            assert 0.0 <= sample.track_deg < 360.0


def test_altitude_never_leaves_the_0_to_fl410_envelope() -> None:
    for history in integrate():
        for sample in history:
            assert 0.0 <= sample.alt_ft <= MAX_ALT_FT


def test_ground_speed_stays_inside_the_type_envelope() -> None:
    for history in integrate():
        for sample in history:
            assert 0.0 <= sample.gs_kt <= _MAX_GS_KT + 1e-9


def test_turn_rate_never_exceeds_the_commanded_bound() -> None:
    for history in integrate():
        for sample in history:
            assert abs(sample.track_rate) <= MAX_TURN_RATE_DEG_S + 1e-9


def test_vertical_rate_stays_within_climb_and_descent_limits() -> None:
    limit = _MAX_CLIMB_FPM + 1e-9
    for history in integrate():
        for sample in history:
            assert -limit <= sample.vs_fpm <= limit


def test_altitude_changes_by_at_most_one_climb_minutes_worth_per_step() -> None:
    for previous, current in _pairs():
        assert abs(current.alt_ft - previous.alt_ft) <= _MAX_CLIMB_FPM * _DT / 60.0 + 1e-6


def test_positions_advance_by_exactly_ground_speed_times_dt() -> None:
    kt_to_km_s = NM_TO_KM / 3600.0
    for previous, current in _pairs():
        travelled = great_circle_km(previous.lat, previous.lon, current.lat, current.lon)
        expected = current.gs_kt * kt_to_km_s * _DT
        assert travelled == pytest.approx(expected, rel=1e-6, abs=1e-6)


def test_no_teleport_step_distance_is_bounded() -> None:
    for previous, current in _pairs():
        travelled = great_circle_km(previous.lat, previous.lon, current.lat, current.lon)
        assert travelled < 0.35  # km; 519 kt for one second is 0.268 km
        assert previous.hex == current.hex


def test_track_rate_is_the_observed_heading_change() -> None:
    for previous, current in _pairs():
        observed = signed_turn_deg(previous.track_deg, current.track_deg) / _DT
        assert current.track_rate == pytest.approx(observed, abs=1e-9)


def test_all_six_flight_phases_are_exercised() -> None:
    observed = {sample.phase for history in integrate() for sample in history}
    assert observed == set(Phase)


def test_parked_aircraft_sits_on_the_deck() -> None:
    for history in integrate():
        for sample in history:
            if sample.phase is Phase.GROUND:
                assert sample.alt_ft == 0.0
                assert sample.vs_fpm == 0.0


def test_cruise_level_stays_inside_the_type_band() -> None:
    for history in integrate():
        for sample in history:
            if sample.phase is Phase.CRUISE:
                spec = AIRCRAFT_TYPES[sample.icao_type]
                assert spec.min_ft <= sample.alt_ft <= spec.max_ft


def test_normalize_degrees_wraps_into_zero_inclusive_circle() -> None:
    assert normalize_degrees(0.0) == 0.0
    assert normalize_degrees(360.0) == 0.0
    assert normalize_degrees(361.5) == pytest.approx(1.5)
    assert normalize_degrees(-90.0) == pytest.approx(270.0)
    assert normalize_degrees(725.0) == pytest.approx(5.0)


def test_signed_turn_takes_the_shortest_way_round() -> None:
    assert signed_turn_deg(0.0, 90.0) == 90.0
    assert signed_turn_deg(0.0, 270.0) == -90.0
    assert signed_turn_deg(350.0, 10.0) == 20.0
    assert signed_turn_deg(10.0, 350.0) == -20.0
    assert signed_turn_deg(100.0, 100.0) == 0.0
    assert abs(signed_turn_deg(0.0, 180.0)) == 180.0


def test_approach_never_overshoots_its_target() -> None:
    assert approach(10.0, 100.0, rate=2.0, dt=1.0) == 12.0
    assert approach(10.0, 0.0, rate=3.0, dt=1.0) == 7.0
    assert approach(10.0, 11.0, rate=5.0, dt=1.0) == 11.0
    assert approach(10.0, 10.0, rate=5.0, dt=1.0) == 10.0
    assert approach(10.0, -5.0, rate=100.0, dt=1.0) == -5.0


def test_turn_toward_respects_the_rate_and_wraps() -> None:
    assert turn_toward(0.0, 90.0, rate=2.5, dt=1.0) == 2.5
    assert turn_toward(350.0, 10.0, rate=3.0, dt=1.0) == 353.0
    assert turn_toward(0.0, 90.0, rate=3.0, dt=100.0) == 90.0
    assert turn_toward(45.0, 45.0, rate=3.0, dt=1.0) == 45.0


def test_great_circle_distance_is_zero_for_identical_points() -> None:
    assert great_circle_km(51.5, -0.12, 51.5, -0.12) == 0.0


def test_one_degree_of_latitude_is_about_111_km() -> None:
    assert great_circle_km(0.0, 0.0, 1.0, 0.0) == pytest.approx(111.19, abs=0.1)
    assert great_circle_km(0.0, 0.0, 0.0, 1.0) == pytest.approx(111.19, abs=0.1)
    assert great_circle_km(10.0, 20.0, 30.0, 40.0) == pytest.approx(
        great_circle_km(30.0, 40.0, 10.0, 20.0)
    )


def test_destination_point_reproduces_the_requested_range_and_bearing() -> None:
    lat, lon = destination_point(51.5, -0.12, 73.0, 250.0)
    assert great_circle_km(51.5, -0.12, lat, lon) == pytest.approx(250.0, rel=1e-6)
    assert initial_bearing_deg(51.5, -0.12, lat, lon) == pytest.approx(73.0, abs=1e-6)


def test_destination_point_wraps_longitude_and_clamps_latitude() -> None:
    _lat, lon = destination_point(0.0, 179.0, 90.0, 1000.0)
    assert -180.0 <= lon <= 180.0
    assert lon == pytest.approx(-172.0, abs=0.5)
    polar_lat, polar_lon = destination_point(89.5, 10.0, 0.0, 50000.0)
    assert -90.0 <= polar_lat <= 90.0
    assert -180.0 <= polar_lon <= 180.0


def test_bearings_point_cardinal_directions() -> None:
    assert initial_bearing_deg(0.0, 0.0, 1.0, 0.0) == pytest.approx(0.0, abs=1e-6)
    assert initial_bearing_deg(0.0, 0.0, 0.0, 1.0) == pytest.approx(90.0, abs=1e-6)
    assert initial_bearing_deg(0.0, 0.0, -1.0, 0.0) == pytest.approx(180.0, abs=1e-6)


def test_identical_seeds_produce_identical_trajectories() -> None:
    def trail() -> list[tuple[float, float, float]]:
        sim = Simulation(base_config(count=3))
        out: list[tuple[float, float, float]] = []
        for _ in range(60):
            sim.step(1.0)
            out.append((sim.fleet[0].lat, sim.fleet[0].lon, sim.fleet[0].gs_kt))
        return out

    assert trail() == trail()
