"""Beast broadcast: fan-out to TCP consumers on an ephemeral port."""

from __future__ import annotations

import socket
import time
from collections.abc import Iterator

import pytest

from dump1090_simulator.beast import Frame, FrameParser, encode_frame
from dump1090_simulator.beast_server import BeastBroadcaster


@pytest.fixture
def broadcaster() -> Iterator[BeastBroadcaster]:
    server = BeastBroadcaster(port=0)
    server.start()
    try:
        yield server
    finally:
        server.close()


def _connect(server: BeastBroadcaster, clients: int = 1) -> socket.socket:
    conn = socket.create_connection(("127.0.0.1", server.port), timeout=5)
    deadline = time.monotonic() + 5.0
    while server.client_count < clients:
        assert time.monotonic() < deadline, "accept loop never registered the consumer"
        time.sleep(0.01)
    return conn


def _receive(conn: socket.socket, size: int) -> bytes:
    conn.settimeout(5.0)
    chunks = bytearray()
    while len(chunks) < size:
        chunk = conn.recv(size - len(chunks))
        assert chunk, "connection closed before the batch arrived"
        chunks.extend(chunk)
    return bytes(chunks)


def test_port_is_an_ephemeral_listener(broadcaster: BeastBroadcaster) -> None:
    assert broadcaster.port > 0
    assert broadcaster.client_count == 0


def test_publish_fans_frames_out_to_a_consumer(broadcaster: BeastBroadcaster) -> None:
    frames = [
        encode_frame(
            Frame(msg_type="3", mlat=index, signal=100 + index, payload=bytes([index]) * 14)
        )
        for index in range(3)
    ]
    total = sum(len(frame) for frame in frames)
    with _connect(broadcaster) as conn:
        broadcaster.publish(frames)
        received = _receive(conn, total)
    parser = FrameParser()
    parsed = parser.feed(received)
    assert [frame.mlat for frame in parsed] == [0, 1, 2]
    assert all(frame.signal == 100 + index for index, frame in enumerate(parsed))


def test_two_consumers_receive_the_same_batch(broadcaster: BeastBroadcaster) -> None:
    frame = encode_frame(Frame(msg_type="3", mlat=42, signal=200, payload=b"\x01" * 14))
    first = _connect(broadcaster, clients=1)
    second = _connect(broadcaster, clients=2)
    try:
        assert broadcaster.client_count == 2
        broadcaster.publish([frame])
        assert _receive(first, len(frame)) == frame
        assert _receive(second, len(frame)) == frame
    finally:
        first.close()
        second.close()


def test_publish_without_consumers_is_a_noop(broadcaster: BeastBroadcaster) -> None:
    broadcaster.publish([])
    broadcaster.publish([b"\x1a3anything"])
    assert broadcaster.client_count == 0


def test_frames_published_before_a_consumer_arrives_are_dropped(
    broadcaster: BeastBroadcaster,
) -> None:
    broadcaster.publish([b"lost"])
    conn = _connect(broadcaster)
    try:
        broadcaster.publish([b"kept"])
        assert _receive(conn, len(b"kept")) == b"kept"
    finally:
        conn.close()


def test_close_is_idempotent_and_disconnects_consumers() -> None:
    server = BeastBroadcaster(port=0)
    server.start()
    conn = _connect(server)
    server.close()
    assert server.client_count == 0
    conn.settimeout(5.0)
    assert conn.recv(1024) == b""
    conn.close()
    server.close()


def test_a_consumer_that_disconnects_is_forgotten(broadcaster: BeastBroadcaster) -> None:
    conn = _connect(broadcaster)
    conn.close()
    deadline = time.monotonic() + 5.0
    while broadcaster.client_count > 0:
        assert time.monotonic() < deadline, "disconnected consumer was never unregistered"
        broadcaster.publish([b"\x1a3" + b"\x00" * 40])
        time.sleep(0.02)
    assert broadcaster.client_count == 0
