"""Fleet generation: hexes, callsigns, squawks, registrations and spawn."""

from __future__ import annotations

import random
import re
from typing import cast

from conftest import TEST_LAT, TEST_LON, base_config
from dump1090_simulator.fleet import (
    AIRLINE_PREFIXES,
    make_callsign,
    make_hex,
    make_registration,
    make_squawk,
    spawn_fleet,
)
from dump1090_simulator.models import AIRCRAFT_TYPES, Phase
from dump1090_simulator.physics import great_circle_km

_HEX_RE = re.compile(r"[0-9a-f]{6}")
_SQUAWK_RE = re.compile(r"[0-7]{4}")
_CALLSIGN_RE = re.compile(r"[A-Z]{3}[0-9]{1,4}(?:[A-Z]{2})?")
_NORMAL_SQUAWKS = {"1000", "1200", "2000", "3000", "4000", "7000"}


class _StubRng:
    """A stand-in random source returning scripted ``getrandbits`` values."""

    def __init__(self, *values: int) -> None:
        self._values = list(values)

    def getrandbits(self, width: int) -> int:
        assert width == 24
        return self._values.pop(0)


def test_make_hex_returns_six_lowercase_hex_digits() -> None:
    rng = random.Random(7)
    used: set[str] = set()
    for _ in range(200):
        value = make_hex(rng, used)
        assert _HEX_RE.fullmatch(value)
        assert value != "000000"
    assert len(used) == 200


def test_make_hex_rejects_the_all_zero_code() -> None:
    stub = cast(random.Random, _StubRng(0, 0xABCDEF))
    assert make_hex(stub) == "abcdef"


def test_make_hex_rejects_a_code_already_in_use() -> None:
    stub = cast(random.Random, _StubRng(0xABCDEF, 5))
    assert make_hex(stub, {"abcdef"}) == "000005"


def test_make_hex_uses_the_supplied_used_set() -> None:
    rng = random.Random(11)
    used: set[str] = set()
    first = make_hex(rng, used)
    assert used == {first}


def test_make_registration_matches_n_numbers_and_european_styles() -> None:
    rng = random.Random(3)
    for _ in range(300):
        registration = make_registration(rng)
        assert re.fullmatch(r"N[0-9]{1,5}", registration) or re.fullmatch(
            r"(?:G|D|F|I|PH|EC|OE|CS)-[A-Z]{3}", registration
        )


def test_general_aviation_callsigns_use_the_registration() -> None:
    rng = random.Random(5)
    assert make_callsign(rng, "A0", "N172DB") == "N172DB"
    assert make_callsign(rng, "A1", "G-ABCD") == "G-ABCD"


def test_airline_callsigns_look_like_real_designators() -> None:
    rng = random.Random(13)
    for _ in range(300):
        callsign = make_callsign(rng, "A2", "N1")
        assert _CALLSIGN_RE.fullmatch(callsign)
        assert callsign[:3] in AIRLINE_PREFIXES


def test_make_squawk_is_four_octal_digits() -> None:
    rng = random.Random(9)
    seen_normal = 0
    for _ in range(400):
        code = make_squawk(rng)
        assert _SQUAWK_RE.fullmatch(code)
        assert code != "0000"
        if code in _NORMAL_SQUAWKS:
            seen_normal += 1
    assert seen_normal > 50


def test_spawn_fleet_size_and_hex_uniqueness() -> None:
    config = base_config(count=12)
    fleet = spawn_fleet(config, random.Random(42))
    assert len(fleet) == 12
    assert len({ac.hex for ac in fleet}) == 12


def test_spawn_scatters_aircraft_around_the_receiver() -> None:
    fleet = spawn_fleet(base_config(count=10), random.Random(42))
    for ac in fleet:
        distance = great_circle_km(TEST_LAT, TEST_LON, ac.lat, ac.lon)
        assert 4.0 <= distance <= 301.0


def test_spawn_uses_only_types_from_the_performance_table() -> None:
    fleet = spawn_fleet(base_config(count=20), random.Random(42))
    for ac in fleet:
        spec = AIRCRAFT_TYPES[ac.icao_type]
        assert ac.category == spec.category
        assert ac.target_alt_ft == 0 or spec.min_ft <= ac.target_alt_ft <= spec.max_ft
        assert ac.gs_kt <= spec.cruise_kt * 1.06 + 1e-9
        assert _HEX_RE.fullmatch(ac.hex)


def test_spawn_phases_stay_inside_the_flight_cycle() -> None:
    fleet = spawn_fleet(base_config(count=20), random.Random(42))
    assert {ac.phase for ac in fleet} <= set(Phase)
    for ac in fleet:
        if ac.phase is Phase.GROUND:
            assert ac.alt_ft == 0.0
            assert ac.gs_kt == 0.0
            assert ac.vs_fpm == 0.0
            assert ac.ground_seconds > 0.0


def test_spawn_initialises_message_bookkeeping() -> None:
    config = base_config(count=10)
    fleet = spawn_fleet(config, random.Random(42))
    for ac in fleet:
        assert ac.messages == 0
        assert (ac.r_lat, ac.r_lon, ac.r_alt_ft) == (ac.lat, ac.lon, ac.alt_ft)
        assert ac.last_msg_t < config.start
        assert ac.last_pos_t < config.start
        assert ac.pos_interval > ac.msg_interval >= config.interval
        assert ac.callsign
        assert ac.registration
        assert _SQUAWK_RE.fullmatch(ac.squawk)


def test_spawn_schedules_squawk_changes_only_for_normal_codes() -> None:
    config = base_config(count=40)
    fleet = spawn_fleet(config, random.Random(42))
    scheduled = 0
    for ac in fleet:
        if ac.squawk_change_at is None:
            continue
        scheduled += 1
        assert ac.squawk != "7700"
        assert config.start + 5.0 <= ac.squawk_change_at <= config.start + 300.0
    assert scheduled > 0
