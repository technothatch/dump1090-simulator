"""The simulation loop: clock, message emission, documents, stats windows.

:class:`Simulation` owns the fleet and the single source of truth for time.
Each :meth:`Simulation.step` call advances the clock, integrates the fleet,
emits whatever messages are due as Beast frames, and rebuilds the three JSON
documents. Consumers (HTTP, Beast broadcast, NDJSON recording) only ever
read the built documents under the instance lock.

Determinism rules:

- the only clock is ``config.start`` plus the ``dt`` values passed to
  ``step`` — wall time never appears;
- one seeded :class:`random.Random` drives spawn, physics and squawk
  changes, so a seed plus a step sequence reproduces byte-identical frames;
- message emission is a pure comparison of ``now`` against per-aircraft
  intervals, so no jitter enters from timers or threads.

Coherence rules the test suite enforces:

- the number of frames returned by ``step`` equals the growth of the
  document's ``messages`` counter, which equals the sum of the per-aircraft
  counters;
- a position message updates ``r_lat``/``r_lon``/``r_alt_ft`` and
  ``last_pos_t`` *and* counts as a state message, so ``seen <= seen_pos``
  always holds in ``aircraft.json``;
- stats buckets aggregate exactly the emitted frames (one event per frame),
  giving ``latest <= last1min <= last5min <= last15min <= total``.
"""

from __future__ import annotations

import random
import threading
from collections import deque
from typing import Any

from dump1090_simulator.beast import Frame, encode_frame, signal_byte
from dump1090_simulator.documents import (
    STATS_BUCKETS,
    WindowStats,
    aircraft_entry,
    build_aircraft_doc,
    build_receiver_doc,
    build_stats_doc,
    rssi_for_distance,
)
from dump1090_simulator.fleet import make_squawk, spawn_fleet
from dump1090_simulator.models import AircraftState, SimConfig
from dump1090_simulator.physics import great_circle_km, step_aircraft
from dump1090_simulator.transponder import df17_identity_payload, df17_payload

__all__ = ["MAX_EVENT_WINDOW_S", "STRONG_RSSI_DBFS", "Simulation"]

#: Longest stats bucket (last15min) — events older than this are pruned so
#: memory stays bounded during long runs.
MAX_EVENT_WINDOW_S = 900.0

#: Signal levels at or above this count towards ``strong_signals``.
STRONG_RSSI_DBFS = -10.0

#: One recorded event per emitted frame: (simulation time, rssi dBFS).
_Event = tuple[float, float]


