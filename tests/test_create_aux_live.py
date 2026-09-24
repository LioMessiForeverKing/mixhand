import os
import subprocess
import time

import pytest

from mixhand.executor import ExecutorError
from mixhand.executor.ax import MIXER_STRIPS, PICK_PLUGIN, click_menu
from mixhand.executor.logicpro import LogicPro
from mixhand.executor.primitives import (
    HIDE_PLUGIN_WINDOWS,
    PLUGIN_FORMATS,
    PLUGIN_MENU,
    SLOT_LABEL,
    create_aux,
    insert_plugin,
    inserts,
    track_index,
    undo,
)

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("MIXHAND_LIVE") != "1", reason="drives the real Logic Pro; set MIXHAND_LIVE=1"),
]

AUX = "Mixhand Aux"
SECOND = "Mixhand Delay"
TRACK = "Lead Vocal"
UNDONE = [
    "Undo Insert Plug-in in Channel Strip",
    "Undo Renaming",
    "Undo Create Track",
    "Undo Create New Auxiliary Channel Strip",
]

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


# LogicProMCP numbers a plugin's slot from 0 even with an empty slot above it, so only the Mixer shows a gap.
INSERT_ROWS = MIXER_STRIPS + """
on run argv
    set {stripName, slotName} to argv
    set area to my mixerStrips()
    set ys to {}
    considering case
    tell application "System Events" to tell process "Logic Pro"
        repeat with s in UI elements of area
            if (value of text fields of s whose description is "name") is {stripName} then
                repeat with e in UI elements of s
                    set d to description of e
                    if d is slotName or d is "audio plug-in" then set end of ys to d & tab & (item 2 of (position of e as list))
                end repeat
            end if
        end repeat
    end tell
    end considering
    set AppleScript's text item delimiters to linefeed
    return ys as text
end run
"""


PICK_LOWEST = PICK_PLUGIN.replace("if y < topmost", "if y > topmost")
assert PICK_LOWEST != PICK_PLUGIN


def osascript(script):
    done = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=30, check=True)
    return done.stdout.strip()


def insert_rows(strip, label):
    deadline = time.monotonic() + 10
    while True:
        done = subprocess.run(["osascript", "-e", INSERT_ROWS, strip, label], capture_output=True, text=True, timeout=30)
        if done.returncode == 0:
            return [line.split("\t") for line in done.stdout.strip().splitlines()]
        assert time.monotonic() < deadline, done.stderr
        time.sleep(0.5)


def plugin_on_top(strip, label):
    rows = insert_rows(strip, label)
    plugin = [float(y) for name, y in rows if name == label]
    empty = [float(y) for name, y in rows if name == "audio plug-in"]
    assert len(plugin) == 1, rows
    return all(plugin[0] < y for y in empty)


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
@pytest.mark.parametrize("plugin", ["Channel EQ", "ChromaVerb", "Stereo Delay"])
def test_create_an_aux_with_a_plugin_then_undo_it(plugin, run, no_aux_left):
    before = session()
    if before[1] == "Undo Create New Auxiliary Channel Strip":
        pytest.fail("the project's last edit made an aux strip, so its undo steps could not be told apart; make any other edit first")

    with LogicPro.from_env() as logic:
        first = create_aux(logic, AUX, plugin)
        assert first.verified
        tracks = [(t["name"], t.get("type")) for t in logic.tracks()]
        assert [t for t in tracks if t not in before[0]] == [(AUX, "aux")]
        assert plugins_on(logic, AUX) == [SLOT_LABEL.get(plugin, plugin)]

        again = create_aux(logic, AUX, plugin)
        assert again.detail.startswith(f"Aux {AUX} already exists")
        assert [(t["name"], t.get("type")) for t in logic.tracks()] == tracks
        assert plugins_on(logic, AUX) == [SLOT_LABEL.get(plugin, plugin)]

        undone = []
        for _ in range(4):
            undone.append(osascript(UNDO_ITEM))
            assert undone[-1] != before[1], f"only {len(undone) - 1} undo steps were the test's own: {undone}"
            undo(logic, 1)
    assert undone[-1] == "Undo Create New Auxiliary Channel Strip", undone
    assert session() == before


