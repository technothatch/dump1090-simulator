"""Assemble the three JSON documents a dump1090 / readsb decoder publishes.

``aircraft.json``, ``receiver.json`` and ``stats.json`` come out of the same
pure functions regardless of whether they are served over HTTP or written to
a recording, which is why they take plain values (clock, receiver position,
fleet) rather than the simulation itself.

Coherence rules the test suite enforces:

- ``now`` is the simulation clock, never wall time;
- ``seen`` / ``seen_pos`` are ages relative to ``now``, and a position message
  is always preceded by a state message, so ``seen <= seen_pos``;
- ``messages`` equals the sum of the per-aircraft counters;
- ``rssi`` is a strictly decreasing function of ``dst`` (plus a per-aircraft
  signal bias), and ``dst`` / ``dir`` come from the *reported* position.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from dump1090_simulator import __version__
from dump1090_simulator.models import AircraftState, emergency_for_squawk
from dump1090_simulator.physics import great_circle_km, initial_bearing_deg, normalize_degrees

__all__ = [
    "RECEIVER_REFRESH_MS",
    "STATS_BUCKETS",
    "WindowStats",
    "aircraft_entry",
    "build_aircraft_doc",
    "build_receiver_doc",
    "build_stats_doc",
    "rssi_for_distance",
]

#: stats.json bucket name -> window width in seconds (dump1090 layout).
STATS_BUCKETS: tuple[tuple[str, float], ...] = (
    ("latest", 1.0),
    ("last1min", 60.0),
    ("last5min", 300.0),
    ("last15min", 900.0),
)

#: receiver.json ``refresh`` — how often a browser page should poll, in
#: milliseconds (readsb uses 1000).
RECEIVER_REFRESH_MS = 1000

#: Fixed magnetic variation applied to derive mag_heading from true track.
_MAGNETIC_VARIATION_DEG = 5.0

#: Signal model: dBFS at 1 km plus a per-aircraft bias, floored/clamped so
#: values stay inside the range a real decoder reports.
_RSSI_AT_1KM = -2.0
_RSSI_MIN = -45.0
_RSSI_MAX = -1.0


@dataclass(frozen=True, slots=True)
class WindowStats:
    """Message count and signal aggregates for one stats.json time bucket.

    Derived from exactly the same event set (one event per emitted frame),
    so a bucket's message count and its signal statistics can never
    disagree with each other.
    """

    messages: int = 0
    signal: float = 0.0
    peak_signal: float = 0.0
    strong_signals: int = 0


def rssi_for_distance(dst_km: float, bias: float = 0.0) -> float:
    """Distance-dependent signal level: falls off logarithmically with range."""
    level = _RSSI_AT_1KM - 10.0 * math.log10(1.0 + max(dst_km, 0.0)) + bias
    return round(max(_RSSI_MIN, min(_RSSI_MAX, level)), 1)


def _tas(gs_kt: float) -> float:
    """True airspeed: no wind model, so TAS equals ground speed."""
    return gs_kt


def _ias(tas_kt: float, alt_ft: float) -> float:
    """Indicated airspeed from TAS through a simple exponential atmosphere."""
    return tas_kt * math.sqrt(math.exp(-max(alt_ft, 0.0) / 30000.0))


def _mach(tas_kt: float, alt_ft: float) -> float:
    """Mach number from TAS and the speed of sound at ``alt_ft``."""
    sound_kt = 661.5 * math.sqrt(max(0.1, 1.0 - 6.875e-6 * max(alt_ft, 0.0)))
    return tas_kt / sound_kt


def _roll_deg(track_rate_deg_s: float, gs_kt: float) -> float:
    """Bank angle implied by a coordinated turn at ``gs_kt``."""
    omega = math.radians(track_rate_deg_s)
    speed_ms = gs_kt * 0.514444
    return math.degrees(math.atan2(omega * speed_ms, 9.80665))


def aircraft_entry(
    ac: AircraftState, *, now: float, rx_lat: float, rx_lon: float
) -> dict[str, Any]:
    """One ``aircraft.json`` object for ``ac`` at simulation time ``now``."""
    dst = great_circle_km(rx_lat, rx_lon, ac.r_lat, ac.r_lon)
    bearing = initial_bearing_deg(rx_lat, rx_lon, ac.r_lat, ac.r_lon)
    on_ground = ac.on_ground
    tas = _tas(ac.gs_kt)

    entry: dict[str, Any] = {
        "hex": ac.hex,
        "type": ac.icao_type,
        "r": ac.registration,
        "t": ac.icao_type,
        "flight": ac.callsign.ljust(8),
        "alt_baro": "ground" if on_ground else round(ac.r_alt_ft),
        "gs": round(ac.gs_kt, 1),
        "track": round(ac.track_deg, 1),
        "track_rate": round(ac.track_rate, 2),
        "roll": round(_roll_deg(ac.track_rate, ac.gs_kt), 1),
        "true_heading": round(ac.track_deg, 1),
        "mag_heading": round(normalize_degrees(ac.track_deg - _MAGNETIC_VARIATION_DEG), 1),
        "baro_rate": round(ac.vs_fpm),
        "geom_rate": round(ac.vs_fpm),
        "squawk": ac.squawk,
        "emergency": emergency_for_squawk(ac.squawk),
        "category": ac.category,
        "lat": round(ac.r_lat, 6),
        "lon": round(ac.r_lon, 6),
        "nic": 8 if on_ground else 10,
        "rc": 500 if on_ground else 75,
        "seen_pos": round(max(0.0, now - ac.last_pos_t), 1),
        "seen": round(max(0.0, now - ac.last_msg_t), 1),
        "messages": ac.messages,
        "rssi": rssi_for_distance(dst, ac.signal_bias),
        "dst": round(dst, 2),
        "dir": round(bearing, 1),
        "version": 2,
        "sil": 2,
        "sil_type": "per-sample",
        "nac_p": 9,
        "nic_baro": 1,
    }
    if not on_ground:
        entry["alt_geom"] = round(ac.r_alt_ft + ac.geom_offset)
        entry["ias"] = round(_ias(tas, ac.r_alt_ft))
        entry["tas"] = round(tas)
        entry["mach"] = round(_mach(tas, ac.r_alt_ft), 3)
    return entry


def build_aircraft_doc(
    now: float, messages: int, entries: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    """The ``aircraft.json`` document: clock, cumulative counter, fleet."""
    return {"now": round(now, 3), "messages": int(messages), "aircraft": list(entries)}


def build_receiver_doc(
    *, lat: float, lon: float, refresh_ms: int = RECEIVER_REFRESH_MS
) -> dict[str, Any]:
    """The ``receiver.json`` document: identity, refresh period, antenna."""
    return {
        "version": f"dump1090-simulator {__version__}",
        "refresh": refresh_ms,
        "lat": lat,
        "lon": lon,
    }


def build_stats_doc(
    *,
    now: float,
    started: float,
    windows: Mapping[str, WindowStats],
    aircraft_count: int,
) -> dict[str, Any]:
    """The ``stats.json`` document with dump1090's five time buckets.

    Bucket aggregates are supplied by the caller for each of
    :data:`STATS_BUCKETS` plus ``total``; empty buckets simply report
    zeros, which is what a decoder publishes before its first message.
    """
    cpu = {
        "demod": round(2.0 + aircraft_count * 0.4, 1),
        "reader": round(1.0 + aircraft_count * 0.1, 1),
        "background": 0.5,
    }
    cpu["total"] = round(cpu["demod"] + cpu["reader"] + cpu["background"], 1)

    doc: dict[str, Any] = {}
    for name, window in (*STATS_BUCKETS, ("total", max(now - started, 1.0))):
        stats = windows[name]
        count = stats.messages
        doc[name] = {
            "start": round(max(now - window, started), 3),
            "end": round(now, 3),
            "local": {
                "blocks_processed": count,
                "blocks_dropped": 0,
                "modeac": 0,
                "modes": count,
                "bad": 0,
                "unknown_icao": 0,
                "accepted": [count, 0, 0, 0, 0, 0, 0, 0],
                "signal": stats.signal,
                "peak_signal": stats.peak_signal,
                "strong_signals": stats.strong_signals,
            },
            "remote": {},
            "cpu": dict(cpu),
            "cpr": {
                "received": count,
                "invalid": 0,
                "filtered": 0,
                "new_star": 0,
                "hit": count,
                "miss": 0,
                "dos": 0,
            },
            "tracks": {"all": aircraft_count, "single_message": 0},
            "messages": {"total": count},
        }
    return doc
