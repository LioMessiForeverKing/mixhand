import json
from types import SimpleNamespace

import pytest
from openai.types.responses import Response
from test_validate import session

from mixhand.executor import ActionResult, ExecutorError
from mixhand.executor.actionlog import LOG_PATH
from mixhand.executor.group import GROUP_PATH
from mixhand.planner import loop
from mixhand.planner.loop import PlannerError, produce


def reply(*output, status="completed", incomplete=None):
    items = [
        {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": o, "annotations": []}]}
        if isinstance(o, str)
        else o
        for o in output
    ]
    return Response.model_validate(
        {
            "id": "resp_1",
            "object": "response",
            "created_at": 0,
            "model": "gpt-6-sol",
            "status": status,
            "incomplete_details": {"reason": incomplete} if incomplete else None,
            "output": items,
            "parallel_tool_calls": False,
            "tool_choice": "auto",
            "tools": [],
        }
    )


def call(name, n=1, **args):
    arguments = json.dumps({**args, "reason": f"reason {n}"})
    return {"type": "function_call", "id": f"fc_{n}", "call_id": f"call_{n}", "name": name, "arguments": arguments, "status": "completed"}


class Stream:
    def __init__(self, response, ends=True):
        self.events = [
            SimpleNamespace(type="response.output_text.delta", delta=part.text)
            for o in response.output
            if o.type == "message"
            for part in o.content
            if part.type == "output_text"
        ]
        if ends:
            self.events.append(SimpleNamespace(type=f"response.{response.status}", response=response))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self.events)


class Client:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []
        self.responses = self

    def stream(self, **request):
        self.requests.append(json.loads(json.dumps(request, default=lambda item: item.model_dump(exclude_none=True))))
        reply = self.replies.pop(0)
        return reply if isinstance(reply, Stream) else Stream(reply)


