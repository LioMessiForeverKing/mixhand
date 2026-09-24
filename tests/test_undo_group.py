import pytest
from conftest import tracks

from mixhand.executor import ExecutorError
from mixhand.executor.group import GROUP_PATH, Action, Group, undo_group
from mixhand.executor.logicpro import LogicPro

BEFORE, AFTER = "Undo Rename Track", "Undo Change Send in Channel Strip"


def moved(observed_raw):
    return {"state": "A", "verified": True, "observed_raw": observed_raw, "trace_id": "t1"}


def ran(**overrides) -> Group:
    group = Group(
        label="make it bigger",
        tracks_before=["Lead Vocal", "Adlib"],
        undo_title_before=BEFORE,
        undo_title_after=AFTER,
        actions=[
            Action("set_volume", {"track": "Lead Vocal", "db": -6.0}, 0, -3.0),
            Action("duplicate_track", {"source": "Lead Vocal", "new_name": "Double"}, 2, None),
            Action("set_pan", {"track": "Double", "value": -40}, 0, 0),
            Action("create_aux", {"name": "Verb", "plugin": "ChromaVerb"}, 4, None),
            Action("add_send", {"track": "Lead Vocal", "aux": "Verb"}, 2, None),
            Action("set_send_level", {"track": "Lead Vocal", "aux": "Verb", "db": -12.0}, 0, "-∞"),
            Action("set_volume", {"track": "Lead Vocal", "db": -4.0}, 0, -6.0),
            Action("set_pan", {"track": "Adlib", "value": 20}, 0, -5),
        ],
    )
    for name, value in overrides.items():
        setattr(group, name, value)
    group.save()
    return group


@pytest.fixture
def titles(monkeypatch):
    shown = []
    monkeypatch.setattr("mixhand.executor.group.undo_title", lambda: shown.pop(0))
    return shown


def serve(fake):
    fake.serve(
        resources={"logic://tracks": [tracks("Lead Vocal", "Double", "Adlib", "Verb")]},
        tools={
            "logic_mixer.set_volume": [moved(143)],
            "logic_mixer.set_pan": [moved(59)],
            "logic_edit.undo": [{"sent": True, "trace_id": "u"}],
        },
    )


def sent(fake):
    return [(c["call"], c["params"].get("track")) for c in fake.calls()]


def test_undo_puts_back_what_the_run_moved_on_tracks_it_found_then_undoes_what_it_made(fake, titles):
    serve(fake)
    ran()
    titles.extend([AFTER, BEFORE])
    with LogicPro.from_env() as logic:
        report = list(undo_group(logic))

    assert sent(fake) == [("logic_mixer.set_volume", 0), ("logic_mixer.set_pan", 2)] + [("logic_edit.undo", None)] * 8
    assert report == [
        ("pass", "Put back: Set Lead Vocal to -3.0 dB (asked -3.0 dB)"),
        ("pass", "Double's pan goes with the undo steps"),
        ("pass", "Lead Vocal's send to Verb goes with the undo steps"),
        ("pass", "Put back: Panned Adlib to -5 (asked -5)"),
        ("pass", "Sent 8 undo steps"),
        ("pass", f"Logic's Undo reads {BEFORE!r}, as before the run"),
    ]
    assert not GROUP_PATH.exists()


def test_undo_touches_nothing_when_logic_changed_after_the_run(fake, titles):
    serve(fake)
    ran()
    titles.append("Undo Move Region")
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match=r"reads 'Undo Move Region'.*made 8 undo steps.*Lead Vocal's volume \(was -3.0\)"):
        list(undo_group(logic))
    assert sent(fake) == []
    assert GROUP_PATH.exists()


def test_undo_will_not_guess_after_a_run_that_failed_partway(fake, titles):
    serve(fake)
    ran(failed="set_pan on 'Double' could not be read back", undo_title_after=None)
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="stopped partway.*could not be read back.*undo it in Logic by hand"):
        list(undo_group(logic))
    assert sent(fake) == []


def test_a_value_undo_cannot_put_back_is_named_rather_than_skipped(fake, titles):
    serve(fake)
    ran(actions=[Action("set_volume", {"track": "Adlib", "db": -6.0}, 0, None), Action("set_pan", {"track": "Lead Vocal", "value": 9}, 0, -5)])
    titles.extend([AFTER, "Undo Something Else"])
    with LogicPro.from_env() as logic:
        report = list(undo_group(logic))

    assert report[0] == ("fail", "Adlib's volume could not be read before the run, so it was not put back")
    assert report[1] == ("pass", "Put back: Panned Lead Vocal to -5 (asked -5)")
    assert report[2][0] == "unknown"


def test_there_is_nothing_to_undo_before_a_run(fake, titles):
    serve(fake)
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="no Mixhand run to undo"):
        list(undo_group(logic))
