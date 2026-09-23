import pytest
from conftest import inventory, slot

from mixhand.executor import ExecutorError
from mixhand.executor.logicpro import LogicPro
from mixhand.executor.primitives import AUX_MENU, AUX_TRACK_MENU, create_aux

VOCAL = ("Lead Vocal", "trk_a", "audio")
NEW = ("Aux 1", "trk_b", "aux")
NAMED = ("Verb", "trk_b", "aux")
SELECTED = {"state": "A", "verified": True}
CHANNEL_EQ_ON_SLOT_0 = {"state": "A", "verified": True, "observed_plugin_name": "Channel EQ", "observed_slot": 0}


def listing(*tracks):
    return {
        "readable": True,
        "source": "ax_live",
        "data": [{"id": i, "name": name, "track_ref": ref, "type": kind} for i, (name, ref, kind) in enumerate(tracks)],
    }


def serve(fake, sequence, select=SELECTED, slots=(slot(0),), insert=CHANNEL_EQ_ON_SLOT_0):
    fake.serve(
        resources={"logic://tracks": [listing(*step) for step in sequence]},
        tools={
            "logic_tracks.select": [select],
            "logic_plugins.get_inventory": [inventory(*slots)],
            "logic_plugins.insert_verified": [insert],
        },
    )


@pytest.fixture(autouse=True)
def mixer(monkeypatch):
    clicked = []
    monkeypatch.setattr("mixhand.executor.primitives.click_mixer_menu", lambda *path: clicked.append(path))
    monkeypatch.setattr("mixhand.executor.primitives.require_mixer", lambda: None)
    monkeypatch.setattr("mixhand.executor.primitives.require_inspector", lambda: None)
    monkeypatch.setattr("mixhand.executor.primitives.SETTLES_WITHIN_S", 0.3)
    return clicked


@pytest.fixture(autouse=True)
def names(monkeypatch):
    named = []
    monkeypatch.setattr("mixhand.executor.primitives.set_track_name", lambda *args: named.append(args))
    return named


def sent(fake, call):
    return [c["params"] for c in fake.calls() if c["call"] == call]


def test_an_aux_is_created_given_a_track_renamed_and_given_its_plugin(fake, mixer, names):
    serve(fake, [[VOCAL], [VOCAL, NEW], [VOCAL, NEW], [VOCAL, NAMED]])
    with LogicPro.from_env() as logic:
        result = create_aux(logic, "Verb", "Channel EQ")

    assert result.ok and result.verified
    assert result.detail == "Created aux Verb with Channel EQ; undo 4 removes it"
    assert mixer == [AUX_MENU, AUX_TRACK_MENU]
    assert sent(fake, "logic_tracks.select") == [{"index": 1, "target_ref": "trk_b"}]
    assert names == [(2, "Aux 1", "Verb")]
    insert = sent(fake, "logic_plugins.insert_verified")
    assert [(i["track"], i["insert"], i["expected_name"]) for i in insert] == [(1, 0, "Verb")]
    assert [e["event"] for e in fake.log()] == [
        "create_aux.start",
        "insert_plugin.start",
        "insert_plugin.done",
        "create_aux.done",
    ]


def test_a_plugin_logicpromcp_cannot_insert_is_refused_before_anything_is_touched(fake, mixer, names):
    serve(fake, [[VOCAL]])
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="cannot carry 'ChromaVerb' yet"):
        create_aux(logic, "Verb", "ChromaVerb")

    assert fake.calls() == [] and mixer == [] and names == []


def test_a_second_call_creates_nothing_and_leaves_the_plugin_alone(fake, mixer, names):
    serve(fake, [[VOCAL, NAMED]], slots=(slot(0, "Channel EQ"), slot(1)))
    with LogicPro.from_env() as logic:
        result = create_aux(logic, "Verb", "Channel EQ")

    assert result.verified
    assert result.detail == "Aux Verb already exists; Channel EQ is already on Verb slot 0"
    assert mixer == [] and names == []
    assert sent(fake, "logic_plugins.insert_verified") == []


def test_an_existing_aux_missing_its_plugin_gets_it_without_a_second_aux(fake, mixer, names):
    serve(fake, [[VOCAL, NAMED]])
    with LogicPro.from_env() as logic:
        result = create_aux(logic, "Verb", "Channel EQ")

    assert result.verified and result.detail.startswith("Aux Verb already exists; Inserted Channel EQ")
    assert mixer == [] and names == []


def test_a_track_of_that_name_that_is_not_an_aux_is_refused(fake, mixer, names):
    serve(fake, [[VOCAL, ("Verb", "trk_c", "audio")]])
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="already exists and is not an aux"):
        create_aux(logic, "Verb", "Channel EQ")

    assert fake.calls() == [] and mixer == []


