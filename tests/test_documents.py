"""The three JSON documents: SPEC field contract, coherence and statistics."""

from __future__ import annotations

import pytest

from conftest import SPEC_FIELDS, START, TEST_LAT, TEST_LON, base_config, make_state
from dump1090_simulator.documents import (
    RECEIVER_REFRESH_MS,
    STATS_BUCKETS,
    WindowStats,
    aircraft_entry,
    build_aircraft_doc,
    build_receiver_doc,
    build_stats_doc,
    rssi_for_distance,
)
from dump1090_simulator.models import Phase
from dump1090_simulator.simulation import Simulation

_BUCKET_KEYS = {"start", "end", "local", "remote", "cpu", "cpr", "tracks", "messages"}
_LOCAL_KEYS = {
    "blocks_processed",
    "blocks_dropped",
    "modeac",
    "modes",
    "bad",
    "unknown_icao",
    "accepted",
    "signal",
    "peak_signal",
    "strong_signals",
}


def _entry(**overrides: object) -> dict[str, object]:
    """One aircraft entry at the pinned clock, with state overrides."""
    state = make_state(**overrides)
    return aircraft_entry(state, now=START, rx_lat=TEST_LAT, rx_lon=TEST_LON)


def _sim_with_traffic(steps: int, **overrides: object) -> Simulation:
    """A simulation stepped ``steps`` seconds so messages have been emitted."""
    sim = Simulation(base_config(**overrides))
    for _ in range(steps):
        sim.step(1.0)
    return sim


def test_entry_uses_only_the_spec_field_subset() -> None:
    entry = _entry()
    assert "hex" in entry
    assert set(entry) <= SPEC_FIELDS


def test_airborne_entry_carries_the_full_speed_block() -> None:
    entry = _entry()
    assert isinstance(entry["alt_baro"], int)
    assert entry["alt_baro"] == 34000
    assert {"alt_geom", "ias", "tas", "mach"} <= set(entry)
    assert entry["alt_geom"] == 34040
    assert entry["tas"] == 450
    assert 0 < entry["ias"] < entry["tas"]
    assert 0.3 < entry["mach"] < 1.0
    assert entry["nic"] == 10
    assert entry["rc"] == 75


def test_ground_entry_reports_ground_and_omits_airborne_fields() -> None:
    entry = _entry(alt_ft=0.0, r_alt_ft=0.0, gs_kt=0.0, phase=Phase.GROUND, vs_fpm=0.0)
    assert entry["alt_baro"] == "ground"
    assert not {"alt_geom", "ias", "tas", "mach"} & set(entry)
    assert entry["nic"] == 8
    assert entry["rc"] == 500
    assert entry["gs"] == 0.0
    assert entry["baro_rate"] == 0


def test_alt_baro_follows_the_reported_not_the_true_altitude() -> None:
    entry = _entry(alt_ft=34000.0, r_alt_ft=0.0)
    assert entry["alt_baro"] == "ground"
    airborne = _entry(alt_ft=0.0, r_alt_ft=5000.0)
    assert airborne["alt_baro"] == 5000


def test_seen_ages_are_relative_to_the_document_clock() -> None:
    entry = _entry()
    assert entry["seen"] == pytest.approx(2.0)
    assert entry["seen_pos"] == pytest.approx(4.0)
    assert entry["seen"] <= entry["seen_pos"]


def test_identity_fields_match_the_generated_aircraft() -> None:
    entry = _entry()
    assert entry["hex"] == "4ca918"
    assert entry["type"] == "B738"
    assert entry["t"] == "B738"
    assert entry["r"] == "EI-DYZ"
    assert entry["flight"] == "RYR22GK "
    assert len(entry["flight"]) == 8
    assert entry["squawk"] == "1200"
    assert entry["emergency"] == "none"
    assert entry["category"] == "A2"
    assert entry["messages"] == 10
    assert entry["version"] == 2
    assert entry["sil"] == 2
    assert entry["sil_type"] == "per-sample"
    assert entry["nac_p"] == 9
    assert entry["nic_baro"] == 1


