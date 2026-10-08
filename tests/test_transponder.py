"""DF17 payload construction: parity, CPR quantisation and the IA5 alphabet."""

from __future__ import annotations

import pytest

from dump1090_simulator.transponder import (
    airborne_position_me,
    cpr_encode,
    crc24,
    df17_identity_payload,
    df17_payload,
    encode_altitude,
    identity_me,
    syndrome,
)

_ICAO = "4ca918"


def _decode_altitude(field: int) -> int:
    """Inverse of the Q-encoded 12-bit ADS-B altitude field."""
    value = ((field >> 5) << 4) | (field & 0xF)
    return 25 * value - 1000


def _decode_callsign(me: bytes) -> str:
    """Inverse of the 48-bit callsign field inside an identification ME."""
    field = int.from_bytes(me, "big") & ((1 << 48) - 1)
    chars = []
    for shift in range(42, -1, -6):
        code = (field >> shift) & 0x3F
        if 1 <= code <= 26:
            chars.append(chr(ord("A") + code - 1))
        elif 32 <= code <= 41:
            chars.append(chr(ord("0") + code - 32))
        else:
            chars.append(" ")
    return "".join(chars)


def test_crc24_of_empty_and_zero_input() -> None:
    assert crc24(b"") == 0
    assert crc24(b"\x00\x00\x00") == 0


def test_crc24_detects_any_flipped_bit() -> None:
    base = crc24(b"dump1090")
    assert base != crc24(b"dump109x")
    assert crc24(b"abc") != crc24(b"abd")


def test_syndrome_of_a_short_message_is_zero() -> None:
    assert syndrome(b"") == 0
    assert syndrome(b"\x01") == 0
    assert syndrome(b"\x01\x02") == 0


def test_syndrome_accepts_our_generated_parity() -> None:
    payload = df17_payload(_ICAO, 34000.0, 51.5, -0.12, odd=True)
    assert syndrome(payload) == 0
    identity = df17_identity_payload(_ICAO, "A2", "BAW123")
    assert syndrome(identity) == 0


def test_syndrome_rejects_a_corrupted_message() -> None:
    payload = bytearray(df17_payload(_ICAO, 12000.0, 48.8, 2.35, odd=False))
    payload[5] ^= 0x04
    assert syndrome(bytes(payload)) != 0


@pytest.mark.parametrize(
    ("hex_icao", "alt_ft", "lat", "lon", "odd"),
    [
        ("4ca918", 0.0, 0.0, 0.0, False),
        ("4ca918", 34000.0, 51.5, -0.12, True),
        ("abc123", -1000.0, -33.9, 151.2, False),
        ("ffffff", 41000.0, 89.0, 179.0, True),
    ],
)
def test_df17_position_payload_structure(
    hex_icao: str, alt_ft: float, lat: float, lon: float, odd: bool
) -> None:
    payload = df17_payload(hex_icao, alt_ft, lat, lon, odd)
    assert len(payload) == 14
    assert payload[0] == 0x8D
    assert payload[1:4] == bytes.fromhex(hex_icao)
    assert syndrome(payload) == 0


def test_df17_identity_payload_structure() -> None:
    payload = df17_identity_payload("4ca918", "A2", "RYR22GK")
    assert len(payload) == 14
    assert payload[0] == 0x8D
    assert payload[1:4] == bytes.fromhex("4ca918")
    assert syndrome(payload) == 0
    assert _decode_callsign(payload[4:11]) == "RYR22GK "


@pytest.mark.parametrize("alt_ft", [-500.0, 0.0, 100.5, 5000.0, 18000.0, 34000.0, 41000.0])
def test_altitude_round_trips_within_one_coding_step(alt_ft: float) -> None:
    field = encode_altitude(alt_ft)
    assert 0 <= field < (1 << 12)
    assert (field >> 4) & 0x1 == 1, "Q bit must be set"
    assert abs(_decode_altitude(field) - alt_ft) <= 25.0


def test_altitude_field_is_clamped_at_both_ends() -> None:
    assert encode_altitude(-500000.0) == 16
    assert encode_altitude(1e9) == 4095


@pytest.mark.parametrize(
    ("lat", "lon"),
    [(0.0, 0.0), (51.5, -0.12), (-33.9, 151.2), (45.0, 180.0), (89.0, -179.5)],
)
def test_cpr_values_stay_inside_17_bits(lat: float, lon: float) -> None:
    for odd in (False, True):
        lat_cpr, lon_cpr = cpr_encode(lat, lon, odd)
        assert 0 <= lat_cpr < (1 << 17)
        assert 0 <= lon_cpr < (1 << 17)


def test_cpr_is_deterministic_but_odd_differs_from_even() -> None:
    even = cpr_encode(51.5, -0.12, odd=False)
    assert cpr_encode(51.5, -0.12, odd=False) == even
    assert cpr_encode(51.5, -0.12, odd=True) != even


def test_position_me_carries_type_code_11_and_the_cpr_format_bit() -> None:
    even = airborne_position_me(34000.0, 51.5, -0.12, odd=False)
    odd = airborne_position_me(34000.0, 51.5, -0.12, odd=True)
    assert len(even) == 7
    assert len(odd) == 7
    assert even[0] >> 3 == 11
    assert odd[0] >> 3 == 11
    assert even[2] & 0x04 == 0
    assert odd[2] & 0x04 == 0x04
    assert even != odd


@pytest.mark.parametrize(
    ("category", "type_code"),
    [("A0", 1), ("A1", 2), ("A2", 3), ("A3", 4), ("A7", 1), ("ZZ", 1)],
)
def test_identity_me_type_code_follows_the_category(category: str, type_code: int) -> None:
    me = identity_me(category, "BAW123")
    assert len(me) == 7
    assert me[0] >> 3 == type_code


def test_identity_me_encodes_the_callsign_in_ia5() -> None:
    assert _decode_callsign(identity_me("A2", "BAW123")) == "BAW123  "
    assert _decode_callsign(identity_me("A2", "ABCDEFGHI")) == "ABCDEFGH"
    assert _decode_callsign(identity_me("A0", "N172DB")) == "N172DB  "


def test_identity_me_blanks_characters_outside_the_alphabet() -> None:
    assert _decode_callsign(identity_me("A0", "ab-c")) == "        "
    assert _decode_callsign(identity_me("A0", "PH-B1")) == "PH B1   "
