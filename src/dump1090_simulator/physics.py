"""Kinematics: bounded turns, realistic rates, takeoffs and landings.

The integration is deliberately simple and *bounded by construction*:

- ground speed only ever moves toward a phase target at a capped accel/decel;
- track only ever changes by ``type.turn_deg_s`` per second toward a target;
- altitude only ever changes by a bounded feet-per-minute rate;
- positions advance with the haversine destination formula, so the distance
  between two consecutive positions is exactly ``gs x dt``.

Those four invariants are what the physics test suite hammers on: no NaNs,
no teleports, no altitude or turn-rate excursions over thousands of steps.
"""

from __future__ import annotations

import math
import random

from dump1090_simulator.models import (
    AIRCRAFT_TYPES,
    MAX_ALT_FT,
    AircraftState,
    Phase,
)

__all__ = [
    "EARTH_RADIUS_KM",
    "NM_TO_KM",
    "approach",
    "destination_point",
    "great_circle_km",
    "initial_bearing_deg",
    "normalize_degrees",
    "signed_turn_deg",
    "step_aircraft",
    "turn_toward",
]

#: Mean Earth radius, kilometres (WGS-84 mean).
EARTH_RADIUS_KM = 6371.0088

#: One nautical mile in kilometres.
NM_TO_KM = 1.852

#: One knot in kilometres per second (knots x dt / 3600 x NM_TO_KM).
_KT_TO_KM_S = NM_TO_KM / 3600.0

#: Acceleration limits, knots per second, by flight regime.
_ACCEL_TAKEOFF = 3.0
_ACCEL_AIR = 2.0
_DECEL_LANDING = 5.0
_DECEL_GROUND = 6.0

#: Feet per minute used for the final landing segment.
_LANDING_FPM = -700.0


def normalize_degrees(deg: float) -> float:
    """Wrap an angle into ``[0, 360)``."""
    return deg % 360.0


def signed_turn_deg(current: float, target: float) -> float:
    """Shortest signed turn from ``current`` to ``target``, in ``(-180, 180]``."""
    return (target - current + 180.0) % 360.0 - 180.0


def approach(current: float, target: float, rate: float, dt: float) -> float:
    """Move ``current`` toward ``target`` by at most ``rate * dt``."""
    delta = target - current
    limit = rate * dt
    if delta > limit:
        return current + limit
    if delta < -limit:
        return current - limit
    return target


def turn_toward(track: float, target: float, rate: float, dt: float) -> float:
    """Turn ``track`` toward ``target`` at no more than ``rate`` deg/s."""
    diff = signed_turn_deg(track, target)
    step = rate * dt
    if abs(diff) <= step:
        return normalize_degrees(target)
    return normalize_degrees(track + math.copysign(step, diff))


def great_circle_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, in kilometres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, a)))


def initial_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing from point 1 to point 2, degrees true."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlam = math.radians(lon2 - lon1)
    y = math.sin(dlam) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    return normalize_degrees(math.degrees(math.atan2(y, x)))


def destination_point(
    lat: float, lon: float, bearing_deg: float, dist_km: float
) -> tuple[float, float]:
    """Point reached flying ``dist_km`` along ``bearing_deg`` from (lat, lon)."""
    delta = dist_km / EARTH_RADIUS_KM
    theta = math.radians(bearing_deg)
    phi1 = math.radians(lat)
    lam1 = math.radians(lon)
    phi2 = math.asin(
        math.sin(phi1) * math.cos(delta) + math.cos(phi1) * math.sin(delta) * math.cos(theta)
    )
    lam2 = lam1 + math.atan2(
        math.sin(theta) * math.sin(delta) * math.cos(phi1),
        math.cos(delta) - math.sin(phi1) * math.sin(phi2),
    )
    new_lat = max(-90.0, min(90.0, math.degrees(phi2)))
    new_lon = (math.degrees(lam2) + 540.0) % 360.0 - 180.0
    return new_lat, new_lon


def _move(ac: AircraftState, dt: float) -> None:
    """Advance the true position ``gs x dt`` along the current track."""
    distance_km = ac.gs_kt * _KT_TO_KM_S * dt
    ac.lat, ac.lon = destination_point(ac.lat, ac.lon, ac.track_deg, distance_km)


