import pytest

from mixhand.executor import ExecutorError
from mixhand.executor.logicpro import LogicPro
from mixhand.executor.primitives import delete_track

LEAD = ("Lead Vocal", "trk_a")
DOUBLE = ("Lead Vocal Double", "trk_b")
BASS = ("Bass", "trk_c")
DELETED = {"state": "A", "verified": True, "trace_id": "t1"}


def listing(*tracks):
    return {
        "readable": True,
        "source": "ax_live",
        "data": [{"id": i, "name": name, "track_ref": ref} for i, (name, ref) in enumerate(tracks)],
    }


def reissued(*tracks):
    return [(name, f"{ref}_new") for name, ref in tracks]


def serve(fake, sequence, delete=DELETED):
    fake.serve(
        resources={"logic://tracks": [listing(*step) for step in sequence]},
        tools={"logic_tracks.delete": [delete]},
    )


@pytest.fixture(autouse=True)
def settles(monkeypatch):
    monkeypatch.setattr("mixhand.executor.primitives.SETTLES_WITHIN_S", 0.3)


def sent(fake):
    return [c["params"] for c in fake.calls() if c["call"] == "logic_tracks.delete"]


def test_the_named_track_is_deleted_by_its_ref(fake):
    serve(fake, [[LEAD, DOUBLE, BASS], reissued(LEAD, DOUBLE, BASS), reissued(LEAD, BASS)])
    with LogicPro.from_env() as logic:
        result = delete_track(logic, "Lead Vocal Double")

    assert result.ok and result.verified
    assert result.detail == "Deleted Lead Vocal Double; undo 1 restores it"
    assert sent(fake) == [{"index": 1, "target_ref": "trk_b"}]
    assert [e["event"] for e in fake.log()] == ["delete_track.start", "delete_track.done"]


@pytest.mark.parametrize(
    "session",
    [[LEAD, BASS], [LEAD, DOUBLE, ("Lead Vocal Double", "trk_x")]],
    ids=["absent", "not-unique"],
)
def test_a_name_that_is_not_exactly_one_track_deletes_nothing(fake, session):
    serve(fake, [session])
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="no track named|must be unique"):
        delete_track(logic, "Lead Vocal Double")

    assert fake.calls() == []


def test_a_track_without_a_ref_is_not_deleted_by_position(fake):
    fake.serve(
        resources={"logic://tracks": [{"readable": True, "source": "ax_live", "data": [{"id": 0, "name": "Lead Vocal"}]}]}
    )
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="no track_ref"):
        delete_track(logic, "Lead Vocal")

    assert fake.calls() == []


def test_a_refusal_from_logicpromcp_is_logged_and_raised(fake):
    serve(fake, [[LEAD, DOUBLE]], delete={"state": "C", "error": "stale_target_reference"})
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="stale_target_reference"):
        delete_track(logic, "Lead Vocal Double")

    assert fake.log()[-1]["event"] == "delete_track.refused"
    assert fake.log()[-1]["error"] == "stale_target_reference"


def test_an_unconfirmed_delete_says_to_check_logic_rather_than_undo(fake):
    serve(fake, [[LEAD, DOUBLE]], delete={"state": "B", "verified": False, "reason": "retry_exhausted"})
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="not confirmed.*retry_exhausted.*check Logic"):
        delete_track(logic, "Lead Vocal Double")


def test_a_track_that_never_disappears_says_so(fake):
    serve(fake, [[LEAD, DOUBLE], reissued(LEAD, DOUBLE)])
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="still shows 'Lead Vocal Double'"):
        delete_track(logic, "Lead Vocal Double")


def test_a_different_track_going_missing_is_not_reported_as_the_delete(fake):
    serve(fake, [[LEAD, DOUBLE, BASS], reissued(LEAD, DOUBLE)])
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="not the session without it"):
        delete_track(logic, "Lead Vocal Double")

    assert fake.log()[-1]["event"] == "delete_track.start"


def test_a_replacement_track_logic_added_is_not_reported_as_the_delete(fake):
    serve(fake, [[DOUBLE], [("Audio 1", "trk_new")]])
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="not the session without it"):
        delete_track(logic, "Lead Vocal Double")
