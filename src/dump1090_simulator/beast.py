"""Beast binary framing — the wire protocol dump1090 speaks on TCP 30005.

A frame is ``0x1a`` + type byte + body, where the body is a 48-bit big-endian
MLAT timestamp, one signal byte and the message payload (2, 7 or 14 bytes for
types ``1``/``2``/``3``). Every ``0x1a`` byte in the body is doubled on the
wire, so a payload byte can never be mistaken for the frame marker — and a
decoder that sees a lone ``0x1a`` knows the stream is corrupt.

:func:`encode_frame` produces wire bytes, :func:`unescape` inverts the byte
stuffing, and :class:`FrameParser` reassembles frames from an arbitrarily
chunked stream, resynchronising after garbage.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = [
    "MARKER",
    "PAYLOAD_LENGTHS",
    "BeastError",
    "Frame",
    "FrameParser",
    "encode_frame",
    "escape",
    "signal_byte",
    "unescape",
]

#: Frame marker byte; every frame starts with it and doubles it in the body.
MARKER = 0x1A

#: Frame type byte -> payload length, as emitted by dump1090-fa and readsb.
PAYLOAD_LENGTHS: dict[str, int] = {"1": 2, "2": 7, "3": 14}

_MLAT_BYTES = 6
_MLAT_MAX = (1 << 48) - 1


class BeastError(ValueError):
    """A frame could not be encoded or decoded."""


def escape(data: bytes) -> bytes:
    """Double every ``0x1a`` byte in ``data`` (the wire byte-stuffing)."""
    return data.replace(b"\x1a", b"\x1a\x1a")


def unescape(data: bytes) -> bytes:
    """Collapse doubled ``0x1a`` bytes; reject a bare (corrupt) marker.

    Raises:
        BeastError: A ``0x1a`` appears that is not followed by another.
    """
    out = bytearray()
    index = 0
    while index < len(data):
        byte = data[index]
        if byte == MARKER:
            if index + 1 >= len(data) or data[index + 1] != MARKER:
                raise BeastError("0x1a is not doubled")
            index += 1
        out.append(byte)
        index += 1
    return bytes(out)


@dataclass(frozen=True, slots=True)
class Frame:
    """One decoded Beast frame."""

    msg_type: str
    mlat: int
    signal: int
    payload: bytes


def signal_byte(rssi: float) -> int:
    """Encode a dBFS signal level as the Beast signal byte.

    Decoders read it back as linear power ``(byte / 255) ** 2``; zero means
    "no signal information", so strong signals map high and the floor is 1.
    """
    amplitude = 255.0 * math.pow(10.0, rssi / 20.0)
    return max(1, min(255, round(amplitude)))


def encode_frame(frame: Frame) -> bytes:
    """Render a frame as escaped wire bytes (marker included).

    Raises:
        BeastError: Unknown type byte, wrong payload length, out-of-range
            signal byte or an MLAT timestamp that does not fit 48 bits.
    """
    length = PAYLOAD_LENGTHS.get(frame.msg_type)
    if length is None:
        raise BeastError(f"unknown message type {frame.msg_type!r}")
    if len(frame.payload) != length:
        raise BeastError(
            f"type {frame.msg_type!r} needs {length} payload bytes, got {len(frame.payload)}"
        )
    if not 0 <= frame.signal <= 255:
        raise BeastError(f"signal byte out of range: {frame.signal}")
    if not 0 <= frame.mlat <= _MLAT_MAX:
        raise BeastError(f"MLAT timestamp out of 48-bit range: {frame.mlat}")
    body = frame.mlat.to_bytes(_MLAT_BYTES, "big") + bytes([frame.signal]) + frame.payload
    return bytes([MARKER]) + frame.msg_type.encode("ascii") + escape(body)


@dataclass(frozen=True, slots=True)
class _Body:
    status: str  # "ok" | "incomplete" | "bare"
    data: bytes
    consumed: int


def _decode_body(buf: bytearray, need: int) -> _Body:
    """Unescape body bytes until ``need`` decoded bytes are available."""
    out = bytearray()
    index = 0
    while len(out) < need:
        if index >= len(buf):
            return _Body("incomplete", b"", 0)
        byte = buf[index]
        if byte == MARKER:
            if index + 1 >= len(buf):
                return _Body("incomplete", b"", 0)
            if buf[index + 1] != MARKER:
                return _Body("bare", b"", 0)
            index += 1
        out.append(byte)
        index += 1
    return _Body("ok", bytes(out), index)


class FrameParser:
    """Incremental parser for a chunked Beast byte stream.

    Feed it whatever ``recv`` returned; it returns every complete frame found
    so far and keeps the remainder. On corruption it drops bytes until the
    next plausible marker rather than raising, which is what a consumer of a
    live radio link needs.
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[Frame]:
        """Consume ``data`` and return the frames completed by it."""
        self._buf.extend(data)
        frames: list[Frame] = []
        while True:
            marker = self._buf.find(MARKER)
            if marker < 0:
                del self._buf[:]
                break
            if marker > 0:
                del self._buf[:marker]
            if len(self._buf) < 2:
                break
            msg_type = chr(self._buf[1])
            length = PAYLOAD_LENGTHS.get(msg_type)
            if length is None:
                del self._buf[0]
                continue
            body = _decode_body(self._buf[2:], _MLAT_BYTES + 1 + length)
            if body.status == "incomplete":
                break
            if body.status == "bare":
                del self._buf[0]
                continue
            frames.append(
                Frame(
                    msg_type=msg_type,
                    mlat=int.from_bytes(body.data[:_MLAT_BYTES], "big"),
                    signal=body.data[_MLAT_BYTES],
                    payload=body.data[_MLAT_BYTES + 1 :],
                )
            )
            del self._buf[: 2 + body.consumed]
        return frames
