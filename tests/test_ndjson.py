"""NDJSON recording: line shape, flushing, truncation and close semantics."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import START, base_config
from dump1090_simulator.ndjson import NdjsonRecorder, format_line
from dump1090_simulator.simulation import Simulation


def test_format_line_has_exactly_t_and_aircraft() -> None:
    line = format_line(START, [{"hex": "abc123", "alt_baro": "ground"}])
    assert "\n" not in line
    payload = json.loads(line)
    assert set(payload) == {"t", "aircraft"}
    assert payload["t"] == START
    assert payload["aircraft"] == [{"hex": "abc123", "alt_baro": "ground"}]


def test_format_line_round_trips_nested_fields() -> None:
    aircraft = [{"hex": "4ca918", "flight": "RYR22GK ", "nav_modes": ["LATLON", "NAV"]}]
    assert json.loads(format_line(1.5, aircraft))["aircraft"] == aircraft


def test_format_line_serialises_an_empty_snapshot() -> None:
    assert json.loads(format_line(2.0, [])) == {"t": 2.0, "aircraft": []}


def test_recorder_writes_one_flushed_line_per_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "capture.ndjson"
    recorder = NdjsonRecorder(path)
    recorder.record(START, [{"hex": "aaaaaa"}])
    assert path.read_text(encoding="utf-8") == format_line(START, [{"hex": "aaaaaa"}]) + "\n"
    recorder.record(START + 1.0, [{"hex": "bbbbbb"}])
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["t"] for line in lines] == [START, START + 1.0]
    assert "\r" not in path.read_text(encoding="utf-8")
    assert recorder.path == path
    recorder.close()


def test_recorder_truncates_an_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "capture.ndjson"
    path.write_text("stale line\nstale line\n", encoding="utf-8")
    with NdjsonRecorder(path) as recorder:
        recorder.record(START, [])
    assert path.read_text(encoding="utf-8") == format_line(START, []) + "\n"


def test_recorder_is_idempotent_on_close(tmp_path: Path) -> None:
    recorder = NdjsonRecorder(tmp_path / "capture.ndjson")
    recorder.close()
    recorder.close()
    with pytest.raises(ValueError, match="is closed"):
        recorder.record(1.0, [])


def test_context_manager_closes_the_file(tmp_path: Path) -> None:
    path = tmp_path / "capture.ndjson"
    with NdjsonRecorder(path) as recorder:
        recorder.record(START, [])
    with pytest.raises(ValueError, match="is closed"):
        recorder.record(START, [])


def test_recording_a_simulation_keeps_snapshot_semantics(tmp_path: Path) -> None:
    sim = Simulation(base_config(count=4))
    path = tmp_path / "capture.ndjson"
    with NdjsonRecorder(path) as recorder:
        for _ in range(10):
            sim.step(1.0)
            doc = sim.aircraft_doc()
            recorder.record(doc["now"], doc["aircraft"])
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 10
    assert [json.loads(line)["t"] for line in lines] == [START + step for step in range(1, 11)]
    for line in lines:
        payload = json.loads(line)
        assert set(payload) == {"t", "aircraft"}
        assert len(payload["aircraft"]) == 4
        for entry in payload["aircraft"]:
            assert isinstance(entry["alt_baro"], int) or entry["alt_baro"] == "ground"
            assert entry["seen"] <= entry["seen_pos"]
