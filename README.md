# dump1090-simulator

[![CI](https://github.com/technothatch/dump1090-simulator/actions/workflows/ci.yml/badge.svg)](https://github.com/technothatch/dump1090-simulator/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/technothatch/dump1090-simulator/blob/main/LICENSE)
[![Latest Release](https://img.shields.io/github/v/release/technothatch/dump1090-simulator)](https://github.com/technothatch/dump1090-simulator/releases)
[![Issues](https://img.shields.io/github/issues/technothatch/dump1090-simulator)](https://github.com/technothatch/dump1090-simulator/issues)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/technothatch/dump1090-simulator/pulls)
[![Code size](https://img.shields.io/github/languages/code-size/technothatch/dump1090-simulator)](https://github.com/technothatch/dump1090-simulator)
[![Last commit](https://img.shields.io/github/last-commit/technothatch/dump1090-simulator)](https://github.com/technothatch/dump1090-simulator/commits/main)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Coverage](https://img.shields.io/badge/coverage-99%25-brightgreen.svg)](https://github.com/technothatch/dump1090-simulator)

A deterministic, stdlib-only generator of synthetic ADS-B traffic for the
[dump1090](https://github.com/flightaware/dump1090) /
[readsb](https://github.com/wiedehopf/readsb) ecosystem. Real receivers need an
RTL-SDR, an antenna and sky; this needs a seed. It flies a kinematically
plausible fleet (climbs at real fpm, turns at a bounded rate, takes off and
lands) and serves it exactly the way a decoder does - `aircraft.json`,
`receiver.json` and `stats.json` over HTTP, `0x1a`-escaped Beast binary on TCP
30005, and an NDJSON recording on disk - so web UIs, alert rules, exporters and
tests built against it work unchanged against live traffic.

## Features

- **Full dump1090/readsb JSON surface**: `GET /data/aircraft.json` with
  `now` / `messages` / `aircraft[]`, `receiver.json`, and `stats.json` with the
  five `latest` / `last1min` / `last5min` / `last15min` / `total` buckets and
  their `local` subkeys (`blocks_processed`, `accepted[]`, `signal`,
  `peak_signal`, ...).
- **Coherent by construction**: `alt_baro` is an `int` or `"ground"`,
  `seen <= seen_pos`, per-aircraft `messages` sum to the document counter,
  stats buckets aggregate exactly the emitted frames, and `rssi` falls off with
  `dst` the way a real signal would.
- **Kinematically plausible physics**: constant-track segments, turns capped at
  3 deg/s, climbs and descents bounded by a per-type fpm table, ground phases
  with takeoff and landing, altitudes from the deck to FL410 and speeds from a
  20-type performance table (A320, B738, B77W, C172, ...).
- **Beast binary output** (`--beast 30005`): real frame structure, `0x1a`
  byte-stuffing, DF17-style payloads with a Mode S CRC that verifies.
- **NDJSON recording** (`--ndjson FILE`): one `{"t": ..., "aircraft": [...]}`
  line per step, the same convention `dump1090-replay` reads back.
- **Deterministic `--seed`**: same seed plus same step sequence reproduces
  byte-identical frames and documents; wall time never leaks into the output.
- **Zero runtime dependencies**: `argparse`, `http.server`, `socket`, `json`,
  `random` only. Fully typed (`py.typed`), 186 tests at 99% coverage.

## Install

```bash
pip install dump1090-simulator
```

Or from source:

```bash
git clone https://github.com/technothatch/dump1090-simulator.git
cd dump1090-simulator
python -m pip install -e ".[dev]"
```

Requires Python 3.11 or newer.

## Quickstart

Check the install:

```bash
$ dump1090-simulator --version
dump1090-simulator 0.1.0
```

Fly six aircraft around a receiver at 51.5 N, 0.12 W and serve the JSON
documents and the Beast feed:

```bash
$ dump1090-simulator --seed 42 --count 6 --lat 51.5 --lon -0.12 --http 8080 --beast 30005
```

In a second terminal, poll the same endpoints a dump1090 web UI uses:

```bash
$ curl -s http://127.0.0.1:8080/data/receiver.json
{"version": "dump1090-simulator 0.1.0", "refresh": 1000, "lat": 51.5, "lon": -0.12}
```

```bash
$ curl -s http://127.0.0.1:8080/data/aircraft.json | python -m json.tool | head -n 42
{
    "now": 1791333601.707,
    "messages": 10,
    "aircraft": [
        {
            "hex": "28df6e",
            "type": "A21N",
            "r": "N36049",
            "t": "A21N",
            "flight": "EZY1680 ",
            "alt_baro": 22041,
            "gs": 382.9,
            "track": 257.8,
            "track_rate": 0.0,
            "roll": 0.0,
            "true_heading": 257.8,
            "mag_heading": 252.8,
            "baro_rate": 2100,
            "geom_rate": 2100,
            "squawk": "1600",
            "emergency": "none",
            "category": "A2",
            "lat": 49.973594,
            "lon": -1.081053,
            "nic": 10,
            "rc": 75,
            "seen_pos": 2.0,
            "seen": 0.0,
            "messages": 2,
            "rssi": -25.1,
            "dst": 182.7,
            "dir": 202.1,
            "version": 2,
            "sil": 2,
            "sil_type": "per-sample",
            "nac_p": 9,
            "nic_baro": 1,
            "alt_geom": 22248,
            "ias": 265,
            "tas": 383,
            "mach": 0.628
        },
```

Read the Beast feed straight off TCP 30005 (marker `0x1a`, type byte `0x33`
for Mode AC/3, then the escaped body: 48-bit MLAT timestamp, signal byte,
14-byte DF17 payload starting with `0x8d` and the ICAO `28df6e` from the JSON
above):

```bash
$ python -c "import socket; s = socket.create_connection(('127.0.0.1', 30005)); print(' '.join(f'{b:02x}' for b in s.recv(48))); s.close()"
1a 33 00 00 00 4c 4b 40 0e 8d 28 df 6e 18 15 a6 61 9a 88 00 ec 75 c3 1a 33 00 00 00 4c 4b 40 0a 8d a2 bc 37 58 05 88 f3 77 9c 80 0f b7 f7 1a 33
```

Record five seconds of traffic to disk instead of serving it:

```bash
$ dump1090-simulator --seed 42 --count 4 --duration 5 --http 0 --beast 0 --ndjson demo.ndjson
$ python -c "import json; [print(json.loads(l)['t'], len(json.loads(l)['aircraft'])) for l in open('demo.ndjson')]"
1791402544.181 4
1791402545.181 4
1791402546.181 4
1791402547.181 4
1791402548.181 4
1791402549.181 4
$ head -c 180 demo.ndjson
{"t": 1791402544.181, "aircraft": [{"hex": "28df6e", "type": "A21N", "r": "N36049", "t": "A21N", "flight": "EZY1680 ", "alt_baro": 22041, "gs": 378.9, "track": 257.8, "track_rate":
```

Full flag set:

```text
$ dump1090-simulator --help
usage: dump1090-simulator [-h] [--seed SEED] [--count COUNT]
                          [--interval INTERVAL] [--lat LAT] [--lon LON]
                          [--http HTTP] [--beast BEAST] [--bind BIND]
                          [--ndjson PATH] [--duration DURATION] [--version]

Deterministic synthetic ADS-B traffic served as dump1090 does: JSON over HTTP,
Beast binary over TCP, NDJSON on disk.

options:
  -h, --help           show this help message and exit
  --seed SEED          RNG seed for a reproducible fleet (default: system
                       entropy)
  --count COUNT        number of aircraft to simulate (default: 12)
  --interval INTERVAL  seconds per step and base message period (default: 1.0)
  --lat LAT            receiver latitude in degrees (default: 0)
  --lon LON            receiver longitude in degrees (default: 0)
  --http HTTP          HTTP JSON port, 0 disables (default: 8080)
  --beast BEAST        Beast output port, 0 disables (default: 30005)
  --bind BIND          bind address for HTTP and Beast (default: 127.0.0.1)
  --ndjson PATH        record one NDJSON snapshot per step to PATH
  --duration DURATION  seconds to run, 0 runs until interrupted (default: 0)
  --version            show program's version number and exit
```

## Configuration reference

All configuration is by flag; the simulator deliberately reads no environment
variables or config files, so a command line is a complete, reproducible run.

| Flag | Meaning | Default |
|------|---------|---------|
| `--seed INT` | RNG seed; the same seed reproduces the same fleet, frames and documents | system entropy |
| `--count INT` | Number of simulated aircraft (0 gives an empty sky) | `12` |
| `--interval FLOAT` | Seconds per step and base state-message period (per-aircraft intervals are 1-3x this, position messages 2-3x that) | `1.0` |
| `--lat FLOAT` | Receiver latitude, degrees in `[-90, 90]`; sets `receiver.json` and every `dst`/`dir` | `0` |
| `--lon FLOAT` | Receiver longitude, degrees in `[-180, 180]` | `0` |
| `--http PORT` | HTTP JSON port; `0` disables the HTTP server | `8080` |
| `--beast PORT` | Beast binary output port; `0` disables it | `30005` |
| `--bind ADDR` | Bind address for HTTP and Beast (`127.0.0.1` local, `0.0.0.0` exposed) | `127.0.0.1` |
| `--ndjson PATH` | Append one NDJSON snapshot per step to `PATH` (file is truncated on open) | unset |
| `--duration FLOAT` | Seconds to run before exiting; `0` runs until Ctrl-C | `0.0` |
| `--version` | Print `dump1090-simulator <version>` and exit | - |

## How it works

`Simulation` owns the fleet and the only clock in the program:
`config.start` plus the `dt` values passed to `step()`. Each step integrates
every aircraft, emits whatever messages are due, and rebuilds the three JSON
documents under a lock; the HTTP server, Beast broadcaster and NDJSON recorder
only ever read those snapshots.

- `models.py` - `SimConfig`, `AircraftState`, the `Phase` machine and the
  20-type performance table (cruise speed, altitude band, climb/descent fpm,
  turn rate, rotation speed).
- `fleet.py` - spawns aircraft: unique 24-bit ICAO hexes, callsigns drawn from
  real airline designators, registrations, Mode A codes (occasionally 7700),
  positions scattered 5-300 km around the receiver, mixed across phases.
- `physics.py` - the integrator. Ground speed moves toward a phase target at a
  capped acceleration, track turns at no more than the type's turn rate,
  altitude changes at no more than the type's fpm, and positions advance with
  the haversine destination formula, so per-step distance is exactly `gs x dt`.
- `transponder.py` - DF17-style payloads: `0x8D` header, 24-bit ICAO, CPR
  position or IA5 callsign, and a Mode S CRC-24 a decoder will accept.
- `beast.py` - Beast framing: `0x1a` marker, type byte, `0x1a`-doubled body,
  plus an incremental parser that resynchronises after corruption.
- `documents.py` - pure builders for the three JSON documents, the RSSI model
  (logarithmic falloff, clamped to a decoder's dBFS range) and the stats
  buckets.
- `simulation.py` - message cadence, stats event windows and the coherence
  rules (`messages` totals, `seen <= seen_pos`, nested stats buckets).
- `server.py`, `beast_server.py`, `ndjson.py`, `cli.py` - the three outputs and
  the loop that drives them.

Determinism rules: one seeded `random.Random` drives spawn, physics and squawk
changes; message emission is a pure comparison of `now` against per-aircraft
intervals, so no timer or thread jitter can enter the output.

## Development

```bash
git clone https://github.com/technothatch/dump1090-simulator.git
cd dump1090-simulator
python -m pip install -e ".[dev]"
```

```bash
ruff check .                 # lint
ruff format --check .        # formatting
mypy src                     # strict type checking
python -m pytest -q --cov --cov-report=term-missing --cov-fail-under=80
```

The same four commands run in CI (`.github/workflows/ci.yml`) on Python 3.11,
3.12 and 3.13; `make lint`, `make test` and `make check` wrap them if you have
`make`.

Test layout: `tests/test_physics.py` hammers the integrator over 1200 steps
(no NaN, no teleports, bounded rates), `test_documents.py` pins the JSON
contract against the dump1090 field list, `test_beast.py` and
`test_transponder.py` prove the wire format and parity, `test_server.py` and
`test_beast_server.py` exercise the threaded servers on ephemeral ports, and
`test_cli.py` drives the real loop with injected clocks.

## Related projects

- [dump1090-fa](https://github.com/flightaware/dump1090) - the FlightAware
  Mode S decoder whose JSON shape this simulator reproduces.
- [readsb](https://github.com/wiedehopf/readsb) - the readsb decoder (same
  `/data/*.json` endpoints, Beast output on 30005).
- [tar1090](https://github.com/wiedehopf/tar1090) - a web UI that happily
  renders this simulator's `aircraft.json`.
- [graphs1090](https://github.com/wiedehopf/graphs1090) - statistics graphs
  fed by `stats.json` like the one this simulator serves.
- Siblings in this ecosystem: [dump1090-replay](https://github.com/technothatch/dump1090-replay)
  (replay a recorded capture), [dump1090-alerts](https://github.com/technothatch/dump1090-alerts)
  (alert rules over `aircraft.json`), [beast-relay](https://github.com/technothatch/beast-relay)
  (fan one Beast feed out to many consumers).

## License

MIT - see [LICENSE](LICENSE). Copyright (c) 2026 technothatch.

---

## Related projects

Part of the [technothatch ADS-B toolkit](https://github.com/technothatch/technothatch#readme) - the full index with one-liners for every tool lives there.

- **Run & operate:** [dump1090-stack](https://github.com/technothatch/dump1090-stack) - full Compose stack; [dump1090-healthcheck](https://github.com/technothatch/dump1090-healthcheck) - decoder watchdog; [dump1090-alerts](https://github.com/technothatch/dump1090-alerts) - rule-based alerting
- **Metrics & data:** [dump1090-exporter](https://github.com/technothatch/dump1090-exporter) - Prometheus; [dump1090-influx](https://github.com/technothatch/dump1090-influx) - InfluxDB/Telegraf; [dump1090-grafana](https://github.com/technothatch/dump1090-grafana) - dashboards; [dump1090-recorder](https://github.com/technothatch/dump1090-recorder) - SQLite history; [dump1090-coverage](https://github.com/technothatch/dump1090-coverage) - heatmaps & reports
- **Feeds & networking:** [beast-relay](https://github.com/technothatch/beast-relay) - Beast fan-out; [dump1090-geofence](https://github.com/technothatch/dump1090-geofence) - region-filtered feeds; [dump1090-to-mqtt](https://github.com/technothatch/dump1090-to-mqtt) - MQTT/Home Assistant
- **Interfaces:** [dump1090-dashboard](https://github.com/technothatch/dump1090-dashboard) - web UI; [adsb-tui](https://github.com/technothatch/adsb-tui) - terminal radar; [dump1090-cli](https://github.com/technothatch/dump1090-cli) - aircraft.json CLI; [dump1090-sdk](https://github.com/technothatch/dump1090-sdk) - TypeScript client
- **Testing & decoding:** [dump1090-simulator](https://github.com/technothatch/dump1090-simulator) - synthetic traffic; [dump1090-replay](https://github.com/technothatch/dump1090-replay) - recorded history replay; [squawk-decoder](https://github.com/technothatch/squawk-decoder) - squawk/emitter/ICAO decoder
