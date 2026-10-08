"""Beast binary framing: byte-stuffing, wire encoding and stream parsing."""

from __future__ import annotations

import pytest

from dump1090_simulator.beast import (
    MARKER,
    PAYLOAD_LENGTHS,
    BeastError,
    Frame,
    FrameParser,
    encode_frame,
    escape,
    signal_byte,
    unescape,
)


def _assert_well_formed(wire: bytes, msg_type: str) -> None:
    """Every ``0x1a`` after the two-byte header must be doubled."""
    assert wire[0] == MARKER
    assert chr(wire[1]) == msg_type
    body = wire[2:]
    index = 0
    while index < len(body):
        if body[index] == MARKER:
            assert index + 1 < len(body), "lone marker inside the body"
            assert body[index + 1] == MARKER, "marker inside the body must be doubled"
            index += 2
        else:
            index += 1


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"\x00",
        b"\x1a",
        b"\x1a\x1a",
        b"header\x1apayload\x1a",
        bytes(range(256)),
    ],
)
def test_escape_unescape_round_trip(payload: bytes) -> None:
    wire = escape(payload)
    assert unescape(wire) == payload


def test_escape_doubles_every_marker() -> None:
    assert escape(b"\x1a") == b"\x1a\x1a"
    assert escape(b"a\x1ab\x1a\x1ac") == b"a\x1a\x1ab\x1a\x1a\x1a\x1ac"
    assert escape(b"no markers") == b"no markers"


def test_unescape_rejects_a_bare_marker() -> None:
    with pytest.raises(BeastError, match="not doubled"):
        unescape(b"\x1a\x33")
    with pytest.raises(BeastError, match="not doubled"):
        unescape(b"abc\x1a")
    with pytest.raises(BeastError, match="not doubled"):
        unescape(b"\x1a\x1a\x1a")


def test_unescape_of_empty_input_is_empty() -> None:
    assert unescape(b"") == b""


@pytest.mark.parametrize("msg_type", sorted(PAYLOAD_LENGTHS))
def test_encode_frame_escapes_marker_heavy_bodies(msg_type: str) -> None:
    length = PAYLOAD_LENGTHS[msg_type]
    mlat = 0x1A1A1A1A1A1A
    signal = 0x1A
    payload = bytes([0x1A] * length)
    wire = encode_frame(Frame(msg_type=msg_type, mlat=mlat, signal=signal, payload=payload))
    _assert_well_formed(wire, msg_type)
    body = (mlat).to_bytes(6, "big") + bytes([signal]) + payload
    assert unescape(wire[2:]) == body


@pytest.mark.parametrize("msg_type", sorted(PAYLOAD_LENGTHS))
def test_encode_then_parse_round_trip(msg_type: str) -> None:
    frame = Frame(
        msg_type=msg_type,
        mlat=123_456_789,
        signal=200,
        payload=bytes(range(PAYLOAD_LENGTHS[msg_type])),
    )
    parser = FrameParser()
    assert parser.feed(encode_frame(frame)) == [frame]
    assert parser.feed(b"") == []


def test_parser_reassembles_an_arbitrarily_chunked_stream() -> None:
    frames = [Frame(msg_type="3", mlat=i, signal=50 + i, payload=bytes([i]) * 14) for i in range(5)]
    wire = b"".join(encode_frame(frame) for frame in frames)
    parser = FrameParser()
    received: list[Frame] = []
    for offset in range(0, len(wire), 3):
        received.extend(parser.feed(wire[offset : offset + 3]))
    assert received == frames


def test_parser_walks_a_marker_heavy_frame_one_byte_at_a_time() -> None:
    frame = Frame(msg_type="1", mlat=0x1A1A, signal=0x1A, payload=b"\x1a\x1a")
    parser = FrameParser()
    received: list[Frame] = []
    for byte in encode_frame(frame):
        received.extend(parser.feed(bytes([byte])))
    assert received == [frame]


def test_parser_skips_leading_garbage() -> None:
    frame = Frame(msg_type="1", mlat=42, signal=7, payload=b"\x01\x02")
    parser = FrameParser()
    assert parser.feed(b"junk without any marker" + encode_frame(frame)) == [frame]
    assert parser.feed(b"more junk, still no marker") == []


def test_parser_drops_unknown_message_types() -> None:
    good = Frame(msg_type="2", mlat=1, signal=2, payload=b"\x00" * 7)
    parser = FrameParser()
    bogus = b"\x1a9" + b"\x00" * 25
    assert parser.feed(bogus + encode_frame(good)) == [good]


def test_parser_resyncs_after_a_bare_marker_in_a_body() -> None:
    good = Frame(msg_type="3", mlat=7, signal=8, payload=b"\x22" * 14)
    corrupt = b"\x1a3" + b"\x11" * 5 + b"\x1a\x99"
    parser = FrameParser()
    assert parser.feed(corrupt + encode_frame(good)) == [good]


def test_parser_waits_for_the_rest_of_a_partial_frame() -> None:
    frame = Frame(msg_type="3", mlat=9, signal=9, payload=b"\x33" * 14)
    wire = encode_frame(frame)
    parser = FrameParser()
    assert parser.feed(wire[:10]) == []
    assert parser.feed(wire[10:]) == [frame]


@pytest.mark.parametrize(
    ("frame", "match"),
    [
        (Frame(msg_type="9", mlat=0, signal=0, payload=b""), "unknown message type"),
        (Frame(msg_type="1", mlat=0, signal=0, payload=b"\x00"), "needs 2 payload bytes"),
        (Frame(msg_type="3", mlat=0, signal=0, payload=b"\x00"), "needs 14 payload bytes"),
        (Frame(msg_type="3", mlat=0, signal=256, payload=b"\x00" * 14), "signal byte out of range"),
        (Frame(msg_type="3", mlat=0, signal=-1, payload=b"\x00" * 14), "signal byte out of range"),
        (
            Frame(msg_type="3", mlat=1 << 48, signal=0, payload=b"\x00" * 14),
            "48-bit range",
        ),
        (Frame(msg_type="3", mlat=-1, signal=0, payload=b"\x00" * 14), "48-bit range"),
    ],
)
def test_encode_frame_rejects_malformed_frames(frame: Frame, match: str) -> None:
    with pytest.raises(BeastError, match=match):
        encode_frame(frame)


def test_signal_byte_stays_inside_the_byte_range() -> None:
    for rssi in (-45.0, -30.0, -20.0, -10.0, -6.0, -3.0, -1.0, 0.0):
        assert 1 <= signal_byte(rssi) <= 255


def test_signal_byte_rises_with_signal_strength() -> None:
    values = [signal_byte(rssi) for rssi in (-40.0, -30.0, -20.0, -10.0, -5.0, -1.0)]
    assert values == sorted(values)
    assert len(set(values)) == len(values)


def test_signal_byte_never_reports_zero_or_overflow() -> None:
    assert signal_byte(-1000.0) == 1
    assert signal_byte(50.0) == 255