@pytest.fixture
def logic(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    titles = iter(["Undo Rename Track", "Undo Create Tracks"])
    monkeypatch.setattr("mixhand.executor.group.undo_title", lambda: next(titles))
    monkeypatch.setattr("mixhand.executor.group.read_routes", lambda: "Lead Vocal\tInput 1\tStereo Out\t\t8")
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


def run(client, lines=None, typed=(), tracks=("Lead Vocal", "Double")):
    shown, lines, typed = [], [] if lines is None else lines, iter(typed)
    logic = SimpleNamespace(track_names=lambda: list(tracks))
    follow_up = lambda: next(typed, None)
    produce(logic, client, "make it bigger", session(), text=shown.append, line=lambda *shown_line: lines.append(shown_line), follow_up=follow_up)
    return "".join(shown)



def test_the_plan_streams_then_each_valid_action_runs_and_its_result_goes_back_to_the_model(logic):
    client = Client(
        reply("I'll double the lead.", call("duplicate_track", 1, source="Lead Vocal", new_name="Double")),
        reply(call("set_pan", 2, track="Double", value=-40)),
        reply("Doubled and panned."),
    )
    lines = []
    shown = run(client, lines)

    assert shown == "I'll double the lead.Doubled and panned."
    assert logic["ran"] == [
        ("duplicate_track", {"source": "Lead Vocal", "new_name": "Double"}),
        ("set_pan", {"track": "Double", "value": -40}),
    ]
    assert lines == [("pass", "duplicate_track done", "reason 1"), ("pass", "set_pan done", "reason 2")]
    sent = client.requests[1]["input"]
    assert [i.get("type") for i in sent[1:]] == ["message", "function_call", "function_call_output"]
    assert sent[-1] == {"type": "function_call_output", "call_id": "call_1", "output": "duplicate_track done"}
    assert client.requests[0]["model"] == loop.DEFAULT_MODEL
    assert client.requests[0]["parallel_tool_calls"] is False
    group = json.loads(GROUP_PATH.read_text())
    assert [(a["tool"], a["undo_steps"]) for a in group["actions"]] == [("duplicate_track", 2), ("set_pan", 0)]
    assert (group["undo_title_before"], group["undo_title_after"], group["failed"]) == ("Undo Rename Track", "Undo Create Tracks", None)
    assert (group["project"], group["tracks_after"]) == ("/tmp/x.logicx", ["Lead Vocal", "Double"])


def test_an_invalid_action_is_not_run_and_the_model_is_told_why(logic):
    client = Client(
        reply(call("set_pan", 1, track="Lead Vox", value=-40)),
        reply(call("set_pan", 2, track="Adlib", value=-40)),
        reply("Done."),
    )
    lines = []
    run(client, lines)

    assert logic["ran"] == [("set_pan", {"track": "Adlib", "value": -40})]
    refusal = client.requests[1]["input"][-1]
    assert refusal["call_id"] == "call_1" and "no track named 'Lead Vox'" in refusal["output"]
    assert lines[0][0] == "fail"


def test_three_invalid_actions_in_a_row_stop_the_run_with_nothing_run(logic):
    bad = [reply(call("set_pan", n, track="Adlib", value=99)) for n in range(3)]
    with pytest.raises(PlannerError, match="3 invalid actions in a row"):
        run(Client(*bad))
    assert logic["ran"] == []
    assert not GROUP_PATH.exists()


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


REFUSAL = {"type": "message", "id": "msg_2", "role": "assistant", "status": "completed", "content": [{"type": "refusal", "refusal": "no"}]}


@pytest.mark.parametrize(
    ("response", "refusal"),
    [
        (reply(call("set_pan", 1, track="Adlib", value=-40), status="incomplete", incomplete="max_output_tokens"), r"stopped short \(max_output_tokens\)"),
        (reply(call("set_pan", 1, track="Adlib", value=-40), status="failed"), r"stopped short \(failed\)"),
        (reply(REFUSAL, call("set_pan", 1, track="Adlib", value=-40)), "declined"),
    ],
    ids=["cut-off", "failed", "declined"],
)
def test_a_cut_off_or_declined_turn_runs_none_of_its_actions(logic, response, refusal):
    with pytest.raises(PlannerError, match=refusal):
        run(Client(response))
    assert logic["ran"] == []


def test_arguments_that_are_not_json_are_refused_and_sent_back(logic):
    broken = {**call("set_pan", 1, track="Adlib", value=-40), "arguments": '{"track": "Adlib", "value": -4'}
    client = Client(reply(broken), reply("Done.", status="completed"))
    run(client)
    assert logic["ran"] == []
    assert "not valid JSON" in client.requests[1]["input"][-1]["output"]


def test_a_blank_model_setting_falls_back_to_the_default(logic, monkeypatch):
    monkeypatch.setenv("MIXHAND_MODEL", "  ")
    client = Client(reply("Nothing to do."))
    run(client)
    assert client.requests[0]["model"] == loop.DEFAULT_MODEL


def test_a_valid_action_between_refusals_starts_the_count_again(logic):
    bad = lambda n: reply(call("set_pan", n, track="Adlib", value=99))
    client = Client(bad(1), reply(call("set_pan", 2, track="Adlib", value=9)), bad(3), bad(4), reply("Done."))
    run(client)
    assert logic["ran"] == [("set_pan", {"track": "Adlib", "value": 9})]


def test_a_reply_that_ends_without_a_final_event_runs_none_of_its_actions(logic):
    with pytest.raises(PlannerError, match="without a final response"):
        run(Client(Stream(reply(call("set_pan", 1, track="Adlib", value=-40)), ends=False)))
    assert logic["ran"] == []


def test_a_run_that_does_nothing_leaves_the_last_run_undoable(logic):
    GROUP_PATH.parent.mkdir()
    GROUP_PATH.write_text('{"label": "the run before"}')
    with pytest.raises(PlannerError):
        run(Client(reply(call("set_pan", 1, track="Adlib", value=-40), status="incomplete", incomplete="max_output_tokens")))
    assert GROUP_PATH.read_text() == '{"label": "the run before"}'


def test_a_run_of_only_no_op_actions_leaves_the_last_run_undoable(logic):
    GROUP_PATH.parent.mkdir()
    GROUP_PATH.write_text('{"label": "the run before"}')
    logic["primitive"]("insert_plugin")
    run(Client(reply(call("insert_plugin", 1, track="Adlib", plugin="Compressor")), reply("Already there.")))
    assert logic["ran"] == [("insert_plugin", {"track": "Adlib", "plugin": "Compressor"})]
    assert GROUP_PATH.read_text() == '{"label": "the run before"}'


def test_stopping_a_run_before_its_first_action_leaves_the_last_run_undoable(logic):
    GROUP_PATH.parent.mkdir()
    GROUP_PATH.write_text('{"label": "the run before"}')

    class Interrupted(Client):
        def stream(self, **request):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run(Interrupted())
    assert GROUP_PATH.read_text() == '{"label": "the run before"}'


def undo_titles(monkeypatch, *read):
    titles = iter(read)
    monkeypatch.setattr("mixhand.executor.group.undo_title", lambda: next(titles))


def test_a_follow_up_continues_the_conversation_and_the_same_undo_group(logic, monkeypatch):
    undo_titles(monkeypatch, "Undo Rename Track", "Undo Create Tracks", "Undo Create Tracks", "Undo Create Tracks")
    client = Client(
        reply("I'll double the lead.", call("duplicate_track", 1, source="Lead Vocal", new_name="Double")),
        reply("Doubled."),
        reply(call("set_pan", 2, track="Double", value=-40)),
        reply("Panned."),
    )
    shown = run(client, typed=["pan the double left"])

    assert shown == "I'll double the lead.Doubled.Panned."
    assert logic["ran"][1] == ("set_pan", {"track": "Double", "value": -40})
    before, turn_two = client.requests[1]["input"], client.requests[2]["input"]
    assert turn_two[: len(before)] == before
    assert turn_two[len(before)]["type"] == "message"
    assert turn_two[len(before) + 1 :] == [{"role": "user", "content": "pan the double left"}]
    group = json.loads(GROUP_PATH.read_text())
    assert [a["tool"] for a in group["actions"]] == ["duplicate_track", "set_pan"]
    assert (group["label"], group["undo_title_before"], group["undo_title_after"]) == ("make it bigger", "Undo Rename Track", "Undo Create Tracks")
    events = [json.loads(line) for line in LOG_PATH.read_text().splitlines()]
    assert [e["event"] for e in events].count("group.begin") == 1
    assert {"event": "planner.follow_up", "text": "pan the double left"}.items() <= next(e for e in events if e["event"] == "planner.follow_up").items()


def test_each_request_gets_its_own_action_budget(logic, monkeypatch):
    undo_titles(monkeypatch, "Undo Rename Track", *["Undo Create Tracks"] * 3)
    monkeypatch.setattr("mixhand.planner.validate.MAX_ACTIONS", 1)
    client = Client(
        reply(call("set_pan", 1, track="Adlib", value=-40)),
        reply("Done."),
        reply(call("set_pan", 2, track="Adlib", value=40)),
        reply("Done."),
    )
    lines = []
    run(client, lines, typed=["the other way"])
    assert [status for status, *_ in lines] == ["pass", "pass"]


def test_an_edit_in_logic_between_turns_stops_the_follow_up_and_keeps_the_last_turn_undoable(logic, monkeypatch):
    undo_titles(monkeypatch, "Undo Rename Track", "Undo Create Tracks", "Undo Volume")
    client = Client(reply(call("duplicate_track", 1, source="Lead Vocal", new_name="Double")), reply("Doubled."))
    before = None

    def typed():
        nonlocal before
        before = GROUP_PATH.read_text()
        yield "pan the double left"

    with pytest.raises(ExecutorError, match="Logic changed since Mixhand's last reply"):
        run(client, typed=typed())
    assert len(client.requests) == 2
    assert GROUP_PATH.read_text() == before
    assert json.loads(LOG_PATH.read_text().splitlines()[-1])["event"] == "planner.stopped"


def test_a_follow_up_after_a_turn_that_changed_nothing_rereads_where_logic_now_stands(logic, monkeypatch):
    undo_titles(monkeypatch, "Undo Rename Track", "Undo Volume", "Undo Create Tracks")
    client = Client(reply("Nothing to do yet."), reply(call("duplicate_track", 1, source="Lead Vocal", new_name="Double")), reply("Doubled."))
    run(client, typed=["double the lead"], tracks=("Lead Vocal", "Adlib", "Mine"))
    group = json.loads(GROUP_PATH.read_text())
    assert (group["undo_title_before"], group["tracks_before"]) == ("Undo Volume", ["Lead Vocal", "Adlib", "Mine"])
