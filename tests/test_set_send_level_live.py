import os
import time

import pytest
from test_add_send_live import TRACK, UNDONE, routed
from test_create_aux_live import AUX, UNDO_ITEM, osascript, session

from mixhand.executor.ax import read_send_level
from mixhand.executor.logicpro import LogicPro
from mixhand.executor.primitives import add_send, create_aux, set_send_level, undo

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("MIXHAND_LIVE") != "1", reason="drives the real Logic Pro; set MIXHAND_LIVE=1"),
]

SECOND = "Mixhand Aux 2"
LEVELS = [-12, -3.5, -40, 0, -60, -6, -7, -24.5, -0.1, -52, -3, -6.3]
PROJECT_WITHIN_S = 120


def near(level, db):
    target = round(db, 1) if round(db * 10) >= -60 else round(db)
    tolerance = 0.05 if target >= -6 else 0.5 if target >= -48 else 1.0
    return abs(float(level) - target) <= tolerance


# After an aux is undone, LogicProMCP can report no front project for a minute or more (SETUP.md).
def project_resolves(logic):
    deadline = time.monotonic() + PROJECT_WITHIN_S
    while not logic.front_project():
        if time.monotonic() >= deadline:
            raise AssertionError(f"LogicProMCP reported no front project for {PROJECT_WITHIN_S}s; undo the test's steps by hand")
        time.sleep(0.5)


@pytest.fixture(scope="module")
def buses():
    before = session()
    if before[1] in UNDONE:
        pytest.fail(f"the project's last edit was {before[1]!r}, so the test's undo steps could not be told apart; make any other edit first")
    made = {}
    with LogicPro.from_env() as logic:
        project_resolves(logic)
        if {AUX, SECOND} & {t["name"] for t in logic.tracks()}:
            pytest.fail(f"delete {AUX!r} and {SECOND!r} from the test project before running this")
        for aux, plugin in ((AUX, "ChromaVerb"), (SECOND, "Channel EQ")):
            assert create_aux(logic, aux, plugin).verified
            sent = add_send(logic, TRACK, aux)
            assert sent.verified, sent.detail
            made[aux] = int(sent.detail.split(" on Bus ")[1].split(";")[0])
    yield made
    undone = []
    with LogicPro.from_env() as logic:
        for _ in UNDONE * len(made):
            project_resolves(logic)
            undone.append(osascript(UNDO_ITEM))
            assert undone[-1] != before[1], f"only {len(undone) - 1} undo steps were the test's own: {undone}"
            undo(logic, 1)
    assert undone == UNDONE * len(made)
    assert session() == before


@pytest.mark.parametrize("db", LEVELS)
def test_set_a_send_level_without_adding_an_undo_step(db, buses):
    route = routed(TRACK)
    with LogicPro.from_env() as logic:
        project_resolves(logic)
        first = set_send_level(logic, TRACK, AUX, db)
        again = set_send_level(logic, TRACK, AUX, db)
    assert first.verified, first.detail
    level = read_send_level(TRACK, buses[AUX])
    assert near(level, db), level
    assert again.detail == f"{TRACK}'s send to {AUX} is already at {level} dB"
    assert routed(TRACK) == route
    assert osascript(UNDO_ITEM) == UNDONE[0]


def test_each_send_moves_only_its_own_knob(buses):
    with LogicPro.from_env() as logic:
        project_resolves(logic)
        set_send_level(logic, TRACK, AUX, -10)
        set_send_level(logic, TRACK, SECOND, -20)
        assert near(read_send_level(TRACK, buses[AUX]), -10)
        assert near(read_send_level(TRACK, buses[SECOND]), -20)
        set_send_level(logic, TRACK, AUX, -3)
    assert near(read_send_level(TRACK, buses[AUX]), -3)
    assert near(read_send_level(TRACK, buses[SECOND]), -20)
