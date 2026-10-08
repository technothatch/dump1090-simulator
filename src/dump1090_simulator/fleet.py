"""Fleet generation: ICAO hexes, callsigns, squawks, registrations, spawn.

Every aircraft the simulator ever shows is born here: a unique 24-bit ICAO
address, a callsign drawn from real airline designators (general-aviation
aircraft use their registration instead, as they do in real life), a Mode A
code (occasionally 7700), and a position scattered around the receiver.

Spawn mixes phases deliberately — most aircraft are already at cruise, but
some are climbing, some descending and some parked — so a freshly started
``aircraft.json`` immediately exercises every branch of the physics loop.
"""

from __future__ import annotations

import random
import string

from dump1090_simulator.models import (
    AIRCRAFT_TYPES,
    AircraftState,
    AircraftType,
    Phase,
    SimConfig,
)
from dump1090_simulator.physics import destination_point

__all__ = [
    "AIRLINE_PREFIXES",
    "make_callsign",
    "make_hex",
    "make_registration",
    "make_squawk",
    "spawn_fleet",
]

#: ICAO airline designators the callsign generator draws from.
AIRLINE_PREFIXES: tuple[str, ...] = (
    "AAL",
    "AFR",
    "ANA",
    "ASA",
    "AWE",
    "BAW",
    "BCS",
    "CCA",
    "CES",
    "CFG",
    "CPA",
    "CSN",
    "DAL",
    "DLH",
    "ETD",
    "EZY",
    "EWG",
    "EXS",
    "FFT",
    "FIN",
    "IBE",
    "JAL",
    "JBU",
    "KLM",
    "LOT",
    "NKS",
    "QFA",
    "QTR",
    "RYR",
    "SAS",
    "SIA",
    "SKW",
    "SWA",
    "TAP",
    "THA",
    "TOM",
    "TRA",
    "TUI",
    "UAL",
    "UPS",
    "VYR",
    "WZZ",
)

#: Frequent normal-transponder codes; the rest are assigned per flight.
_NORMAL_SQUAWKS: tuple[str, ...] = ("1000", "1200", "2000", "3000", "4000", "7000")

#: Registration prefixes for non-US registrations (plus plain N-numbers).
_REG_PREFIXES: tuple[str, ...] = ("G-", "D-", "F-", "I-", "PH-", "EC-", "OE-", "CS-")

#: Letters that can appear in a flight-number suffix (no O/I confusion).
_SUFFIX_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"


def make_hex(rng: random.Random, used: set[str] | None = None) -> str:
    """A unique lowercase 24-bit ICAO address (never the all-zero code)."""
    taken = used if used is not None else set()
    while True:
        candidate = f"{rng.getrandbits(24):06x}"
        if candidate != "000000" and candidate not in taken:
            taken.add(candidate)
            return candidate


def make_registration(rng: random.Random) -> str:
    """A plausible registration: an N-number or a lettered European style."""
    if rng.random() < 0.5:
        return f"N{rng.randint(1, 99999)}"
    letters = "".join(rng.choices(string.ascii_uppercase, k=3))
    return f"{rng.choice(_REG_PREFIXES)}{letters}"


def make_callsign(rng: random.Random, category: str, registration: str) -> str:
    """An airline callsign; general-aviation categories use the registration."""
    if category in {"A0", "A1"}:
        return registration
    prefix = rng.choice(AIRLINE_PREFIXES)
    if rng.random() < 0.6:
        return f"{prefix}{rng.randint(1, 9999)}"
    letters = "".join(rng.choices(_SUFFIX_LETTERS, k=2))
    return f"{prefix}{rng.randint(10, 99)}{letters}"


def make_squawk(rng: random.Random) -> str:
    """A four-digit Mode A code: common pool, else a random octal assignment."""
    if rng.random() < 0.6:
        return rng.choice(_NORMAL_SQUAWKS)
    while True:
        code = "".join(rng.choice("01234567") for _ in range(4))
        if code != "0000":
            return code


def _cruise_level(spec: AircraftType, rng: random.Random) -> float:
    """A cruising altitude inside the type's band, rounded to a whole FL."""
    level = rng.uniform(float(spec.min_ft), float(spec.max_ft))
    return float(int(level) // 1000 * 1000)


def _spawn_aircraft(config: SimConfig, rng: random.Random, used_hexes: set[str]) -> AircraftState:
    spec = rng.choice(list(AIRCRAFT_TYPES.values()))
    registration = make_registration(rng)
    category = spec.category
    callsign = make_callsign(rng, category, registration)
    squawk = make_squawk(rng)
    if rng.random() < 0.03:
        squawk = "7700"

    start_level = _cruise_level(spec, rng)
    distance = rng.uniform(5.0, 300.0)
    bearing = rng.uniform(0.0, 360.0)
    lat, lon = destination_point(config.lat, config.lon, bearing, distance)

    track = rng.uniform(0.0, 360.0)
    speed_factor = rng.uniform(0.94, 1.06)
    cruise_gs = spec.cruise_kt * speed_factor
    msg_interval = config.interval * rng.uniform(1.0, 3.0)
    pos_interval = msg_interval * rng.uniform(2.0, 3.0)

    roll = rng.random()
    if roll < 0.15:
        phase = Phase.GROUND
        alt_ft = 0.0
        gs_kt = 0.0
        vs_fpm = 0.0
        target_alt = int(start_level)
        ground_seconds = rng.uniform(10.0, 90.0)
    elif roll < 0.30:
        phase = Phase.CLIMB
        alt_ft = rng.uniform(0.25, 0.9) * start_level
        gs_kt = cruise_gs * 0.8
        vs_fpm = spec.climb_fpm
        target_alt = int(start_level)
        ground_seconds = 0.0
    elif roll < 0.40:
        phase = Phase.DESCENT
        alt_ft = rng.uniform(2500.0, 15000.0)
        gs_kt = cruise_gs * 0.8
        vs_fpm = -spec.descent_fpm
        target_alt = 0
        ground_seconds = 0.0
    else:
        phase = Phase.CRUISE
        alt_ft = start_level
        gs_kt = cruise_gs
        vs_fpm = 0.0
        target_alt = int(start_level)
        ground_seconds = 0.0

    squawk_change_at: float | None = None
    if squawk != "7700" and rng.random() < 0.15:
        squawk_change_at = config.start + rng.uniform(5.0, 300.0)

    return AircraftState(
        hex=make_hex(rng, used_hexes),
        callsign=callsign,
        icao_type=spec.icao,
        registration=registration,
        category=category,
        squawk=squawk,
        lat=lat,
        lon=lon,
        alt_ft=alt_ft,
        gs_kt=gs_kt,
        track_deg=track,
        target_track_deg=track,
        target_alt_ft=target_alt,
        vs_fpm=vs_fpm,
        phase=phase,
        speed_factor=speed_factor,
        geom_offset=rng.randint(-150, 250),
        signal_bias=rng.uniform(-3.0, 3.0),
        msg_interval=msg_interval,
        pos_interval=pos_interval,
        last_msg_t=config.start - msg_interval,
        last_pos_t=config.start - pos_interval,
        messages=0,
        r_lat=lat,
        r_lon=lon,
        r_alt_ft=alt_ft,
        segment_seconds=rng.uniform(60.0, 300.0),
        ground_seconds=ground_seconds,
        squawk_change_at=squawk_change_at,
    )


def spawn_fleet(config: SimConfig, rng: random.Random) -> list[AircraftState]:
    """Build ``config.count`` aircraft around the configured receiver."""
    used: set[str] = set()
    return [_spawn_aircraft(config, rng, used) for _ in range(config.count)]