@pytest.mark.parametrize(
    "missing, message",
    [("require_mixer", "found 0 Mixers"), ("require_inspector", "show the Inspector")],
    ids=["mixer", "inspector"],
)
def test_a_hidden_pane_stops_before_anything_is_touched(fake, mixer, names, monkeypatch, missing, message):
    def hidden():
        raise ExecutorError(message)

    monkeypatch.setattr(f"mixhand.executor.primitives.{missing}", hidden)
    serve(fake, [[VOCAL]])
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match=message):
        create_aux(logic, "Verb", "Channel EQ")

    assert fake.calls() == [] and mixer == [] and names == []


def test_a_strip_that_could_not_be_given_a_track_says_how_to_remove_it(fake, names, monkeypatch):
    def disabled(*path):
        if path == AUX_TRACK_MENU:
            raise ExecutorError("Options > Create Tracks for Selected Channel Strips is disabled in the Mixer")

    monkeypatch.setattr("mixhand.executor.primitives.click_mixer_menu", disabled)
    serve(fake, [[VOCAL]])
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="undo 1 if the new aux strip has no track, undo 2 if it has one"):
        create_aux(logic, "Verb", "Channel EQ")

    assert names == []


def test_an_aux_track_that_never_appears_says_so(fake, names):
    serve(fake, [[VOCAL]])
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="no aux track appeared"):
        create_aux(logic, "Verb", "Channel EQ")

    assert names == []


@pytest.mark.parametrize(
    "after",
    [[VOCAL, ("Audio 2", "trk_b", "audio")], [VOCAL, NEW, ("Aux 2", "trk_c", "aux")], [NEW, ("Lead Vocal", "trk_z", "audio")]],
    ids=["not-an-aux", "two-new", "others-changed"],
)
def test_anything_but_exactly_one_new_aux_is_not_renamed(fake, names, after):
    serve(fake, [[VOCAL], after])
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="not one new aux track"):
        create_aux(logic, "Verb", "Channel EQ")

    assert names == []


def test_an_unconfirmed_selection_renames_nothing(fake, names):
    serve(fake, [[VOCAL], [VOCAL, NEW]], select={"state": "B", "verified": False, "reason": "readback_mismatch"})
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="selecting the new aux was not confirmed.*undo 2"):
        create_aux(logic, "Verb", "Channel EQ")

    assert names == []


def test_an_aux_that_moved_before_the_rename_is_not_renamed(fake, names):
    serve(fake, [[VOCAL], [VOCAL, NEW], [NEW, VOCAL]])
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="moved before it could be renamed"):
        create_aux(logic, "Verb", "Channel EQ")

    assert names == []


@pytest.mark.parametrize(
    "renamed",
    [[("Verb", "trk_a", "audio"), NEW], [("Verb", "trk_x", "audio"), NEW]],
    ids=["other-track-renamed", "other-track-replaced"],
)
def test_a_rename_that_lands_elsewhere_says_how_to_get_back(fake, renamed):
    serve(fake, [[VOCAL], [VOCAL, NEW], [VOCAL, NEW], renamed])
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="did not land on the new aux.*undo 3"):
        create_aux(logic, "Verb", "Channel EQ")


def test_a_failed_insert_leaves_a_named_aux_and_says_a_rerun_finishes_it(fake):
    refused = {"state": "C", "error": "slot_popup_menu_not_found", "safe_to_retry": False}
    serve(fake, [[VOCAL], [VOCAL, NEW], [VOCAL, NEW], [VOCAL, NAMED]], insert=refused)
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="may or may not be on it.*undo 3 to remove it without the plugin, 4 with it"):
        create_aux(logic, "Verb", "Channel EQ")


def test_an_aux_asked_for_by_the_name_logic_gives_it_is_not_renamed(fake, names):
    serve(fake, [[VOCAL], [VOCAL, NEW]])
    with LogicPro.from_env() as logic:
        result = create_aux(logic, "Aux 1", "Channel EQ")

    assert result.verified
    assert result.detail == "Created aux Aux 1 with Channel EQ; undo 3 removes it"
    assert names == []
    insert = sent(fake, "logic_plugins.insert_verified")
    assert [(i["track"], i["expected_name"]) for i in insert] == [(1, "Aux 1")]


def test_a_new_aux_without_a_track_ref_is_not_selected(fake, names):
    serve(fake, [[VOCAL], [VOCAL, ("Aux 1", None, "aux")]])
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="no track_ref for the new aux"):
        create_aux(logic, "Verb", "Channel EQ")

    assert sent(fake, "logic_tracks.select") == [] and names == []
