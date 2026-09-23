import os
import time

import pytest

from mixhand.executor.fader import DB_AT_RAW, volume_contract
from mixhand.executor.logicpro import LogicPro
from mixhand.executor.primitives import set_pan, set_volume, track_index

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("MIXHAND_LIVE") != "1", reason="drives the real Logic Pro; set MIXHAND_LIVE=1"),
]

TRACK = "Lead Vocal"
VOLUMES = [-6.0, -3.2, -12.0, 2.0, -20.0, -0.5, -9.0, 4.5, -1.0, -30.0]
PANS = [-40, 40, -64, 63, -17, 5, 0, -25, 33, -8]


def strip():
    with LogicPro.from_env() as logic:
        index = track_index(logic, TRACK)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            strips = [s for s in logic.read("logic://mixer").get("strips", []) if s.get("trackIndex") == index]
            if strips:
                return strips[0]
            time.sleep(0.25)
    raise AssertionError("logic://mixer never showed the strip")


def landed(detail):
    return detail.split(" to ")[1].split(" ")[0]


@pytest.fixture(scope="module", autouse=True)
def restore_unity_and_centre():
    yield
    with LogicPro.from_env() as logic:
        set_volume(logic, TRACK, 0.0)
        set_pan(logic, TRACK, 0)


@pytest.mark.parametrize("db", VOLUMES)
def test_set_volume_on_lead_vocal(db):
    with LogicPro.from_env() as logic:
        first = set_volume(logic, TRACK, db)
        again = set_volume(logic, TRACK, db)
    assert first.verified and again.detail == first.detail
    raw = [f"{d:+.1f}" for d in DB_AT_RAW].index(landed(first.detail))
    assert abs(DB_AT_RAW[raw] - db) <= 2.5
    assert strip()["volume"] == pytest.approx(volume_contract(raw), abs=1e-6)


@pytest.mark.parametrize("pan", PANS)
def test_set_pan_on_lead_vocal(pan):
    with LogicPro.from_env() as logic:
        first = set_pan(logic, TRACK, pan)
        again = set_pan(logic, TRACK, pan)
    assert first.verified and again.detail == first.detail
    value = int(landed(first.detail))
    assert abs(value - pan) <= 5
    assert strip()["pan"] == pytest.approx(value / (64 if value < 0 else 63), abs=1e-6)
