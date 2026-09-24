import pytest
from conftest import PROJECT
from test_add_send import PAIR, listing, mixer, strip

from mixhand.executor import ExecutorError
from mixhand.executor.logicpro import LogicPro
from mixhand.executor.primitives import set_send_level

SENT = mixer(strip("Lead Vocal", sends=("Bus 3",), empty=0), strip("Verb", inputs=("Bus 3",)))


@pytest.fixture
def knob(monkeypatch):
    done = {"routes": [], "levels": [], "steps": [], "landed": None}

    def step(*args):
        done["steps"].append(args)
        if isinstance(done["landed"], Exception):
            raise done["landed"]
        return done["levels"][0], done["landed"], 12

    monkeypatch.setattr("mixhand.executor.primitives.read_routes", lambda: done["routes"].pop(0))
    monkeypatch.setattr("mixhand.executor.primitives.read_send_level", lambda *args: done["levels"].pop(0))
    monkeypatch.setattr("mixhand.executor.primitives.step_send_level", step)
    return done


def serve(fake, tracks=PAIR, project=None):
    resources = {"logic://tracks": [listing(*tracks)]}
    if project:
        resources["logic://project/info"] = project
    fake.serve(resources=resources)


def test_the_send_steps_to_the_level_asked_and_reports_the_one_it_replaced(fake, knob):
    serve(fake)
    knob.update(routes=[SENT, SENT], levels=["-∞", "-12.0"], landed="-12.0")
    with LogicPro.from_env() as logic:
        result = set_send_level(logic, "Lead Vocal", "Verb", -12)

    assert result.ok and result.verified
    assert result.detail == "Set Lead Vocal's send to Verb to -12.0 dB (asked -12 dB, was -∞ dB; undo does not restore it)"
    assert knob["steps"] == [("Lead Vocal", 3, -120, 5, 300)]
    assert [e["event"] for e in fake.log()] == ["set_send_level.start", "set_send_level.done"]


def test_a_send_already_at_the_level_is_not_moved(fake, knob):
    serve(fake)
    knob.update(routes=[SENT], levels=["-12.1"])
    with LogicPro.from_env() as logic:
        result = set_send_level(logic, "Lead Vocal", "Verb", -12)

    assert result.verified and result.detail == "Lead Vocal's send to Verb is already at -12.1 dB"
    assert knob["steps"] == []
    assert [e["event"] for e in fake.log()] == ["set_send_level.skipped"]


@pytest.mark.parametrize(
    "db, target, tolerance",
    [(0, 0, 0), (-3.46, -35, 0), (-6.04, -60, 0), (-6.3, -60, 5), (-12.4, -120, 5), (-48, -480, 5), (-52.6, -530, 10), (-60, -600, 10)],
)
def test_the_target_is_as_fine_as_the_knob_steps_there(fake, knob, db, target, tolerance):
    serve(fake)
    knob.update(routes=[SENT, SENT], levels=["-∞", "-99.0"], landed="-99.0")
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError):
        set_send_level(logic, "Lead Vocal", "Verb", db)

    assert knob["steps"] == [("Lead Vocal", 3, target, tolerance, 300)]


@pytest.mark.parametrize("db", [0.5, -60.5])
def test_a_level_outside_the_range_is_refused_before_logic_is_asked(fake, knob, db):
    serve(fake)
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="outside -60..0 dB"):
        set_send_level(logic, "Lead Vocal", "Verb", db)

    assert knob["steps"] == []


@pytest.mark.parametrize(
    "routes, message",
    [
        (mixer(strip("Lead Vocal"), strip("Verb", inputs=("Bus 3",))), "'Lead Vocal' does not send to 'Verb'; add the send first"),
        (mixer(strip("Lead Vocal", sends=("Bus 3",)), strip("Verb")), "'Lead Vocal' does not send to 'Verb'; add the send first"),
        (mixer(strip("Lead Vocal", sends=("Bus 2",)), strip("Verb", inputs=("Bus 3",))), "'Lead Vocal' does not send to 'Verb'"),
        (mixer(strip("Lead Vocal", sends=("Bus 3",))), "0 strips named 'Verb'"),
    ],
    ids=["no-send", "aux-on-no-bus", "send-to-another-bus", "no-aux-strip"],
)
def test_a_send_that_is_not_there_is_refused_before_anything_is_touched(fake, knob, routes, message):
    serve(fake)
    knob["routes"] = [routes]
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match=message):
        set_send_level(logic, "Lead Vocal", "Verb", -12)

    assert knob["steps"] == []


def test_a_target_that_is_not_an_aux_is_refused(fake, knob):
    serve(fake, [("Lead Vocal", "audio"), ("Verb", "audio")])
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="'Verb' is not an aux"):
        set_send_level(logic, "Lead Vocal", "Verb", -12)

    assert knob["steps"] == []


def test_a_step_that_fails_says_where_the_send_was_and_that_undo_cannot_restore_it(fake, knob):
    serve(fake)
    knob.update(routes=[SENT], levels=["-20.0"], landed=ExecutorError("the send knob stopped moving at -30.0 dB"))
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="stopped moving.*read -20.0 dB before.*undo does not restore it"):
        set_send_level(logic, "Lead Vocal", "Verb", -12)

    assert fake.log()[-1]["event"] == "set_send_level.start"


@pytest.mark.parametrize(
    "after, level",
    [
        (SENT, "-11.0"),
        (mixer(strip("Lead Vocal", sends=("Bus 3",)), strip("Verb", inputs=("Bus 4",))), "-12.0"),
    ],
    ids=["level-moved-again", "route-changed"],
)
def test_a_send_that_does_not_read_back_as_stepped_is_not_confirmed(fake, knob, after, level):
    serve(fake)
    knob.update(routes=[SENT, after], levels=["-∞", level], landed="-12.0")
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="read -∞ dB before: check it in the Mixer|could not be read back"):
        set_send_level(logic, "Lead Vocal", "Verb", -12)

    assert fake.log()[-1]["event"] == "set_send_level.start"


def test_a_project_switched_while_stepping_is_not_confirmed(fake, knob):
    serve(fake, project=[{"data": {"filePath": PROJECT}}, {"data": {"filePath": "/Users/me/Music/Real Song.logicx"}}])
    knob.update(routes=[SENT, SENT], levels=["-∞", "-12.0"], landed="-12.0")
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="Real Song.*could not be read back"):
        set_send_level(logic, "Lead Vocal", "Verb", -12)

    assert fake.log()[-1]["event"] == "set_send_level.start"


def test_a_knob_that_reads_something_other_than_a_level_is_refused(fake, knob):
    serve(fake)
    knob.update(routes=[SENT], levels=["loud"])
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="reads 'loud', not a level in dB"):
        set_send_level(logic, "Lead Vocal", "Verb", -12)

    assert knob["steps"] == []
