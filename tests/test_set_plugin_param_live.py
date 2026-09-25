import os

import pytest
from test_create_aux_live import UNDO_ITEM, UNDONE, osascript

from mixhand.executor.logicpro import LogicPro
from mixhand.executor.primitives import create_aux, insert_plugin, inserts, set_plugin_param, track_index, undo

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("MIXHAND_LIVE") != "1", reason="drives the real Logic Pro; set MIXHAND_LIVE=1"),
]

TRACK = "Lead Vocal"
INSERTED = "Undo Insert Plug-in in Channel Strip"
SWEEPS = {
    "Compressor": [("Threshold", v) for v in (0, 100, 35, 60, 1, 99, 50, 20, 80, 45)],
    "Channel EQ": [
        *[
            (f"{band} Gain", db)
            for band, db in (
                ("Peak 1", 3), ("Peak 1", -6.5), ("Peak 2", 0), ("Peak 2", 24), ("Peak 3", -24),
                ("Peak 3", 2.37), ("Peak 4", -12), ("Peak 4", 0.1), ("Peak 1", -0.1), ("Peak 2", 9.9),
            )
        ],
        *[
            (f"{band} Frequency", hz)
            for band, hz in (
                ("Peak 1", 100), ("Peak 1", 805), ("Peak 2", 19000), ("Peak 2", 150), ("Peak 3", 2500),
                ("Peak 3", 12000), ("Peak 4", 1000), ("Peak 4", 440), ("Peak 1", 5000), ("Peak 2", 250),
            )
        ],
    ],
}

DELAY = "Mixhand Delay"
DELAY_SWEEP = [
    ("Crossfeed L->R", 60), ("Crossfeed R->L", 45), ("Left Feedback", 0), ("Right Feedback", 100), ("Left Note", 0.75),
    ("Right Note", 1.5), ("Crossfeed L->R", 1), ("Left Note", 0.25), ("Right Feedback", 99), ("Right Note", 3),
]


def plugins_on(logic):
    return [s["name"] for s in inserts(logic, track_index(logic, TRACK), TRACK) if s["occupied"]]


@pytest.mark.parametrize("plugin", SWEEPS)
def test_set_each_mapped_param_then_undo_the_plugin(plugin):
    before = osascript(UNDO_ITEM)
    if before == INSERTED:
        pytest.fail(f"the project's last edit was {INSERTED!r}, so the test's undo step could not be told apart; make any other edit first")

    with LogicPro.from_env() as logic:
        if plugins_on(logic):
            pytest.fail(f"remove every plugin from {TRACK!r} before running this")
        assert insert_plugin(logic, TRACK, plugin).verified
        try:
            for param, value in SWEEPS[plugin]:
                first = set_plugin_param(logic, TRACK, plugin, param, value)
                again = set_plugin_param(logic, TRACK, plugin, param, value)
                assert first.verified and again.detail == first.detail
            assert plugins_on(logic) == [plugin]
        finally:
            left = osascript(UNDO_ITEM)
            assert left == INSERTED, f"the last edit is {left!r}, not the test's insert; remove {plugin} from {TRACK} by hand"
            undo(logic, 1)
    assert osascript(UNDO_ITEM) == before


def test_set_each_stereo_delay_row_then_undo_the_aux():
    before = osascript(UNDO_ITEM)
    with LogicPro.from_env() as logic:
        if any(t["name"] == DELAY for t in logic.tracks()):
            pytest.fail(f"delete {DELAY!r} before running this")
        assert create_aux(logic, DELAY, "Stereo Delay").verified
        try:
            for param, value in DELAY_SWEEP:
                first = set_plugin_param(logic, DELAY, "Stereo Delay", param, value)
                again = set_plugin_param(logic, DELAY, "Stereo Delay", param, value)
                shown = first.detail.split(" to ", 1)[1].split(" (asked", 1)[0]
                assert first.verified and again.detail == f"{DELAY}'s Stereo Delay {param} is already {shown}"
        finally:
            for title in UNDONE:
                left = osascript(UNDO_ITEM)
                assert left == title, f"the last edit is {left!r}, not {title!r}; remove {DELAY} by hand"
                undo(logic, 1)
    assert osascript(UNDO_ITEM) == before
