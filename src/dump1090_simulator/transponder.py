"""DF17-style Mode S payload construction (structure, not content).

The Beast output port needs *something* to put in a 112-bit Mode S frame.
This module builds a structurally valid DF17 extended squitter: the ``0x8D``
DF/CA header, the 24-bit ICAO address, a 56-bit airborne-position ME field
(type code 11 with Q-encoded barometric altitude and an even/odd CPR
position), and a correct 24-bit Mode S parity so a decoder that checks CRC
accepts the frame.

The exact decoded *content* is out of scope for the simulator — what must be
right is the framing, the escaping (see :mod:`dump1090_simulator.beast`) and
a parity that verifies, and the tests assert exactly that.
"""

from __future__ import annotations

import math

__all__ = [
    "airborne_position_me",
    "cpr_encode",
    "crc24",
    "df17_identity_payload",
    "df17_payload",
    "encode_altitude",
    "identity_me",
    "syndrome",
]

#: Mode S generator polynomial, x^24 + ... + 1 (dump1090's crc.c).
_CRC_GENERATOR = 0x1FFF409

#: Number of longitude zones for airborne CPR (Nz).
_CPR_NZ = 15

#: CPR quantization: 2 ** 17 bins per zone.
_CPR_BINS = 1 << 17

#: DF17 with capability field 5 ("airborne, no TS").
_DF17_CA5 = 0x8D

#: Airborne aircraft category A0..A3 -> type code of an identification
#: message (dump1090 publishes the category from these type codes).
_IDENTITY_TC: dict[str, int] = {"A0": 1, "A1": 2, "A2": 3, "A3": 4}


def _crc_table() -> tuple[int, ...]:
    rows = []
    for value in range(256):
        crc = value << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= _CRC_GENERATOR
        rows.append(crc & 0xFFFFFF)
    return tuple(rows)


_CRC_ROWS = _crc_table()


def crc24(data: bytes) -> int:
    """CRC-24 of ``data`` using the Mode S generator polynomial."""
    crc = 0
    for byte in data:
        crc = ((crc << 8) ^ _CRC_ROWS[byte ^ (crc >> 16)]) & 0xFFFFFF
    return crc


def syndrome(message: bytes) -> int:
    """Mode S syndrome of a full message; zero when parity is intact."""
    if len(message) < 3:
        return 0
    body, tail = message[:-3], message[-3:]
    return crc24(body) ^ int.from_bytes(tail, "big")


def encode_altitude(alt_ft: float) -> int:
    """Q-encode a barometric altitude into the 12-bit ADS-B altitude field.

    The field carries an 11-bit ``25 ft - 1000`` value with a ``Q = 1`` bit
    inserted at position 8, so decoders recover
    ``25 * bits - 1000`` feet.
    """
    value = int(round(alt_ft) + 1000) // 25
    value = max(0, min(0x7FF, value))
    return ((value >> 4) << 5) | (1 << 4) | (value & 0xF)


def _cpr_nz(lat_deg: float) -> int:
    """Number of longitude zones at ``lat_deg`` (CPR ``NL`` function)."""
    if abs(lat_deg) >= 87.0:
        return 1
    cos_lat = math.cos(math.radians(lat_deg))
    arg = 1.0 - (1.0 - math.cos(math.pi / (2.0 * _CPR_NZ))) / (cos_lat * cos_lat)
    zones = math.floor(2.0 * math.pi / math.acos(max(-1.0, min(1.0, arg))))
    return max(1, min(59, zones))


def cpr_encode(lat_deg: float, lon_deg: float, odd: bool) -> tuple[int, int]:
    """Encode a position into the 17-bit CPR latitude/longitude pair."""
    index = 1 if odd else 0
    dlat = 360.0 / (60 - index)
    lat_zone = lat_deg % dlat
    lat_cpr = int((lat_zone / dlat) * _CPR_BINS) & (_CPR_BINS - 1)

    zones = _cpr_nz(lat_deg) - index
    if zones < 1:
        zones = 1
    dlon = 360.0 / zones
    lon_zone = lon_deg % dlon
    lon_cpr = int((lon_zone / dlon) * _CPR_BINS) & (_CPR_BINS - 1)
    return lat_cpr, lon_cpr


def airborne_position_me(alt_ft: float, lat_deg: float, lon_deg: float, odd: bool) -> bytes:
    """The 56-bit ME field of a type-code-11 airborne position message."""
    tc = 11
    ss = 0
    saf = 0
    altitude = encode_altitude(alt_ft)
    time_bit = 1
    cpr_format = 1 if odd else 0
    lat_cpr, lon_cpr = cpr_encode(lat_deg, lon_deg, odd)
    me = (
        (tc << 51)
        | (ss << 49)
        | (saf << 48)
        | (altitude << 36)
        | (time_bit << 35)
        | (cpr_format << 34)
        | (lat_cpr << 17)
        | lon_cpr
    )
    return me.to_bytes(7, "big")


def df17_payload(hex_icao: str, alt_ft: float, lat_deg: float, lon_deg: float, odd: bool) -> bytes:
    """A 14-byte DF17 airborne-position message with valid parity."""
    icao = int(hex_icao, 16)
    head = (
        bytes([_DF17_CA5])
        + icao.to_bytes(3, "big")
        + airborne_position_me(alt_ft, lat_deg, lon_deg, odd)
    )
    return head + crc24(head).to_bytes(3, "big")


def _ia5_char(ch: str) -> int:
    """One character of the ADS-B callsign alphabet (6 bits).

    ``A``-``Z`` map to 1-26, ``0``-``9`` to 32-41 and everything else
    (spaces, the hyphen in registrations, lower case) collapses to blank.
    """
    if "A" <= ch <= "Z":
        return ord(ch) - ord("A") + 1
    if "0" <= ch <= "9":
        return ord(ch) - ord("0") + 32
    return 0


def identity_me(category: str, callsign: str) -> bytes:
    """The 56-bit ME field of an identification and category message (TC 1-4).

    These are what a decoder turns into the ``flight`` and ``category``
    fields of ``aircraft.json`` — unlike the position messages they never
    advance ``seen_pos``, which is exactly the semantics the documents need
    for their state-only updates.
    """
    tc = _IDENTITY_TC.get(category, 1)
    callsign_field = 0
    for ch in callsign[:8].ljust(8):
        callsign_field = (callsign_field << 6) | _ia5_char(ch)
    return ((tc << 51) | callsign_field).to_bytes(7, "big")


def df17_identity_payload(hex_icao: str, category: str, callsign: str) -> bytes:
    """A 14-byte DF17 identification message with valid parity."""
    icao = int(hex_icao, 16)
    head = bytes([_DF17_CA5]) + icao.to_bytes(3, "big") + identity_me(category, callsign)
    return head + crc24(head).to_bytes(3, "big")
