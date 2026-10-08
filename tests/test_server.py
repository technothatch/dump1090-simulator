"""HTTP JSON serving: a threaded server on port 0, exercised with http.client."""

from __future__ import annotations

import http.client
import json
from collections.abc import Iterator

import pytest

from conftest import START, base_config
from dump1090_simulator.server import HttpServer
from dump1090_simulator.simulation import Simulation


def fetch(host: str, port: int, path: str) -> tuple[int, dict[str, str], bytes]:
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        body = response.read()
        headers = {name.lower(): value for name, value in response.getheaders()}
        return response.status, headers, body
    finally:
        conn.close()


@pytest.fixture
def endpoint() -> Iterator[tuple[str, int, Simulation]]:
    sim = Simulation(base_config())
    server = HttpServer(sim, port=0)
    server.start()
    try:
        yield ("127.0.0.1", server.port, sim)
    finally:
        server.close()


def test_aircraft_json_is_served_exactly_as_built(endpoint: tuple[str, int, Simulation]) -> None:
    host, port, sim = endpoint
    status, headers, body = fetch(host, port, "/data/aircraft.json")
    assert status == 200
    assert headers["content-type"] == "application/json"
    assert int(headers["content-length"]) == len(body)
    doc = json.loads(body)
    assert set(doc) == {"now", "messages", "aircraft"}
    assert doc == sim.aircraft_doc()
    assert doc["now"] == pytest.approx(START)


def test_receiver_json_is_served(endpoint: tuple[str, int, Simulation]) -> None:
    host, port, _sim = endpoint
    status, _, body = fetch(host, port, "/data/receiver.json")
    assert status == 200
    doc = json.loads(body)
    assert set(doc) == {"version", "refresh", "lat", "lon"}
    assert doc["version"].startswith("dump1090-simulator ")


def test_stats_json_carries_the_five_buckets(endpoint: tuple[str, int, Simulation]) -> None:
    host, port, _sim = endpoint
    status, _, body = fetch(host, port, "/data/stats.json")
    assert status == 200
    doc = json.loads(body)
    assert {"latest", "last1min", "last5min", "last15min", "total"} <= set(doc)
    assert "blocks_processed" in doc["latest"]["local"]
    assert doc["total"]["messages"]["total"] >= doc["last15min"]["messages"]["total"]


def test_unknown_paths_are_404(endpoint: tuple[str, int, Simulation]) -> None:
    host, port, _sim = endpoint
    for path in ("/", "/aircraft.json", "/data/nope.json", "/data/history_001.json"):
        status, _, _body = fetch(host, port, path)
        assert status == 404


def test_server_identifies_itself(endpoint: tuple[str, int, Simulation]) -> None:
    host, port, _sim = endpoint
    _status, headers, _body = fetch(host, port, "/data/aircraft.json")
    assert headers["server"].startswith("dump1090-simulator/")


def test_documents_advance_between_requests(endpoint: tuple[str, int, Simulation]) -> None:
    host, port, sim = endpoint
    _status, _headers, first = fetch(host, port, "/data/aircraft.json")
    sim.step(5.0)
    _status, _headers, second = fetch(host, port, "/data/aircraft.json")
    before = json.loads(first)["now"]
    after = json.loads(second)["now"]
    assert after == pytest.approx(before + 5.0)
    assert json.loads(second)["messages"] >= json.loads(first)["messages"]


def test_server_reports_its_ephemeral_port_and_url() -> None:
    sim = Simulation(base_config())
    server = HttpServer(sim, port=0)
    assert server.port > 0
    assert server.url == f"http://127.0.0.1:{server.port}"
    server.start()
    try:
        status, _headers, _body = fetch("127.0.0.1", server.port, "/data/receiver.json")
        assert status == 200
    finally:
        server.close()
        server.close()  # idempotent
