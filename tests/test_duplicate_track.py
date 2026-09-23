import pytest

from mixhand.executor import ExecutorError
from mixhand.executor.logicpro import LogicPro
from mixhand.executor.primitives import DUPLICATE_MENU, duplicate_track

SOURCE = ("Lead Vocal", "trk_a")
COPY = ("Lead Vocal", "trk_b")
RENAMED = ("Lead Vocal Double", "trk_c")
SELECTED = {"state": "A", "verified": True}


def listing(*tracks):
    return {
        "readable": True,
        "source": "ax_live",
        "data": [{"id": i, "name": name, "track_ref": ref} for i, (name, ref) in enumerate(tracks)],
    }


def region(name, start="1 1 1 1", end="15 1 1 1"):
    return {"name": name, "startPosition": start, "endPosition": end}


def serve(fake, sequence, copy_regions=None, select=SELECTED):
    fake.serve(
        resources={
            "logic://tracks": [listing(*step) for step in sequence],
            "logic://tracks/0/regions": [[region("Lead Vocal #01")]],
            "logic://tracks/1/regions": [[region("Lead Vocal #01.1")] if copy_regions is None else copy_regions],
        },
        tools={"logic_tracks.select": [select]},
    )


@pytest.fixture
def clicks(monkeypatch):
    clicked = []
    monkeypatch.setattr("mixhand.executor.primitives.click_menu", lambda *path: clicked.append(path))
    monkeypatch.setattr("mixhand.executor.primitives.SETTLES_WITHIN_S", 0.3)
    return clicked


@pytest.fixture(autouse=True)
def names(monkeypatch):
    named = []
    monkeypatch.setattr("mixhand.executor.primitives.set_track_name", lambda *args: named.append(args))
    return named


def sent(fake, command):
    return [c["params"] for c in fake.calls() if c["call"] == f"logic_tracks.{command}"]


def test_a_track_is_duplicated_with_its_regions_and_renamed(fake, clicks, names):
    serve(fake, [[SOURCE], [SOURCE], [SOURCE, COPY], [SOURCE, COPY], [SOURCE, COPY], [SOURCE, RENAMED]])
    with LogicPro.from_env() as logic:
        result = duplicate_track(logic, "Lead Vocal", "Lead Vocal Double")

    assert result.ok and result.verified
    assert result.detail == "Duplicated Lead Vocal to Lead Vocal Double with 1 region"
    assert sent(fake, "select") == [{"index": 0, "target_ref": "trk_a"}]
    assert clicks == [DUPLICATE_MENU]
    assert names == [(2, "Lead Vocal", "Lead Vocal Double")]
    assert [c["call"] for c in fake.calls()] == ["logic_tracks.select"]
    assert [e["event"] for e in fake.log()] == ["duplicate_track.start", "duplicate_track.done"]


@pytest.mark.parametrize("new_name", ["Lead Vocal Double", "Lead Vocal"], ids=["taken", "the-source"])
def test_a_name_already_in_the_session_is_refused_before_anything_is_touched(fake, clicks, new_name):
    serve(fake, [[SOURCE, RENAMED]])
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="already exists"):
        duplicate_track(logic, "Lead Vocal", new_name)

    assert fake.calls() == [] and clicks == []


def test_a_click_that_failed_says_a_copy_may_exist(fake, monkeypatch):
    def slow(*path):
        raise ExecutorError("clicking Track > Other did not finish within 10s")

    monkeypatch.setattr("mixhand.executor.primitives.click_menu", slow)
    serve(fake, [[SOURCE]])
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="undo 1 if a copy was made"):
        duplicate_track(logic, "Lead Vocal", "Lead Vocal Double")


def test_an_unconfirmed_selection_duplicates_nothing(fake, clicks):
    serve(fake, [[SOURCE]], select={"state": "B", "verified": False, "reason": "readback_mismatch"})
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="selecting 'Lead Vocal' was not confirmed"):
        duplicate_track(logic, "Lead Vocal", "Lead Vocal Double")

    assert clicks == []


def test_a_copy_that_never_appears_says_so(fake, clicks, names):
    serve(fake, [[SOURCE]])
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="no copy of 'Lead Vocal' appeared"):
        duplicate_track(logic, "Lead Vocal", "Lead Vocal Double")

    assert names == []


def test_a_new_track_that_is_not_right_after_the_source_is_not_taken_for_the_copy(fake, clicks, names):
    serve(fake, [[SOURCE], [COPY, SOURCE]])
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="not one copy after it"):
        duplicate_track(logic, "Lead Vocal", "Lead Vocal Double")

    assert names == []


def test_a_copy_without_the_regions_is_not_renamed(fake, clicks, names):
    serve(fake, [[SOURCE], [SOURCE, COPY]], copy_regions=[])
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="does not carry its regions; undo 1"):
        duplicate_track(logic, "Lead Vocal", "Lead Vocal Double")

    assert names == []


def test_a_rename_that_lands_on_the_source_says_how_to_get_back(fake, clicks):
    swapped = [("Lead Vocal Double", "trk_x"), COPY]
    serve(fake, [[SOURCE], [SOURCE, COPY], [SOURCE, COPY], swapped])
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="did not land on the copy.*undo 2"):
        duplicate_track(logic, "Lead Vocal", "Lead Vocal Double")


def test_a_copy_that_moved_before_the_rename_is_not_renamed(fake, clicks, names):
    serve(fake, [[SOURCE], [SOURCE, COPY], [SOURCE, ("Lead Vocal", "trk_z")]])
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="moved before it could be renamed; check Logic"):
        duplicate_track(logic, "Lead Vocal", "Lead Vocal Double")

    assert names == []


def test_a_source_dragged_into_the_copys_place_before_the_rename_is_caught(fake, clicks):
    renamed_source = [COPY, ("Lead Vocal Double", "trk_x")]
    serve(fake, [[SOURCE], [SOURCE, COPY], [SOURCE, COPY], renamed_source])
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="did not land on the copy.*undo 2"):
        duplicate_track(logic, "Lead Vocal", "Lead Vocal Double")


def test_a_name_logic_refuses_is_not_typed_instead(fake, clicks, monkeypatch):
    def refuse(*args):
        raise ExecutorError("could not set track 2's name: the selected tracks are Track 1 “Lead Vocal”")

    monkeypatch.setattr("mixhand.executor.primitives.set_track_name", refuse)
    serve(fake, [[SOURCE], [SOURCE, COPY], [SOURCE, COPY]])
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="selected tracks are.*check Logic before undoing"):
        duplicate_track(logic, "Lead Vocal", "Lead Vocal Double")

    assert [c["call"] for c in fake.calls()] == ["logic_tracks.select"]
