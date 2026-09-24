import os
import subprocess
import time

import pytest

from mixhand.executor.group import begin_group, end_group, record, undo_group
from mixhand.executor.logicpro import LogicPro
from mixhand.executor.primitives import (
    VOLUME_DB_MAX,
    VOLUME_DB_MIN,
    add_send,
    create_aux,
    duplicate_track,
    set_pan,
    set_send_level,
    set_volume,
)
from mixhand.state.reader import read_session

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("MIXHAND_LIVE") != "1", reason="drives the real Logic Pro; set MIXHAND_LIVE=1"),
]

TRACK = "Lead Vocal"
DOUBLE = "Mixhand Double"
AUX = "Mixhand Verb"
ANSWERS_WITHIN_S = 600
RUN = [
    (set_volume, {"track": TRACK, "db": -6.0}),
    (duplicate_track, {"source": TRACK, "new_name": DOUBLE}),
    (set_pan, {"track": DOUBLE, "value": -40}),
    (create_aux, {"name": AUX, "plugin": "ChromaVerb"}),
    (add_send, {"track": TRACK, "aux": AUX}),
    (set_send_level, {"track": TRACK, "aux": AUX, "db": -12.0}),
    (set_volume, {"track": AUX, "db": -6.0}),
    (set_volume, {"track": TRACK, "db": -4.0}),
    (set_pan, {"track": TRACK, "value": 20}),
]


# Logic can stop answering Apple events for minutes after an aux is undone (SETUP.md).
def logic_answers():
    deadline = time.monotonic() + ANSWERS_WITHIN_S
    while time.monotonic() < deadline:
        done = subprocess.run(
            ["osascript", "-e", 'tell application "Logic Pro" to get name of front document'], capture_output=True, timeout=60
        )
        if done.returncode == 0:
            return
        time.sleep(5)
    pytest.fail(f"Logic did not answer within {ANSWERS_WITHIN_S}s of the undo")


def test_undo_returns_the_session_to_where_the_run_found_it(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with LogicPro.from_env() as logic:
        before = read_session(logic)
        names = [t.name for t in before.tracks]
        if DOUBLE in names or AUX in names:
            pytest.fail(f"delete {DOUBLE!r} and {AUX!r} from the test project before running this")
        [lead] = [t for t in before.tracks if t.name == TRACK]
        if not VOLUME_DB_MIN <= lead.volume_db <= VOLUME_DB_MAX:
            pytest.fail(f"{TRACK} is at {lead.volume_db} dB, which undo could not put back; move it within range first")
        group = begin_group("live undo", before.project.path, names)
        for primitive, args in RUN:
            record(group, primitive.__name__, args, primitive(logic, **args))
        end_group(logic, group)
        report = list(undo_group(logic))

    assert [status for status, _ in report[:-1]] == ["pass"] * (len(report) - 1), report
    assert "Sent 8 undo steps" in [detail for _, detail in report], report
    logic_answers()
    with LogicPro.from_env() as logic:
        after = read_session(logic)
    assert [(t.name, t.plugins, t.sends, t.bus) for t in after.tracks] == [(t.name, t.plugins, t.sends, t.bus) for t in before.tracks]
    [back] = [t for t in after.tracks if t.name == TRACK]
    assert abs(back.volume_db - lead.volume_db) <= 1.1 and abs(back.pan - lead.pan) <= 5, (back, lead)