@pytest.mark.parametrize(
    ("squawk", "emergency"),
    [("7500", "unlawful"), ("7600", "radio"), ("7700", "emergency"), ("1200", "none")],
)
def test_entry_emergency_follows_the_squawk(squawk: str, emergency: str) -> None:
    entry = _entry(squawk=squawk)
    assert entry["squawk"] == squawk
    assert entry["emergency"] == emergency


def test_entry_distance_and_bearing_come_from_the_reported_position() -> None:
    co_located = _entry()
    assert co_located["dst"] == 0.0
    assert co_located["dir"] == 0.0
    assert co_located["rssi"] == pytest.approx(rssi_for_distance(0.0))
    far = _entry(r_lat=TEST_LAT + 2.0)
    assert far["dst"] > co_located["dst"]
    assert far["dst"] > 0.0
    assert far["rssi"] < co_located["rssi"]
    assert 0.0 <= far["dir"] < 360.0


def test_entry_headings_and_bank_angle_are_consistent() -> None:
    entry = _entry(track_deg=210.0, track_rate=2.5, gs_kt=450.0)
    assert entry["track"] == 210.0
    assert entry["true_heading"] == 210.0
    assert entry["mag_heading"] == pytest.approx(205.0)
    assert entry["track_rate"] == 2.5
    assert entry["roll"] > 30.0


def test_build_aircraft_doc_shape_and_copy_semantics() -> None:
    entries: list[dict[str, str]] = [{"hex": "abc123"}]
    doc = build_aircraft_doc(12.3456789, 7, entries)
    assert set(doc) == {"now", "messages", "aircraft"}
    assert doc["now"] == 12.346
    assert doc["messages"] == 7
    assert isinstance(doc["messages"], int)
    entries.append({"hex": "ffffff"})
    assert len(doc["aircraft"]) == 1


def test_receiver_doc_contract() -> None:
    doc = build_receiver_doc(lat=TEST_LAT, lon=TEST_LON)
    assert set(doc) == {"version", "refresh", "lat", "lon"}
    assert doc["version"].startswith("dump1090-simulator ")
    assert doc["refresh"] == RECEIVER_REFRESH_MS == 1000
    assert (doc["lat"], doc["lon"]) == (TEST_LAT, TEST_LON)
    assert build_receiver_doc(lat=1.0, lon=2.0, refresh_ms=250)["refresh"] == 250


def test_stats_doc_has_dump1090s_five_buckets() -> None:
    windows = {name: WindowStats() for name, _ in STATS_BUCKETS}
    windows["total"] = WindowStats(messages=7, signal=-6.0, peak_signal=-2.5, strong_signals=2)
    doc = build_stats_doc(now=1000.0, started=400.0, windows=windows, aircraft_count=5)
    assert set(doc) == {name for name, _ in STATS_BUCKETS} | {"total"}
    for name, _window in STATS_BUCKETS:
        assert set(doc[name]) == _BUCKET_KEYS
        assert set(doc[name]["local"]) == _LOCAL_KEYS
        assert doc[name]["remote"] == {}


