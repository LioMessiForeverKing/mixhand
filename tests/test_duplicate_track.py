import pytest

from mixhand.executor import ExecutorError
from mixhand.executor.logicpro import LogicPro
from mixhand.executor.primitives import DUPLICATE_MENU, duplicate_track

SOURCE = ("Lead Vocal", "trk_a")
COPY = ("Lead Vocal", "trk_b")
RENAMED = ("Lead Vocal Double", "trk_c")
SELECTED = {"state": "A", "verified": True}
RENAME_CONFIRMED = {"state": "A", "verified": True, "observed": "Lead Vocal Double"}


def listing(*tracks):
    return {
        "readable": True,
        "source": "ax_live",
        "data": [{"id": i, "name": name, "track_ref": ref} for i, (name, ref) in enumerate(tracks)],
    }


def region(name, start="1 1 1 1", end="15 1 1 1"):
    return {"name": name, "startPosition": start, "endPosition": end}


def serve(fake, sequence, copy_regions=None, select=SELECTED, rename=RENAME_CONFIRMED):
    fake.serve(
        resources={
            "logic://tracks": [listing(*step) for step in sequence],
            "logic://tracks/0/regions": [[region("Lead Vocal #01")]],
            "logic://tracks/1/regions": [[region("Lead Vocal #01.1")] if copy_regions is None else copy_regions],
        },
        tools={"logic_tracks.select": [select], "logic_tracks.rename": [rename]},
    )


@pytest.fixture
def clicks(monkeypatch):
    clicked = []
    monkeypatch.setattr("mixhand.executor.primitives.click_menu", lambda *path: clicked.append(path))
    monkeypatch.setattr("mixhand.executor.primitives.SETTLES_WITHIN_S", 0.3)
    return clicked


def sent(fake, command):
    return [c["params"] for c in fake.calls() if c["call"] == f"logic_tracks.{command}"]


def test_a_track_is_duplicated_with_its_regions_and_renamed(fake, clicks):
    serve(fake, [[SOURCE], [SOURCE], [SOURCE, COPY], [SOURCE, COPY], [SOURCE, COPY], [SOURCE, RENAMED]])
    with LogicPro.from_env() as logic:
        result = duplicate_track(logic, "Lead Vocal", "Lead Vocal Double")

    assert result.ok and result.verified
    assert result.detail == "Duplicated Lead Vocal to Lead Vocal Double with 1 region"
    assert sent(fake, "select") == [{"index": 0, "target_ref": "trk_a"}]
    assert clicks == [DUPLICATE_MENU]
    assert sent(fake, "rename") == [{"index": 1, "name": "Lead Vocal Double"}]
    assert [e["event"] for e in fake.log()] == ["duplicate_track.start", "duplicate_track.done"]


def test_a_second_call_finds_the_copy_and_makes_no_other(fake, clicks):
    serve(fake, [[SOURCE, RENAMED]])
    with LogicPro.from_env() as logic:
        result = duplicate_track(logic, "Lead Vocal", "Lead Vocal Double")

    assert result.verified and result.detail == "Lead Vocal Double already duplicates Lead Vocal"
    assert fake.calls() == [] and clicks == []


def test_a_track_already_holding_the_new_name_that_is_not_a_copy_is_refused(fake, clicks):
    serve(fake, [[SOURCE, RENAMED]], copy_regions=[region("Adlib", start="9 1 1 1")])
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="already exists and does not carry"):
        duplicate_track(logic, "Lead Vocal", "Lead Vocal Double")

    assert fake.calls() == [] and clicks == []


def test_an_unconfirmed_selection_duplicates_nothing(fake, clicks):
    serve(fake, [[SOURCE]], select={"state": "B", "verified": False, "reason": "readback_mismatch"})
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="selecting 'Lead Vocal' was not confirmed"):
        duplicate_track(logic, "Lead Vocal", "Lead Vocal Double")

    assert clicks == []


def test_a_copy_that_never_appears_says_so(fake, clicks):
    serve(fake, [[SOURCE]])
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="no copy of 'Lead Vocal' appeared"):
        duplicate_track(logic, "Lead Vocal", "Lead Vocal Double")

    assert sent(fake, "rename") == []


def test_a_new_track_that_is_not_right_after_the_source_is_not_taken_for_the_copy(fake, clicks):
    serve(fake, [[SOURCE], [COPY, SOURCE]])
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="not one copy after it"):
        duplicate_track(logic, "Lead Vocal", "Lead Vocal Double")

    assert sent(fake, "rename") == []


def test_a_copy_without_the_regions_is_not_renamed(fake, clicks):
    serve(fake, [[SOURCE], [SOURCE, COPY]], copy_regions=[])
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="does not carry its regions; undo 1"):
        duplicate_track(logic, "Lead Vocal", "Lead Vocal Double")

    assert sent(fake, "rename") == []


def test_a_rename_that_lands_on_the_source_says_how_to_get_back(fake, clicks):
    swapped = [("Lead Vocal Double", "trk_x"), COPY]
    serve(fake, [[SOURCE], [SOURCE, COPY], [SOURCE, COPY], swapped])
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="did not land on the copy.*undo 2"):
        duplicate_track(logic, "Lead Vocal", "Lead Vocal Double")


def test_a_copy_that_moved_before_the_rename_is_not_renamed(fake, clicks):
    serve(fake, [[SOURCE], [SOURCE, COPY], [SOURCE, ("Lead Vocal", "trk_z")]])
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="moved before it could be renamed; undo 1"):
        duplicate_track(logic, "Lead Vocal", "Lead Vocal Double")

    assert sent(fake, "rename") == []


def test_a_source_dragged_into_the_copys_place_before_the_rename_is_caught(fake, clicks):
    renamed_source = [COPY, ("Lead Vocal Double", "trk_x")]
    serve(fake, [[SOURCE], [SOURCE, COPY], [SOURCE, COPY], renamed_source])
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="did not land on the copy.*undo 2"):
        duplicate_track(logic, "Lead Vocal", "Lead Vocal Double")
