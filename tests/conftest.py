"""Shared fixtures: the SPEC field set, pinned clocks, simulation factories."""

from __future__ import annotations

import socket
from collections.abc import Callable
from typing import Any

import pytest

from dump1090_simulator.models import AircraftState, Phase, SimConfig
from dump1090_simulator.simulation import Simulation

#: Aircraft.json fields the simulator is allowed to emit (SPEC subset).
SPEC_FIELDS: frozenset[str] = frozenset(
    {
        "hex",
        "type",
        "flight",
        "alt_baro",
        "alt_geom",
        "gs",
        "ias",
        "tas",
        "mach",
        "track",
        "track_rate",
        "roll",
        "mag_heading",
        "true_heading",
        "baro_rate",
        "geom_rate",
        "squawk",
        "emergency",
        "category",
        "lat",
        "lon",
        "nic",
        "rc",
        "seen_pos",
        "version",
        "nic_baro",
        "nac_p",
        "sil",
        "sil_type",
        "messages",
        "seen",
        "rssi",
        "dst",
        "dir",
        "r",
        "t",
    }
)

#: A pinned wall clock so every test shares one reproducible epoch.
START = 1_700_000_000.0

#: Receiver position used by the default test configuration (London-ish).
TEST_LAT = 51.5
TEST_LON = -0.12


def base_config(**overrides: Any) -> SimConfig:
    """The default test configuration with optional per-test overrides."""
    values: dict[str, Any] = {
        "seed": 42,
        "count": 8,
        "interval": 1.0,
        "lat": TEST_LAT,
        "lon": TEST_LON,
        "start": START,
    }
    values.update(overrides)
    return SimConfig(**values)


def make_state(**overrides: Any) -> AircraftState:
    """One airborne aircraft at cruise, with any field overridable."""
    values: dict[str, Any] = {
        "hex": "4ca918",
        "callsign": "RYR22GK",
        "icao_type": "B738",
        "registration": "EI-DYZ",
        "category": "A2",
        "squawk": "1200",
        "lat": TEST_LAT,
        "lon": TEST_LON,
        "alt_ft": 34000.0,
        "gs_kt": 450.0,
        "track_deg": 210.0,
        "target_track_deg": 210.0,
        "target_alt_ft": 34000,
        "vs_fpm": 0.0,
        "phase": Phase.CRUISE,
        "speed_factor": 1.0,
        "geom_offset": 40,
        "signal_bias": 0.0,
        "msg_interval": 5.0,
        "pos_interval": 15.0,
        "last_msg_t": START - 2.0,
        "last_pos_t": START - 4.0,
        "messages": 10,
        "r_lat": TEST_LAT,
        "r_lon": TEST_LON,
        "r_alt_ft": 34000.0,
    }
    values.update(overrides)
    return AircraftState(**values)


def free_port(host: str = "127.0.0.1") -> int:
    """A TCP port that was free a moment ago (for bind-it-yourself tests)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])
    finally:
        probe.close()


@pytest.fixture
def spec_fields() -> frozenset[str]:
    """Every field our aircraft entries may carry."""
    return SPEC_FIELDS


@pytest.fixture
def config() -> SimConfig:
    """The pinned default simulation configuration."""
    return base_config()


@pytest.fixture
def make_sim() -> Callable[..., Simulation]:
    """Factory building a simulation from the pinned defaults."""

    def factory(**overrides: Any) -> Simulation:
        return Simulation(base_config(**overrides))

    return factory
