"""Command line entry point: flags in, a running feed out.

The CLI wires the pieces together and owns the wall clock:

- builds a :class:`~dump1090_simulator.models.Simulation` from the flags;
- optionally starts the HTTP JSON server and the Beast broadcaster;
- optionally records NDJSON snapshots;
- steps the simulation on a fixed interval until ``--duration`` elapses
  (or forever, until Ctrl-C).

All three clocks (wall time for ``receiver``/``now``, the sleep function,
the monotonic deadline) are injectable, so the test suite exercises the
real loop with no real waiting.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable, Sequence

from dump1090_simulator import __version__
from dump1090_simulator.beast_server import BeastBroadcaster
from dump1090_simulator.models import SimConfig
from dump1090_simulator.ndjson import NdjsonRecorder
from dump1090_simulator.server import HttpServer
from dump1090_simulator.simulation import Simulation

__all__ = ["build_parser", "main"]

_DEFAULT_COUNT = 12
_DEFAULT_INTERVAL = 1.0
_DEFAULT_HTTP_PORT = 8080
_DEFAULT_BEAST_PORT = 30005


def _positive_float(text: str) -> float:
    value = float(text)
    if value <= 0.0:
        raise argparse.ArgumentTypeError(f"must be > 0, got {text}")
    return value


def _nonnegative_float(text: str) -> float:
    value = float(text)
    if value < 0.0:
        raise argparse.ArgumentTypeError(f"must be >= 0, got {text}")
    return value


def _nonnegative_int(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0, got {text}")
    return value


def _latitude(text: str) -> float:
    value = float(text)
    if not -90.0 <= value <= 90.0:
        raise argparse.ArgumentTypeError(f"latitude out of range [-90, 90]: {text}")
    return value


def _longitude(text: str) -> float:
    value = float(text)
    if not -180.0 <= value <= 180.0:
        raise argparse.ArgumentTypeError(f"longitude out of range [-180, 180]: {text}")
    return value


def _port(text: str) -> int:
    value = int(text)
    if not 0 <= value <= 65535:
        raise argparse.ArgumentTypeError(f"port out of range [0, 65535]: {text}")
    return value


def build_parser() -> argparse.ArgumentParser:
    """The full flag set (also exercised directly by the tests)."""
    parser = argparse.ArgumentParser(
        prog="dump1090-simulator",
        description="Deterministic synthetic ADS-B traffic served as dump1090 does: "
        "JSON over HTTP, Beast binary over TCP, NDJSON on disk.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="RNG seed for a reproducible fleet (default: system entropy)",
    )
    parser.add_argument(
        "--count",
        type=_nonnegative_int,
        default=_DEFAULT_COUNT,
        help=f"number of aircraft to simulate (default: {_DEFAULT_COUNT})",
    )
    parser.add_argument(
        "--interval",
        type=_positive_float,
        default=_DEFAULT_INTERVAL,
        help=f"seconds per step and base message period (default: {_DEFAULT_INTERVAL})",
    )
    parser.add_argument(
        "--lat",
        type=_latitude,
        default=0.0,
        help="receiver latitude in degrees (default: 0)",
    )
    parser.add_argument(
        "--lon",
        type=_longitude,
        default=0.0,
        help="receiver longitude in degrees (default: 0)",
    )
    parser.add_argument(
        "--http",
        type=_port,
        default=_DEFAULT_HTTP_PORT,
        help=f"HTTP JSON port, 0 disables (default: {_DEFAULT_HTTP_PORT})",
    )
    parser.add_argument(
        "--beast",
        type=_port,
        default=_DEFAULT_BEAST_PORT,
        help=f"Beast output port, 0 disables (default: {_DEFAULT_BEAST_PORT})",
    )
    parser.add_argument(
        "--bind",
        default="127.0.0.1",
        help="bind address for HTTP and Beast (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--ndjson",
        default=None,
        metavar="PATH",
        help="record one NDJSON snapshot per step to PATH",
    )
    parser.add_argument(
        "--duration",
        type=_nonnegative_float,
        default=0.0,
        help="seconds to run, 0 runs until interrupted (default: 0)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    sleep: Callable[[float], None] = time.sleep,
    mono: Callable[[], float] = time.monotonic,
    now: Callable[[], float] = time.time,
) -> int:
    """Run the simulator until ``--duration`` elapses or Ctrl-C.

    Args:
        argv: Argument vector (defaults to ``sys.argv[1:]``).
        sleep: Wait function; tests inject a fake to run instantly.
        mono: Monotonic clock for the duration deadline (tests inject).
        now: Wall clock that seeds the simulation's ``now`` (tests inject).

    Returns:
        Process exit code: 0 on normal completion, 130 on Ctrl-C, 1 on a
        startup failure such as a busy port.
    """
    args = build_parser().parse_args(argv)

    sim = Simulation(
        SimConfig(
            seed=args.seed,
            count=args.count,
            interval=args.interval,
            lat=args.lat,
            lon=args.lon,
            start=now(),
        )
    )
    http: HttpServer | None = None
    beast: BeastBroadcaster | None = None
    recorder: NdjsonRecorder | None = None
    exit_code = 0
    try:
        if args.http != 0:
            http = HttpServer(sim, host=args.bind, port=args.http)
            http.start()
        if args.beast != 0:
            beast = BeastBroadcaster(host=args.bind, port=args.beast)
            beast.start()
        if args.ndjson is not None:
            recorder = NdjsonRecorder(args.ndjson)

        deadline = mono() + args.duration if args.duration > 0.0 else None
        while True:
            frames = sim.step(args.interval)
            if beast is not None:
                beast.publish(frames)
            if recorder is not None:
                doc = sim.aircraft_doc()
                recorder.record(doc["now"], doc["aircraft"])
            if deadline is not None and mono() >= deadline:
                break
            sleep(args.interval)
    except KeyboardInterrupt:
        exit_code = 130
    except OSError as exc:
        print(f"dump1090-simulator: {exc}", file=sys.stderr)
        exit_code = 1
    finally:
        if recorder is not None:
            recorder.close()
        if http is not None:
            http.close()
        if beast is not None:
            beast.close()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