def step_aircraft(ac: AircraftState, dt: float, rng: random.Random) -> None:
    """Advance one aircraft by ``dt`` seconds through its flight phase.

    Args:
        ac: The aircraft state, mutated in place.
        dt: Step length in seconds; must be positive (``track_rate`` is
            computed from the step).
        rng: The simulation's seeded random source (segment decisions,
            takeoff headings, parking durations), so a seed reproduces a run.
    """
    spec = AIRCRAFT_TYPES[ac.icao_type]
    before_track = ac.track_deg

    if ac.phase is Phase.GROUND:
        ac.gs_kt = approach(ac.gs_kt, 0.0, _DECEL_GROUND, dt)
        ac.alt_ft = 0.0
        ac.vs_fpm = 0.0
        ac.target_track_deg = ac.track_deg
        ac.phase_elapsed += dt
        if ac.phase_elapsed >= ac.ground_seconds:
            ac.phase = Phase.TAKEOFF
            ac.phase_elapsed = 0.0

    elif ac.phase is Phase.TAKEOFF:
        ac.gs_kt = approach(ac.gs_kt, spec.rotate_kt, _ACCEL_TAKEOFF, dt)
        ac.alt_ft = 0.0
        ac.vs_fpm = 0.0
        ac.phase_elapsed += dt
        if ac.gs_kt >= spec.rotate_kt - 0.5:
            ac.phase = Phase.CLIMB
            ac.phase_elapsed = 0.0
            ac.vs_fpm = spec.climb_fpm
            if ac.target_alt_ft <= 0:
                # Departing after a landing: the leftover target was the
                # runway, so pick a fresh cruising level in the type's band.
                level = rng.uniform(float(spec.min_ft), float(spec.max_ft))
                ac.target_alt_ft = int(level // 1000 * 1000)
            # Depart the runway heading: bounded like every other turn.
            ac.target_track_deg = normalize_degrees(
                ac.track_deg + rng.choice((-1.0, 1.0)) * rng.uniform(25.0, 120.0)
            )

    elif ac.phase is Phase.CLIMB:
        cruise = spec.cruise_kt * ac.speed_factor
        ac.gs_kt = approach(ac.gs_kt, cruise * 0.85, _ACCEL_AIR, dt)
        ac.vs_fpm = spec.climb_fpm
        ac.track_deg = turn_toward(ac.track_deg, ac.target_track_deg, spec.turn_deg_s, dt)
        ac.alt_ft += ac.vs_fpm * dt / 60.0
        if ac.alt_ft >= float(ac.target_alt_ft):
            ac.alt_ft = float(ac.target_alt_ft)
            ac.vs_fpm = 0.0
            ac.phase = Phase.CRUISE
            ac.phase_elapsed = 0.0
            ac.segment_elapsed = 0.0

    elif ac.phase is Phase.CRUISE:
        cruise = spec.cruise_kt * ac.speed_factor
        ac.gs_kt = approach(ac.gs_kt, cruise, _ACCEL_AIR, dt)
        ac.vs_fpm = 0.0
        ac.alt_ft = float(ac.target_alt_ft)
        ac.track_deg = turn_toward(ac.track_deg, ac.target_track_deg, spec.turn_deg_s, dt)
        ac.segment_elapsed += dt
        if ac.segment_elapsed >= ac.segment_seconds:
            ac.segment_elapsed = 0.0
            ac.segment_seconds = rng.uniform(60.0, 300.0)
            roll = rng.random()
            if roll < 0.25:
                # Head for the field: descend all the way to landing.
                ac.target_alt_ft = 0
                ac.phase = Phase.DESCENT
                ac.vs_fpm = -spec.descent_fpm
            elif roll < 0.40:
                # Step climb / step descent to another cruising level.
                candidate = float(
                    max(
                        spec.min_ft,
                        min(spec.max_ft, int(ac.alt_ft) + int(rng.choice((-4000, 4000)))),
                    )
                )
                ac.target_alt_ft = int(candidate)
                if candidate > ac.alt_ft:
                    ac.phase = Phase.CLIMB
                    ac.vs_fpm = spec.climb_fpm
                elif candidate < ac.alt_ft:
                    ac.phase = Phase.DESCENT
                    ac.vs_fpm = -spec.descent_fpm
            else:
                # Constant-track segment ends; pick a new one.
                ac.target_track_deg = normalize_degrees(
                    ac.track_deg + rng.choice((-1.0, 1.0)) * rng.uniform(20.0, 160.0)
                )

    elif ac.phase is Phase.DESCENT:
        landing = ac.target_alt_ft == 0
        if landing:
            target_gs = spec.rotate_kt + (20.0 if ac.alt_ft >= 5000.0 else 8.0)
        else:
            target_gs = spec.cruise_kt * ac.speed_factor * 0.85
        ac.gs_kt = approach(ac.gs_kt, target_gs, _ACCEL_AIR, dt)
        ac.vs_fpm = -spec.descent_fpm
        ac.track_deg = turn_toward(ac.track_deg, ac.target_track_deg, spec.turn_deg_s, dt)
        new_alt = ac.alt_ft + ac.vs_fpm * dt / 60.0
        if landing and new_alt <= 1000.0:
            ac.alt_ft = max(new_alt, 0.0)
            ac.phase = Phase.LANDING
            ac.vs_fpm = _LANDING_FPM
        elif not landing and new_alt <= float(ac.target_alt_ft):
            ac.alt_ft = float(ac.target_alt_ft)
            ac.vs_fpm = 0.0
            ac.phase = Phase.CRUISE
            ac.phase_elapsed = 0.0
        else:
            ac.alt_ft = new_alt

    elif ac.phase is Phase.LANDING:
        ac.gs_kt = approach(ac.gs_kt, spec.rotate_kt * 0.9, _DECEL_LANDING, dt)
        ac.vs_fpm = _LANDING_FPM
        ac.track_deg = turn_toward(ac.track_deg, ac.target_track_deg, spec.turn_deg_s, dt)
        new_alt = ac.alt_ft + ac.vs_fpm * dt / 60.0
        ac.phase_elapsed += dt
        if new_alt <= 0.0:
            ac.alt_ft = 0.0
            ac.vs_fpm = 0.0
            ac.target_track_deg = ac.track_deg
            ac.phase = Phase.GROUND
            ac.phase_elapsed = 0.0
            ac.ground_seconds = rng.uniform(45.0, 180.0)
        else:
            ac.alt_ft = new_alt

    ac.alt_ft = max(0.0, min(MAX_ALT_FT, ac.alt_ft))
    ac.gs_kt = max(0.0, ac.gs_kt)
    ac.track_deg = normalize_degrees(ac.track_deg)
    ac.track_rate = signed_turn_deg(before_track, ac.track_deg) / dt
    _move(ac, dt)
