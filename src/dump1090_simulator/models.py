"""Configuration and state model for the synthetic traffic generator.

Everything the simulator needs to know lives here: the per-aircraft state
that the physics loop mutates, the per-type performance table the physics
loop reads, and the top-level :class:`SimConfig` built from CLI flags.

The state deliberately mirrors what a real dump1090 / readsb track holds
(true position, last *reported* position, message ages, cumulative message
count) so the JSON documents can be assembled without reaching back into
the physics loop.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field

__all__ = [
    "AIRCRAFT_TYPES",
    "EMERGENCY_BY_SQUAWK",
    "MAX_ALT_FT",
    "MAX_TURN_RATE_DEG_S",
    "AircraftState",
    "AircraftType",
    "Phase",
    "SimConfig",
    "emergency_for_squawk",
]

#: Highest altitude the simulator produces, in feet (FL410).
MAX_ALT_FT = 41000.0

#: Hard bound on commanded turn rate, degrees/second (every entry of
#: :data:`AIRCRAFT_TYPES` sits at or below it; tests assert the bound).
MAX_TURN_RATE_DEG_S = 3.0

#: Emergency field values dump1090 publishes for the emergency squawks.
EMERGENCY_BY_SQUAWK: dict[str, str] = {
    "7500": "unlawful",
    "7600": "radio",
    "7700": "emergency",
}


class Phase(enum.Enum):
    """Where an aircraft is in its takeoff → cruise → landing cycle."""

    GROUND = "ground"
    TAKEOFF = "takeoff"
    CLIMB = "climb"
    CRUISE = "cruise"
    DESCENT = "descent"
    LANDING = "landing"


def emergency_for_squawk(squawk: str) -> str:
    """Map a Mode A code to dump1090's ``emergency`` field value."""
    return EMERGENCY_BY_SQUAWK.get(squawk, "none")


@dataclass(frozen=True, slots=True)
class AircraftType:
    """Performance envelope for one ICAO aircraft type.

    Speeds are knots true airspeed at cruise, rates are feet/minute, the
    altitude band is the cruise band the generator picks levels from, and
    ``turn_deg_s`` is the commanded turn rate (also the per-step bound the
    physics tests assert).
    """

    icao: str
    cruise_kt: float
    min_ft: int
    max_ft: int
    climb_fpm: float
    descent_fpm: float
    turn_deg_s: float
    rotate_kt: float
    category: str


def _type(
    icao: str,
    cruise: int,
    band: tuple[int, int],
    climb: int,
    descent: int,
    turn: float,
    rotate: int,
    category: str,
) -> AircraftType:
    return AircraftType(
        icao=icao,
        cruise_kt=float(cruise),
        min_ft=band[0],
        max_ft=band[1],
        climb_fpm=float(climb),
        descent_fpm=float(descent),
        turn_deg_s=turn,
        rotate_kt=float(rotate),
        category=category,
    )


#: Per-type speed/altitude table used for spawn and every physics step.
AIRCRAFT_TYPES: dict[str, AircraftType] = {
    t.icao: t
    for t in (
        _type("A319", 450, (24000, 41000), 2200, 1700, 2.5, 140, "A2"),
        _type("A320", 450, (24000, 41000), 2200, 1700, 2.5, 145, "A2"),
        _type("A321", 460, (24000, 41000), 2100, 1600, 2.5, 150, "A2"),
        _type("A21N", 460, (24000, 41000), 2100, 1600, 2.5, 150, "A2"),
        _type("B738", 455, (24000, 41000), 2300, 1700, 2.5, 145, "A2"),
        _type("B739", 460, (24000, 41000), 2200, 1700, 2.5, 150, "A2"),
        _type("B77W", 490, (31000, 41000), 2400, 1800, 2.0, 160, "A3"),
        _type("B789", 490, (32000, 41000), 2500, 1900, 2.0, 160, "A3"),
        _type("B763", 470, (24000, 41000), 2200, 1800, 2.2, 155, "A3"),
        _type("A333", 480, (28000, 41000), 2300, 1800, 2.0, 155, "A3"),
        _type("A359", 490, (30000, 41000), 2500, 1900, 2.0, 160, "A3"),
        _type("MD11", 490, (24000, 41000), 2200, 1800, 2.2, 170, "A3"),
        _type("E190", 440, (20000, 41000), 2000, 1600, 2.5, 135, "A2"),
        _type("DH8D", 300, (15000, 25000), 1600, 1400, 2.5, 110, "A1"),
        _type("AT72", 280, (15000, 25000), 1500, 1300, 2.5, 105, "A1"),
        _type("C172", 120, (2000, 11000), 700, 600, 3.0, 55, "A0"),
        _type("PA28", 110, (2000, 10000), 700, 600, 3.0, 55, "A0"),
        _type("BE58", 200, (4000, 16000), 1200, 900, 3.0, 85, "A0"),
        _type("SR22", 175, (3000, 14000), 1000, 800, 3.0, 75, "A0"),
        _type("PC12", 260, (6000, 26000), 1600, 1300, 3.0, 100, "A1"),
    )
}


@dataclass(slots=True)
class AircraftState:
    """Mutable kinematic + bookkeeping state for one simulated aircraft.

    Position fields (``lat``/``lon``/``alt_ft``) are the *true* state owned
    by the physics loop; ``r_lat``/``r_lon``/``r_alt_ft`` are what the last
    position message reported and therefore what ``aircraft.json`` serves —
    the same lag a real decoder exhibits through ``seen_pos``.

    Message bookkeeping (``last_msg_t``/``last_pos_t``/``messages``) is
    maintained by :mod:`dump1090_simulator.simulation`, which is also the
    only writer of ``track_rate`` (the observed rate of change of
    ``track_deg`` over the previous step).
    """

    hex: str
    callsign: str
    icao_type: str
    registration: str
    category: str
    squawk: str
    lat: float
    lon: float
    alt_ft: float
    gs_kt: float
    track_deg: float
    target_track_deg: float
    target_alt_ft: int
    vs_fpm: float
    phase: Phase
    speed_factor: float
    geom_offset: int
    signal_bias: float
    msg_interval: float
    pos_interval: float
    last_msg_t: float
    last_pos_t: float
    messages: int
    r_lat: float
    r_lon: float
    r_alt_ft: float
    phase_elapsed: float = 0.0
    segment_elapsed: float = 0.0
    segment_seconds: float = 0.0
    ground_seconds: float = 0.0
    squawk_change_at: float | None = None
    cpr_odd: bool = False
    track_rate: float = 0.0

    @property
    def on_ground(self) -> bool:
        """True when the last reported altitude is on the deck."""
        return self.r_alt_ft < 1.0


@dataclass(frozen=True, slots=True)
class SimConfig:
    """Everything needed to build a deterministic simulation.

    ``start`` defaults to wall time but tests pin it so ``now`` in the
    generated documents is reproducible; ``interval`` is the base state
    message period the per-aircraft intervals are derived from.
    """

    seed: int | None = None
    count: int = 12
    interval: float = 1.0
    lat: float = 0.0
    lon: float = 0.0
    start: float = field(default_factory=time.time)
