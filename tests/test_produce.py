import json

import pytest
from anthropic.types.beta import BetaMessage
from test_validate import session

from mixhand.executor import ActionResult, ExecutorError
from mixhand.executor.group import GROUP_PATH
from mixhand.planner import loop
from mixhand.planner.loop import PlannerError, produce


def reply(*content, stop_reason="tool_use"):
    blocks = [{"type": "text", "text": c} if isinstance(c, str) else c for c in content]
    return BetaMessage.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5",
            "content": blocks,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    )


def call(name, n=1, **args):
    return {"type": "tool_use", "id": f"toolu_{n}", "name": name, "input": {**args, "reason": f"reason {n}"}}


class Stream:
    def __init__(self, message):
        self.message = message
        self.text_stream = iter([b.text for b in message.content if b.type == "text"])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


class Client:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []
        self.beta = self
        self.messages = self

    def stream(self, **request):
        self.requests.append(json.loads(json.dumps(request, default=lambda b: b.model_dump())))
        return Stream(self.replies.pop(0))


@pytest.fixture
def logic(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    titles = iter(["Undo Rename Track", "Undo Create Tracks"])
    monkeypatch.setattr("mixhand.executor.group.undo_title", lambda: next(titles))
    ran = []

    def primitive(name, steps=0, fails=False):
        def run(logic, **args):
            ran.append((name, args))
            if fails:
                raise ExecutorError(f"{name} landed somewhere unexpected; check Logic before undoing")
            return ActionResult(ok=True, detail=f"{name} done", verified=True, undo_steps=steps, was=-3.0)

        monkeypatch.setitem(loop.EXECUTE, name, run)

    primitive("set_pan")
    primitive("duplicate_track", steps=2)
    return {"ran": ran, "primitive": primitive}


def run(client, lines=None):
    shown, lines = [], [] if lines is None else lines
    produce(None, client, "make it bigger", session(), text=shown.append, line=lambda s, d: lines.append((s, d)))
    return "".join(shown)


def test_the_plan_streams_then_each_valid_action_runs_and_its_result_goes_back_to_the_model(logic):
    client = Client(
        reply("I'll double the lead.", call("duplicate_track", 1, source="Lead Vocal", new_name="Double")),
        reply(call("set_pan", 2, track="Double", value=-40)),
        reply("Doubled and panned.", stop_reason="end_turn"),
    )
    lines = []
    shown = run(client, lines)

    assert shown == "I'll double the lead.Doubled and panned."
    assert logic["ran"] == [
        ("duplicate_track", {"source": "Lead Vocal", "new_name": "Double"}),
        ("set_pan", {"track": "Double", "value": -40}),
    ]
    assert lines == [("pass", "duplicate_track done — reason 1"), ("pass", "set_pan done — reason 2")]
    [result] = client.requests[1]["messages"][-1]["content"]
    assert result == {"type": "tool_result", "tool_use_id": "toolu_1", "content": "duplicate_track done"}
    assert client.requests[0]["model"] == loop.DEFAULT_MODEL
    assert client.requests[0]["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    group = json.loads(GROUP_PATH.read_text())
    assert [(a["tool"], a["undo_steps"]) for a in group["actions"]] == [("duplicate_track", 2), ("set_pan", 0)]
    assert (group["undo_title_before"], group["undo_title_after"], group["failed"]) == ("Undo Rename Track", "Undo Create Tracks", None)


def test_an_invalid_action_is_not_run_and_the_model_is_told_why(logic):
    client = Client(
        reply(call("set_pan", 1, track="Lead Vox", value=-40)),
        reply(call("set_pan", 2, track="Adlib", value=-40)),
        reply("Done.", stop_reason="end_turn"),
    )
    lines = []
    run(client, lines)

    assert logic["ran"] == [("set_pan", {"track": "Adlib", "value": -40})]
    [refusal] = client.requests[1]["messages"][-1]["content"]
    assert refusal["is_error"] is True and "no track named 'Lead Vox'" in refusal["content"]
    assert lines[0][0] == "fail"


def test_three_invalid_actions_in_a_row_stop_the_run_with_nothing_run(logic):
    bad = [reply(call("set_pan", n, track="Adlib", value=99)) for n in range(3)]
    with pytest.raises(PlannerError, match="3 invalid actions in a row"):
        run(Client(*bad))
    assert logic["ran"] == []
    assert json.loads(GROUP_PATH.read_text())["failed"] is None


def test_a_failed_action_stops_the_run_and_marks_the_group_so_undo_will_not_guess(logic):
    logic["primitive"]("set_pan", fails=True)
    client = Client(
        reply(call("duplicate_track", 1, source="Lead Vocal", new_name="Double")),
        reply(call("set_pan", 2, track="Double", value=-40)),
    )
    with pytest.raises(ExecutorError, match="landed somewhere unexpected"):
        run(client)
    group = json.loads(GROUP_PATH.read_text())
    assert [a["tool"] for a in group["actions"]] == ["duplicate_track"]
    assert "landed somewhere unexpected" in group["failed"]
    assert group["undo_title_after"] is None


@pytest.mark.parametrize("stop_reason", ["max_tokens", "refusal"])
def test_a_cut_off_or_declined_turn_runs_none_of_its_actions(logic, stop_reason):
    client = Client(reply(call("set_pan", 1, track="Adlib", value=-40), stop_reason=stop_reason))
    with pytest.raises(PlannerError):
        run(client)
    assert logic["ran"] == []


def test_a_blank_model_setting_falls_back_to_the_default(logic, monkeypatch):
    monkeypatch.setenv("MIXHAND_MODEL", "  ")
    client = Client(reply("Nothing to do.", stop_reason="end_turn"))
    run(client)
    assert client.requests[0]["model"] == loop.DEFAULT_MODEL


def test_a_valid_action_between_refusals_starts_the_count_again(logic):
    bad = lambda n: reply(call("set_pan", n, track="Adlib", value=99))
    client = Client(bad(1), reply(call("set_pan", 2, track="Adlib", value=9)), bad(3), bad(4), reply("Done.", stop_reason="end_turn"))
    run(client)
    assert logic["ran"] == [("set_pan", {"track": "Adlib", "value": 9})]
