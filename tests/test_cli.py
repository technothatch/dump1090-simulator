"""CLI flags, the main loop with injected clocks, and failure handling."""

from __future__ import annotations

import http.client
import json
import socket
import time
from collections.abc import Sequence
from pathlib import Path

import pytest

from conftest import START, free_port
from dump1090_simulator import __version__
from dump1090_simulator.beast import FrameParser
from dump1090_simulator.cli import build_parser, main


class FakeClock:
    """Sleep-driven monotonic clock so ``main`` runs its loop without waiting."""

    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def mono(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


def _run(argv: Sequence[str], clock: FakeClock, *, start: float = START) -> int:
    return main(argv, sleep=clock.sleep, mono=clock.mono, now=lambda: start)


def test_defaults_match_the_dump1090_conventions() -> None:
    args = build_parser().parse_args([])
    assert args.seed is None
    assert args.count == 12
    assert args.interval == 1.0
    assert args.lat == 0.0
    assert args.lon == 0.0
    assert args.http == 8080
    assert args.beast == 30005
    assert args.bind == "127.0.0.1"
    assert args.ndjson is None
    assert args.duration == 0.0


def test_help_lists_every_headline_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--help"])
    assert excinfo.value.code == 0
    output = capsys.readouterr().out
    for flag in (
        "--seed",
        "--count",
        "--interval",
        "--lat",
        "--lon",
        "--http",
        "--beast",
        "--bind",
        "--ndjson",
        "--duration",
        "--version",
    ):
        assert flag in output
    assert "dump1090-simulator" in output


def test_version_prints_the_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--version"])
    assert excinfo.value.code == 0
    assert capsys.readouterr().out.strip() == f"dump1090-simulator {__version__}"


@pytest.mark.parametrize(
    "argv",
    [
        ["--count", "-1"],
        ["--interval", "0"],
        ["--interval", "-2"],
        ["--lat", "91"],
        ["--lat", "-91"],
        ["--lon", "181"],
        ["--http", "65536"],
        ["--beast", "-1"],
        ["--duration", "-1"],
    ],
)
def test_invalid_flags_abort_with_an_argparse_error(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(argv)
    assert excinfo.value.code == 2


def test_boundary_values_are_accepted() -> None:
    args = build_parser().parse_args(
        [
            "--count",
            "0",
            "--lat",
            "51.5",
            "--lon",
            "-0.12",
            "--http",
            "0",
            "--beast",
            "0",
            "--bind",
            "0.0.0.0",
            "--duration",
            "2.5",
        ]
    )
    assert args.count == 0
    assert args.lat == 51.5
    assert args.lon == -0.12
    assert args.http == 0
    assert args.beast == 0
    assert args.bind == "0.0.0.0"
    assert args.duration == 2.5


def test_ndjson_run_records_one_snapshot_per_step(tmp_path: Path) -> None:
    path = tmp_path / "capture.ndjson"
    clock = FakeClock()
    code = _run(
        [
            "--seed",
            "42",
            "--count",
            "4",
            "--interval",
            "1",
            "--duration",
            "3",
            "--http",
            "0",
            "--beast",
            "0",
            "--ndjson",
            str(path),
        ],
        clock,
    )
    assert code == 0
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    assert [json.loads(line)["t"] for line in lines] == [
        START + 1,
        START + 2,
        START + 3,
        START + 4,
    ]
    for line in lines:
        payload = json.loads(line)
        assert set(payload) == {"t", "aircraft"}
        assert len(payload["aircraft"]) == 4
    assert clock.sleeps == [1.0, 1.0, 1.0]


def test_count_zero_runs_an_empty_sky(tmp_path: Path) -> None:
    path = tmp_path / "empty.ndjson"
    clock = FakeClock()
    code = _run(
        [
            "--seed",
            "3",
            "--count",
            "0",
            "--duration",
            "1",
            "--http",
            "0",
            "--beast",
            "0",
            "--ndjson",
            str(path),
        ],
        clock,
    )
    assert code == 0
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines
    assert all(json.loads(line)["aircraft"] == [] for line in lines)


def test_http_is_served_while_the_loop_runs() -> None:
    port = free_port()
    clock = FakeClock()
    seen: dict[str, object] = {}

    def sleep(seconds: float) -> None:
        if "status" not in seen:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                conn.request("GET", "/data/aircraft.json")
                response = conn.getresponse()
                seen["status"] = response.status
                seen["doc"] = json.loads(response.read())
            finally:
                conn.close()
        clock.sleep(seconds)

    code = main(
        [
            "--seed",
            "7",
            "--count",
            "3",
            "--interval",
            "1",
            "--duration",
            "2",
            "--http",
            str(port),
            "--beast",
            "0",
        ],
        sleep=sleep,
        mono=clock.mono,
        now=lambda: START,
    )
    assert code == 0
    assert seen["status"] == 200
    doc = seen["doc"]
    assert isinstance(doc, dict)
    assert set(doc) == {"now", "messages", "aircraft"}
    assert doc["now"] > START
    assert len(doc["aircraft"]) == 3


def test_beast_port_delivers_frames_to_a_tcp_consumer() -> None:
    port = free_port()
    clock = FakeClock()
    received = bytearray()
    conn: socket.socket | None = None

    def sleep(seconds: float) -> None:
        nonlocal conn
        if conn is None:
            conn = socket.create_connection(("127.0.0.1", port), timeout=5)
            conn.settimeout(0.5)
            time.sleep(0.05)  # give the accept loop time to register us
        else:
            try:
                chunk = conn.recv(4096)
            except TimeoutError:
                chunk = b""
            received.extend(chunk)
        clock.sleep(seconds)

    try:
        code = main(
            [
                "--seed",
                "11",
                "--count",
                "3",
                "--interval",
                "1",
                "--duration",
                "3",
                "--http",
                "0",
                "--beast",
                str(port),
            ],
            sleep=sleep,
            mono=clock.mono,
            now=lambda: START,
        )
        assert conn is not None
        conn.settimeout(2.0)
        try:
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                received.extend(chunk)
        except TimeoutError:
            pass
    finally:
        if conn is not None:
            conn.close()
    assert code == 0
    assert received, "no beast bytes reached the consumer"
    frames = FrameParser().feed(bytes(received))
    assert frames
    assert all(len(frame.payload) == 14 for frame in frames)


def test_keyboard_interrupt_exits_with_130() -> None:
    clock = FakeClock()

    def sleep(_seconds: float) -> None:
        raise KeyboardInterrupt

    code = main(
        ["--seed", "1", "--duration", "5", "--http", "0", "--beast", "0"],
        sleep=sleep,
        mono=clock.mono,
        now=lambda: START,
    )
    assert code == 130


def test_startup_failure_reports_and_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = FakeClock()
    missing = tmp_path / "does-not-exist" / "capture.ndjson"
    code = _run(
        [
            "--seed",
            "1",
            "--duration",
            "1",
            "--http",
            "0",
            "--beast",
            "0",
            "--ndjson",
            str(missing),
        ],
        clock,
    )
    assert code == 1
    assert "dump1090-simulator:" in capsys.readouterr().err
