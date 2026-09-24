import os

import pytest
from test_create_aux_live import AUX, UNDO_ITEM, no_aux_left, osascript, session

from mixhand.executor.logicpro import LogicPro
from mixhand.executor.primitives import add_send, create_aux, routes, undo

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("MIXHAND_LIVE") != "1", reason="drives the real Logic Pro; set MIXHAND_LIVE=1"),
]

TRACK = "Lead Vocal"
UNDONE = [
    "Undo Change Send in Channel Strip",
    "Undo Change Input in Channel Strip",
    "Undo Insert Plug-in in Channel Strip",
    "Undo Renaming",
    "Undo Create Track",
    "Undo Create New Auxiliary Channel Strip",
]


def routed(name):
    return [(s.inputs, s.sends) for s in routes() if s.name == name]


@pytest.mark.parametrize("run", range(10))
def test_send_a_track_to_a_new_aux_then_undo_it(run, no_aux_left):
    before = session()
    if before[1] in UNDONE:
        pytest.fail(f"the project's last edit was {before[1]!r}, so the test's undo steps could not be told apart; make any other edit first")
    sends_before = routed(TRACK)[0][1]

    with LogicPro.from_env() as logic:
        assert create_aux(logic, AUX, "ChromaVerb").verified
        sent = add_send(logic, TRACK, AUX)
        assert sent.verified and sent.detail.endswith("undo 2 removes it"), sent.detail
        bus = sent.detail.split(" on ")[1].split(";")[0]
        assert routed(AUX) == [((bus,), ())]
        assert routed(TRACK)[0][1] == (*sends_before, bus)

        again = add_send(logic, TRACK, AUX)
        assert again.detail == f"{TRACK} already sends to {AUX} on {bus}"
        assert routed(TRACK)[0][1] == (*sends_before, bus)

        undone = []
        for _ in UNDONE:
            undone.append(osascript(UNDO_ITEM))
            assert undone[-1] != before[1], f"only {len(undone) - 1} undo steps were the test's own: {undone}"
            undo(logic, 1)
    assert undone == UNDONE
    assert session() == before
    assert routed(TRACK)[0][1] == sends_before
