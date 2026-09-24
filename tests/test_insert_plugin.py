import pytest
from conftest import inventory, slot, tracks

from mixhand.executor import ExecutorError
from mixhand.executor.logicpro import LogicPro
from mixhand.executor.primitives import insert_plugin, set_pan, set_volume, undo

CHANNEL_EQ_ON_SLOT_1 = {
    "state": "A",
    "verified": True,
    "observed_plugin_name": "Channel EQ",
    "observed_slot": 1,
    "trace_id": "t1",
}


def test_inserts_into_the_first_empty_slot_of_the_named_track(fake):
    fake.serve(
        resources={"logic://tracks": [tracks(readable=False), tracks("Adlib", "Lead Vocal")]},
        tools={
            "logic_plugins.get_inventory": [inventory(slot(0, "Compressor"), slot(1))],
            "logic_plugins.insert_verified": [CHANNEL_EQ_ON_SLOT_1],
        },
    )
    with LogicPro.from_env() as logic:
        result = insert_plugin(logic, "Lead Vocal", "Channel EQ")

    assert result.ok and result.verified
    assert result.undo_steps == 1
    insert = [c for c in fake.calls() if c["call"] == "logic_plugins.insert_verified"]
    assert len(insert) == 1
    assert insert[0]["params"]["track"] == 1
    assert insert[0]["params"]["insert"] == 1
    assert insert[0]["params"]["expected_name"] == "Lead Vocal"
    assert [e["event"] for e in fake.log()] == ["insert_plugin.start", "insert_plugin.done"]


def test_a_second_call_inserts_nothing(fake):
    fake.serve(
        resources={"logic://tracks": [tracks("Lead Vocal")]},
        tools={"logic_plugins.get_inventory": [inventory(slot(0, "Channel EQ"), slot(1))]},
    )
    with LogicPro.from_env() as logic:
        result = insert_plugin(logic, "Lead Vocal", "Channel EQ")

    assert result.verified
    assert "already" in result.detail
    assert "logic_plugins.insert_verified" not in [c["call"] for c in fake.calls()]


def test_an_unreadable_track_list_is_not_reported_as_a_missing_track(fake, monkeypatch):
    monkeypatch.setattr("mixhand.executor.logicpro.TRACKS_READABLE_WITHIN_S", 0.05)
    fake.serve(resources={"logic://tracks": [tracks("Track 1", readable=False)]})
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="not readable"):
        insert_plugin(logic, "Lead Vocal", "Channel EQ")


def test_a_missing_track_names_what_logic_has(fake):
    fake.serve(resources={"logic://tracks": [tracks("Adlib")]})
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="no track named 'Lead Vocal'.*Adlib"):
        insert_plugin(logic, "Lead Vocal", "Channel EQ")


@pytest.mark.parametrize(
    "reply",
    [
        {"state": "B", "verified": False, "reason": "readback_unavailable"},
        {**CHANNEL_EQ_ON_SLOT_1, "observed_slot": 0, "verified": False},
        {**CHANNEL_EQ_ON_SLOT_1, "observed_slot": 0, "observed_plugin_name": "Compressor"},
        CHANNEL_EQ_ON_SLOT_1,
    ],
    ids=["state-b", "state-a-unverified", "state-a-wrong-plugin", "state-a-wrong-slot"],
)
def test_an_unconfirmed_insert_raises_and_logs_it_unverified(fake, reply):
    fake.serve(
        resources={"logic://tracks": [tracks("Lead Vocal")]},
        tools={
            "logic_plugins.get_inventory": [inventory(slot(0))],
            "logic_plugins.insert_verified": [reply],
        },
    )
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="not confirmed"):
        insert_plugin(logic, "Lead Vocal", "Channel EQ")

    assert fake.log()[-1]["verified"] is False


def test_nothing_is_touched_when_another_project_is_in_front(fake):
    fake.serve(resources={"logic://project/info": [{"data": {"filePath": "/Users/me/Music/Real Song.logicx"}}]})
    with LogicPro.from_env() as logic:
        with pytest.raises(ExecutorError, match="Real Song"):
            insert_plugin(logic, "Lead Vocal", "Channel EQ")
        with pytest.raises(ExecutorError, match="Real Song"):
            undo(logic)
        with pytest.raises(ExecutorError, match="Real Song"):
            set_volume(logic, "Lead Vocal", -3.0)
        with pytest.raises(ExecutorError, match="Real Song"):
            set_pan(logic, "Lead Vocal", -40)

    assert fake.calls() == []


MENU_NOT_FOUND = {"state": "C", "error": "insert_setup_failed", "setup_stage": "slot_popup_menu_not_found"}


def test_a_refusal_marked_safe_to_retry_is_retried_after_rereading_the_slots(fake):
    fake.serve(
        resources={"logic://tracks": [tracks("Adlib", "Lead Vocal")]},
        tools={
            "logic_plugins.get_inventory": [inventory(slot(0, "Compressor"), slot(1))],
            "logic_plugins.insert_verified": [{**MENU_NOT_FOUND, "safe_to_retry": True}, CHANNEL_EQ_ON_SLOT_1],
        },
    )
    with LogicPro.from_env() as logic:
        result = insert_plugin(logic, "Lead Vocal", "Channel EQ")

    assert result.verified
    calls = [c["call"] for c in fake.calls()]
    assert calls.count("logic_plugins.insert_verified") == 2
    assert calls.count("logic_plugins.get_inventory") == 2


def test_a_refusal_not_marked_safe_to_retry_is_not_retried(fake):
    fake.serve(
        resources={"logic://tracks": [tracks("Lead Vocal")]},
        tools={
            "logic_plugins.get_inventory": [inventory(slot(0))],
            "logic_plugins.insert_verified": [{**MENU_NOT_FOUND, "safe_to_retry": False}, CHANNEL_EQ_ON_SLOT_1],
        },
    )
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="slot_popup_menu_not_found"):
        insert_plugin(logic, "Lead Vocal", "Channel EQ")

    assert [c["call"] for c in fake.calls()].count("logic_plugins.insert_verified") == 1


def test_it_gives_up_after_three_refusals(fake):
    fake.serve(
        resources={"logic://tracks": [tracks("Lead Vocal")]},
        tools={
            "logic_plugins.get_inventory": [inventory(slot(0))],
            "logic_plugins.insert_verified": [{**MENU_NOT_FOUND, "safe_to_retry": True}],
        },
    )
    with LogicPro.from_env() as logic, pytest.raises(ExecutorError, match="slot_popup_menu_not_found"):
        insert_plugin(logic, "Lead Vocal", "Channel EQ")

    assert [c["call"] for c in fake.calls()].count("logic_plugins.insert_verified") == 3
