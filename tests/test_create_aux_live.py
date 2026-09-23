import os
import subprocess

import pytest

from mixhand.executor.logicpro import LogicPro
from mixhand.executor.primitives import create_aux, inserts, track_index, undo

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("MIXHAND_LIVE") != "1", reason="drives the real Logic Pro; set MIXHAND_LIVE=1"),
]

AUX = "Mixhand Aux"
PLUGIN = "Channel EQ"

# A menu item's title is only refreshed when its menu opens.
UNDO_ITEM = """
tell application "System Events" to tell process "Logic Pro"
    set edit to menu 1 of menu bar item "Edit" of menu bar 1
    click menu bar item "Edit" of menu bar 1
    delay 0.3
    set undoing to name of menu item 1 of edit
    perform action "AXCancel" of edit
    return undoing
end tell
"""


def osascript(script):
    done = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=30, check=True)
    return done.stdout.strip()


def session():
    with LogicPro.from_env() as logic:
        tracks = [(t["name"], t.get("type")) for t in logic.tracks()]
    return tracks, osascript(UNDO_ITEM)


def plugins_on(logic, name):
    return [s["name"] for s in inserts(logic, track_index(logic, name), name) if s["occupied"]]


@pytest.fixture
def no_aux_left():
    with LogicPro.from_env() as logic:
        if AUX in [t["name"] for t in logic.tracks()]:
            pytest.fail(f"delete {AUX!r} from the test project before running this")
    yield
    with LogicPro.from_env() as logic:
        if AUX in [t["name"] for t in logic.tracks()]:
            pytest.fail(f"{AUX!r} was left behind; undo in Logic until it and its strip are gone")


@pytest.mark.parametrize("run", range(10))
def test_create_an_aux_with_channel_eq_then_undo_it(run, no_aux_left):
    before = session()

    with LogicPro.from_env() as logic:
        first = create_aux(logic, AUX, PLUGIN)
        assert first.verified
        tracks = [(t["name"], t.get("type")) for t in logic.tracks()]
        assert [t for t in tracks if t not in before[0]] == [(AUX, "aux")]
        assert plugins_on(logic, AUX) == [PLUGIN]

        again = create_aux(logic, AUX, PLUGIN)
        assert again.detail.startswith(f"Aux {AUX} already exists")
        assert [(t["name"], t.get("type")) for t in logic.tracks()] == tracks
        assert plugins_on(logic, AUX) == [PLUGIN]

        undone = []
        for _ in range(4):
            undone.append(osascript(UNDO_ITEM))
            undo(logic, 1)
    assert undone[-1] == "Undo Create New Auxiliary Channel Strip", undone
    assert session() == before
