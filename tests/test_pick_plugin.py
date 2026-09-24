import pytest
from conftest import inventory, slot, tracks

from mixhand.executor import ExecutorError
from mixhand.executor.logicpro import LogicPro
from mixhand.executor.primitives import HIDE_PLUGIN_WINDOWS, SLOT_LABEL, insert_plugin

BEFORE = inventory(slot(0, "Channel EQ"), slot(1))


@pytest.fixture(autouse=True)
def menus(monkeypatch):
    done = {"picked": [], "clicked": []}
    monkeypatch.setattr("mixhand.executor.primitives.pick_plugin", lambda *path: done["picked"].append(path))
    monkeypatch.setattr("mixhand.executor.primitives.click_menu", lambda *path: done["clicked"].append(path))
    monkeypatch.setattr("mixhand.executor.primitives.SETTLES_WITHIN_S", 0.3)
    return done


def serve(fake, *inventories):
    fake.serve(
        resources={"logic://tracks": [tracks("Adlib", "Lead Vocal")]},
        tools={"logic_plugins.get_inventory": list(inventories)},
    )


def calls(fake, call):
    return [c["params"] for c in fake.calls() if c["call"] == call]


@pytest.mark.parametrize("plugin, category", [("ChromaVerb", "Reverb"), ("Stereo Delay", "Delay")])
def test_a_plugin_logicpromcp_cannot_insert_is_picked_from_the_menu_and_read_back(fake, menus, plugin, category):
    serve(fake, BEFORE, BEFORE, inventory(slot(0, "Channel EQ"), slot(1, SLOT_LABEL.get(plugin, plugin)), slot(2)))
    with LogicPro.from_env() as logic:
        result = insert_plugin(logic, "Lead Vocal", plugin)

    assert result.ok and result.verified
    assert result.detail == f"Inserted {plugin} on Lead Vocal slot 1"
    assert menus["picked"] == [("Lead Vocal", category, plugin, ("Stereo", "Mono->Stereo"))]
    assert menus["clicked"] == [HIDE_PLUGIN_WINDOWS]
    assert calls(fake, "logic_plugins.insert_verified") == []
    assert all(c == {"track": 1} for c in calls(fake, "logic_plugins.get_inventory"))
    assert [e["event"] for e in fake.log()] == ["insert_plugin.start", "insert_plugin.done"]


@pytest.mark.parametrize("plugin, label", [("ChromaVerb", "ChromaVerb"), ("Stereo Delay", "St-Delay")])
def test_a_second_call_picks_nothing(fake, menus, plugin, label):
    serve(fake, inventory(slot(0, label), slot(1)))
    with LogicPro.from_env() as logic:
        result = insert_plugin(logic, "Lead Vocal", plugin)

    assert result.verified and "already" in result.detail
    assert menus["picked"] == []


def test_a_plugin_mixhand_has_no_path_for_is_refused_before_anything_is_touched(fake, menus):
    serve(fake, BEFORE)
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="not 'Space Designer'"):
        insert_plugin(logic, "Lead Vocal", "Space Designer")

    assert fake.calls() == [] and menus["picked"] == []


def test_a_failed_pick_says_to_check_before_undoing(fake, monkeypatch):
    def refuse(*path):
        raise ExecutorError("the plug-in menu on Lead Vocal did not open")

    monkeypatch.setattr("mixhand.executor.primitives.pick_plugin", refuse)
    serve(fake, BEFORE)
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="undo 1 only if ChromaVerb is on it"):
        insert_plugin(logic, "Lead Vocal", "ChromaVerb")

    assert fake.log()[-1]["event"] == "insert_plugin.refused"


def test_a_pick_that_changes_nothing_times_out_without_claiming_an_insert(fake, menus):
    serve(fake, BEFORE)
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="no plugin appeared.*undo 1 only if"):
        insert_plugin(logic, "Lead Vocal", "ChromaVerb")

    assert menus["clicked"] == []


@pytest.mark.parametrize(
    "after",
    [
        inventory(slot(0, "Channel EQ"), slot(1, "St-Delay"), slot(2)),
        inventory(slot(0, "ChromaVerb"), slot(1)),
        inventory(slot(0, "Channel EQ"), slot(1, "ChromaVerb"), slot(2, "ChromaVerb"), slot(3)),
    ],
    ids=["wrong-plugin", "replaced-another", "two-added"],
)
def test_anything_but_the_plugin_added_on_the_empty_slot_is_not_confirmed(fake, menus, after):
    serve(fake, BEFORE, BEFORE, after)
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="check Logic before undoing"):
        insert_plugin(logic, "Lead Vocal", "ChromaVerb")

    assert menus["clicked"] == []


def test_a_plugin_window_left_open_does_not_undo_a_confirmed_insert(fake, monkeypatch):
    def stuck(*path):
        raise ExecutorError("could not click Window > Hide All Plug-in Windows")

    monkeypatch.setattr("mixhand.executor.primitives.click_menu", stuck)
    serve(fake, BEFORE, BEFORE, inventory(slot(0, "Channel EQ"), slot(1, "ChromaVerb"), slot(2)))
    with LogicPro.from_env() as logic:
        result = insert_plugin(logic, "Lead Vocal", "ChromaVerb")

    assert result.verified
    assert fake.log()[-1]["event"] == "insert_plugin.window_left_open"