class Simulation:
    """Deterministic, thread-safe synthetic ADS-B traffic source.

    Args:
        config: Fleet size, receiver position, message cadence and seed.
    """

    def __init__(self, config: SimConfig) -> None:
        self.config = config
        self.now = config.start
        self.lock = threading.Lock()
        self._rng = random.Random(config.seed)
        self.fleet = spawn_fleet(config, self._rng)
        self._events: deque[_Event] = deque()
        self._total_count = 0
        self._total_sum = 0.0
        self._total_peak = float("-inf")
        self._total_strong = 0
        self._receiver_doc = build_receiver_doc(lat=config.lat, lon=config.lon)
        self._aircraft_doc: dict[str, Any] = {}
        self._stats_doc: dict[str, Any] = {}
        self._rebuild_docs()

    # ------------------------------------------------------------------ read

    def aircraft_doc(self) -> dict[str, Any]:
        """The current ``aircraft.json`` document (treat as read-only)."""
        with self.lock:
            return self._aircraft_doc

    def stats_doc(self) -> dict[str, Any]:
        """The current ``stats.json`` document (treat as read-only)."""
        with self.lock:
            return self._stats_doc

    def receiver_doc(self) -> dict[str, Any]:
        """The static ``receiver.json`` document (treat as read-only)."""
        return self._receiver_doc

    # ------------------------------------------------------------------ step

    def step(self, dt: float) -> list[bytes]:
        """Advance the clock by ``dt`` seconds and return Beast wire bytes.

        Args:
            dt: Step length in seconds; must be positive (the clock only
                ever moves forward, and physics rates assume it).

        Returns:
            One encoded Beast frame per message emitted during this step.

        Raises:
            ValueError: ``dt`` is not positive.
        """
        if dt <= 0.0:
            raise ValueError(f"step dt must be positive, got {dt}")
        with self.lock:
            self.now += dt
            now = self.now
            frames: list[Frame] = []
            for ac in self.fleet:
                step_aircraft(ac, dt, self._rng)
                self._maybe_change_squawk(ac, now)
                frame = self._maybe_emit(ac, now)
                if frame is not None:
                    frames.append(frame)
            self._prune_events(now)
            self._rebuild_docs()
        return [encode_frame(frame) for frame in frames]

    # -------------------------------------------------------------- internals

    def _maybe_change_squawk(self, ac: AircraftState, now: float) -> None:
        """Fire a scheduled one-shot Mode A change (never onto 7700's twin)."""
        if ac.squawk_change_at is not None and now >= ac.squawk_change_at:
            ac.squawk = make_squawk(self._rng)
            ac.squawk_change_at = None

    def _maybe_emit(self, ac: AircraftState, now: float) -> Frame | None:
        """Build the state or position message due at ``now``, if any."""
        due_position = now - ac.last_pos_t >= ac.pos_interval
        if due_position:
            payload = df17_payload(ac.hex, ac.alt_ft, ac.lat, ac.lon, not ac.cpr_odd)
            ac.cpr_odd = not ac.cpr_odd
            ac.r_lat = ac.lat
            ac.r_lon = ac.lon
            ac.r_alt_ft = ac.alt_ft
            ac.last_pos_t = now
        elif now - ac.last_msg_t >= ac.msg_interval:
            payload = df17_identity_payload(ac.hex, ac.category, ac.callsign)
        else:
            return None

        # A position message doubles as the state message: ages stay ordered.
        ac.last_msg_t = now
        ac.messages += 1

        dst = great_circle_km(self.config.lat, self.config.lon, ac.r_lat, ac.r_lon)
        rssi = rssi_for_distance(dst, ac.signal_bias)
        self._record_event(now, rssi)
        return Frame(
            msg_type="3",
            mlat=int((now - self.config.start) * 1_000_000) & 0xFFFFFFFFFFFF,
            signal=signal_byte(rssi),
            payload=payload,
        )

    def _record_event(self, now: float, rssi: float) -> None:
        """Log one emitted frame for the stats windows and totals."""
        self._events.append((now, rssi))
        self._total_count += 1
        self._total_sum += rssi
        self._total_peak = rssi if self._total_count == 1 else max(self._total_peak, rssi)
        if rssi >= STRONG_RSSI_DBFS:
            self._total_strong += 1

    def _prune_events(self, now: float) -> None:
        """Drop events no stats bucket can still see."""
        cutoff = now - MAX_EVENT_WINDOW_S
        while self._events and self._events[0][0] <= cutoff:
            self._events.popleft()

    @staticmethod
    def _aggregate(rssi_values: list[float]) -> WindowStats:
        """Fold the rssi values of one bucket into its :class:`WindowStats`."""
        if not rssi_values:
            return WindowStats()
        return WindowStats(
            messages=len(rssi_values),
            signal=round(sum(rssi_values) / len(rssi_values), 1),
            peak_signal=round(max(rssi_values), 1),
            strong_signals=sum(1 for value in rssi_values if value >= STRONG_RSSI_DBFS),
        )

    def _window_stats(self, cutoff: float) -> WindowStats:
        """Aggregates for every retained event strictly after ``cutoff``."""
        return self._aggregate([rssi for t, rssi in self._events if t > cutoff])

    def _total_stats(self) -> WindowStats:
        """Aggregates over every frame ever emitted (never pruned)."""
        if self._total_count == 0:
            return WindowStats()
        return WindowStats(
            messages=self._total_count,
            signal=round(self._total_sum / self._total_count, 1),
            peak_signal=round(self._total_peak, 1),
            strong_signals=self._total_strong,
        )

    def _build_windows(self) -> dict[str, WindowStats]:
        """The five stats buckets keyed the way stats.json spells them."""
        windows = {name: self._window_stats(self.now - window) for name, window in STATS_BUCKETS}
        windows["total"] = self._total_stats()
        return windows

    def _rebuild_docs(self) -> None:
        """Recompute aircraft.json and stats.json from the current state."""
        entries = [
            aircraft_entry(ac, now=self.now, rx_lat=self.config.lat, rx_lon=self.config.lon)
            for ac in self.fleet
        ]
        self._aircraft_doc = build_aircraft_doc(self.now, self._total_count, entries)
        self._stats_doc = build_stats_doc(
            now=self.now,
            started=self.config.start,
            windows=self._build_windows(),
            aircraft_count=len(self.fleet),
        )