@pytest.fixture
def no_auxes_left():
    with LogicPro.from_env() as logic:
        if {AUX, SECOND} & {t["name"] for t in logic.tracks()}:
            pytest.fail(f"delete {AUX!r} and {SECOND!r} from the test project before running this")
    yield
    with LogicPro.from_env() as logic:
        if {AUX, SECOND} & {t["name"] for t in logic.tracks()}:
            pytest.fail(f"{AUX!r} or {SECOND!r} was left behind; undo in Logic until both and their strips are gone")


@pytest.mark.parametrize("run", range(10))
def test_a_second_aux_takes_a_menu_plugin_then_both_undo(run, no_auxes_left):
    before = session()
    if before[1] in UNDONE:
        pytest.fail(f"the project's last edit was {before[1]!r}, so the test's undo steps could not be told apart; make any other edit first")

    with LogicPro.from_env() as logic:
        assert create_aux(logic, AUX, "ChromaVerb").verified
        second = create_aux(logic, SECOND, "Stereo Delay")
        assert second.verified, second.detail
        assert plugins_on(logic, AUX) == ["ChromaVerb"]
        assert plugins_on(logic, SECOND) == [SLOT_LABEL["Stereo Delay"]]
        assert plugin_on_top(SECOND, SLOT_LABEL["Stereo Delay"])

        undone = []
        for _ in UNDONE * 2:
            undone.append(osascript(UNDO_ITEM))
            assert undone[-1] != before[1], f"only {len(undone) - 1} undo steps were the test's own: {undone}"
            undo(logic, 1)
    assert undone == UNDONE * 2
    assert session() == before


@pytest.mark.parametrize("run", range(10))
def test_a_strip_with_an_empty_slot_above_a_plugin_is_refused_before_anything_changes(run, no_auxes_left):
    before = session()
    if before[1] in UNDONE:
        pytest.fail(f"the project's last edit was {before[1]!r}, so the test's undo steps could not be told apart; make any other edit first")

    with LogicPro.from_env() as logic:
        if plugins_on(logic, TRACK):
            pytest.fail(f"remove every plugin from {TRACK!r} before running this")
        assert create_aux(logic, AUX, "ChromaVerb").verified
        deadline = time.monotonic() + 10
        while len(insert_rows(TRACK, "")) < 2:
            assert time.monotonic() < deadline, f"{TRACK} never showed a second insert row"
            time.sleep(0.5)
        args = [TRACK, *PLUGIN_MENU["Stereo Delay"], *PLUGIN_FORMATS]
        picked = subprocess.run(["osascript", "-e", PICK_LOWEST, *args], capture_output=True, text=True, timeout=30)
        assert picked.returncode == 0, picked.stderr
        while plugins_on(logic, TRACK) != [SLOT_LABEL["Stereo Delay"]]:
            assert time.monotonic() < deadline + 10, f"Stereo Delay never showed on {TRACK}"
            time.sleep(0.5)
        click_menu(*HIDE_PLUGIN_WINDOWS)
        assert not plugin_on_top(TRACK, SLOT_LABEL["Stereo Delay"])
        chain = plugins_on(logic, TRACK)

        with pytest.raises(ExecutorError, match="empty insert slot above a plug-in"):
            insert_plugin(logic, TRACK, "ChromaVerb")
        assert plugins_on(logic, TRACK) == chain

        undone = []
        for _ in UNDONE[:1] + UNDONE:
            undone.append(osascript(UNDO_ITEM))
            assert undone[-1] != before[1], f"only {len(undone) - 1} undo steps were the test's own: {undone}"
            undo(logic, 1)
    assert undone == UNDONE[:1] + UNDONE
    assert session() == before
