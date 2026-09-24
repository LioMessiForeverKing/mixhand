import os

import pytest

from mixhand.executor.logicpro import LogicPro
from mixhand.executor.primitives import set_pan, set_volume
from mixhand.state.reader import read_session

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("MIXHAND_LIVE") != "1", reason="drives the real Logic Pro; set MIXHAND_LIVE=1"),
]

TRACK = "Lead Vocal"
SETTINGS = [(-6.0, -40), (-3.2, 37), (-17.0, -64), (4.5, 63), (-0.5, 5), (-12.0, -17), (2.0, 25), (-9.0, 0), (-1.0, -8), (-14.5, 33)]


def landed(detail):
    return detail.split(" to ")[1].split(" ")[0]


@pytest.fixture(scope="module", autouse=True)
def restore_unity_and_centre():
    yield
    with LogicPro.from_env() as logic:
        set_volume(logic, TRACK, 0.0)
        set_pan(logic, TRACK, 0)


@pytest.mark.parametrize(("db", "pan"), SETTINGS)
def test_state_reads_back_the_volume_and_pan_that_were_set(db, pan):
    with LogicPro.from_env() as logic:
        volume = float(landed(set_volume(logic, TRACK, db).detail))
        panned = int(landed(set_pan(logic, TRACK, pan).detail))
        session = read_session(logic)

    [lead] = [t for t in session.tracks if t.name == TRACK]
    assert (lead.volume_db, lead.pan) == (volume, panned)