def test_stats_doc_windows_and_cpu_add_up() -> None:
    windows = {name: WindowStats() for name, _ in STATS_BUCKETS}
    windows["total"] = WindowStats(messages=7, signal=-6.0, peak_signal=-2.5, strong_signals=2)
    doc = build_stats_doc(now=1000.0, started=400.0, windows=windows, aircraft_count=5)
    assert doc["latest"]["start"] == 999.0
    assert doc["last15min"]["start"] == 400.0
    assert doc["total"]["start"] == 400.0
    for name, _window in STATS_BUCKETS:
        assert doc[name]["end"] == 1000.0
        cpu = doc[name]["cpu"]
        assert cpu["demod"] == 4.0
        assert cpu["reader"] == 1.5
        assert cpu["background"] == 0.5
        assert cpu["total"] == pytest.approx(cpu["demod"] + cpu["reader"] + cpu["background"])
        assert doc[name]["tracks"] == {"all": 5, "single_message": 0}
        assert len(doc[name]["local"]["accepted"]) == 8
        assert doc[name]["local"]["blocks_dropped"] == 0
        assert doc[name]["local"]["bad"] == 0
        assert doc[name]["cpr"]["received"] == doc[name]["cpr"]["hit"]
        assert doc[name]["cpr"]["hit"] == doc[name]["local"]["modes"]

    total = doc["total"]
    assert total["local"]["blocks_processed"] == 7
    assert total["local"]["modes"] == 7
    assert total["local"]["signal"] == -6.0
    assert total["local"]["peak_signal"] == -2.5
    assert total["local"]["strong_signals"] == 2
    assert total["messages"] == {"total": 7}
    assert doc["latest"]["local"]["blocks_processed"] == 0
    assert doc["latest"]["messages"] == {"total": 0}


def test_rssi_falls_off_monotonically_with_distance() -> None:
    levels = [rssi_for_distance(distance) for distance in (0.0, 1.0, 5.0, 25.0, 100.0, 400.0)]
    assert levels == sorted(levels, reverse=True)
    assert len(set(levels)) == len(levels)
    assert all(-45.0 <= level <= -1.0 for level in levels)


def test_rssi_is_clamped_to_the_decoder_range() -> None:
    assert rssi_for_distance(1e9) == -45.0
    assert rssi_for_distance(0.0, bias=50.0) == -1.0


def test_rssi_bias_shifts_the_level() -> None:
    expected = rssi_for_distance(10.0) + 2.0
    assert rssi_for_distance(10.0, 2.0) == pytest.approx(expected, abs=0.11)


def test_simulation_aircraft_doc_is_coherent() -> None:
    sim = _sim_with_traffic(90)
    doc = sim.aircraft_doc()
    assert set(doc) == {"now", "messages", "aircraft"}
    assert doc["now"] == pytest.approx(START + 90.0)
    assert doc["messages"] == sum(entry["messages"] for entry in doc["aircraft"])
    assert doc["messages"] > 0
    assert len(doc["aircraft"]) == 8
    for state, entry in zip(sim.fleet, doc["aircraft"], strict=True):
        value = entry["alt_baro"]
        assert type(value) is not bool
        if state.on_ground:
            assert value == "ground"
        else:
            assert isinstance(value, int)
            assert value == round(state.r_alt_ft)
        assert entry["seen"] <= entry["seen_pos"]
        assert 0.0 <= entry["seen"] <= 30.0
        assert set(entry) <= SPEC_FIELDS
        assert -45.0 <= entry["rssi"] <= -1.0


def test_simulation_clock_never_reads_wall_time() -> None:
    sim = _sim_with_traffic(45)
    assert sim.aircraft_doc()["now"] < 1_800_000_000.0
    assert sim.receiver_doc()["lat"] == TEST_LAT
    assert sim.receiver_doc()["refresh"] == RECEIVER_REFRESH_MS


def test_stats_buckets_are_nested_and_count_every_frame() -> None:
    sim = _sim_with_traffic(240)
    stats = sim.stats_doc()
    assert set(stats) == {name for name, _ in STATS_BUCKETS} | {"total"}
    counts = [stats[name]["local"]["blocks_processed"] for name, _ in STATS_BUCKETS]
    assert counts == sorted(counts)
    assert counts[-1] <= stats["total"]["local"]["blocks_processed"]
    total = stats["total"]["local"]
    assert total["blocks_processed"] == sim.aircraft_doc()["messages"]
    assert stats["total"]["messages"]["total"] == total["blocks_processed"]
    assert total["modes"] == total["blocks_processed"]
    assert total["accepted"][0] == total["blocks_processed"]
    assert 0 <= total["strong_signals"] <= total["blocks_processed"]
    assert total["peak_signal"] >= total["signal"]
    assert -45.0 <= total["signal"] <= -1.0
