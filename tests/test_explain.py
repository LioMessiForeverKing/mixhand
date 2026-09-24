import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from test_produce import Client, call, reply
from test_validate import session
from typer.testing import CliRunner

from mixhand.cli import FOLLOW_UP, NOTHING_TO_UNDO, app
from mixhand.executor import ActionResult, ExecutorError
from mixhand.executor.actionlog import LOG_PATH
from mixhand.planner import loop

TRACKS = ["Lead Vocal", "Double", "Adlib", "Verb"]
NO_END = "? The log has no end for this run: it is still going, or it stopped before it could write one."
FINISHED = [FOLLOW_UP, "› "]


@pytest.fixture
def logic(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fake = SimpleNamespace(track_names=lambda: TRACKS, require_project=lambda: "/tmp/x.logicx")

    @contextmanager
    def from_env():
        yield fake

    monkeypatch.setattr("mixhand.cli.LogicPro.from_env", from_env)
    monkeypatch.setattr("mixhand.cli.read_session", lambda logic, key, selection: session())
    monkeypatch.setattr("mixhand.cli._interactive", lambda: True)
    monkeypatch.setattr("mixhand.executor.group.undo_title", lambda: "Undo Create Tracks")
    monkeypatch.setattr("mixhand.executor.group.read_routes", lambda: "Lead Vocal\tInput 1\tStereo Out\t\t8")
    monkeypatch.setattr("mixhand.executor.group.undo", lambda logic, n: None)

    def primitive(name, detail, steps=0, fails=None):
        def run(logic, **args):
            if fails:
                raise ExecutorError(fails)
            return ActionResult(ok=True, detail=detail.format(**args), verified=True, undo_steps=steps, was=0)

        monkeypatch.setitem(loop.EXECUTE, name, run)

    primitive("duplicate_track", "Duplicated {source} → {new_name}", steps=2)
    primitive("set_pan", "Panned {track} to {value}")
    return primitive


def produce(monkeypatch, *replies, typed=""):
    monkeypatch.setattr("mixhand.cli.openai.OpenAI", lambda: Client(*replies))
    return CliRunner().invoke(app, ["produce", "make it bigger"], input=typed + "\n")


def explain():
    return CliRunner().invoke(app, ["explain"])


def doubled(n=1):
    return reply(f"I'll double the lead, take {n}.", call("duplicate_track", n, source="Lead Vocal", new_name="Double"))


def test_explain_reprints_exactly_what_produce_showed_and_produce_shows_no_json(logic, monkeypatch):
    shown = produce(
        monkeypatch,
        doubled(),
        reply(call("set_pan", 2, track="Lead Vox", value=-40)),
        reply(call("set_pan", 3, track="Double", value=-40)),
        reply("Doubled and panned."),
        reply(call("set_pan", 4, track="Double", value=-60)),
        reply("Wider."),
        typed="pan the double wider\n",
    )
    assert shown.exit_code == 0, shown.output
    lines = shown.stdout.splitlines()
    assert lines[:2] == ["I'll double the lead, take 1.", "✔ Duplicated Lead Vocal → Double — reason 1"]
    assert lines[2].startswith("✖ Refused set_pan: there is no track named 'Lead Vox'")
    assert lines[3:] == [
        "✔ Panned Double to -40 — reason 3",
        "Doubled and panned.",
        FOLLOW_UP,
        "› pan the double wider",
        "✔ Panned Double to -60 — reason 4",
        "Wider.",
        *FINISHED,
        "mixhand undo reverses this run.",
    ]

    explained = explain()
    assert explained.exit_code == 0, explained.output
    header, *replayed = explained.stdout.splitlines()
    assert header.startswith("Last run, ") and header.endswith(": make it bigger")
    assert replayed == lines[:-3]


def test_an_undone_run_says_undo_began_on_it(logic, monkeypatch):
    produce(monkeypatch, doubled(), reply("Done."))
    undone = CliRunner().invoke(app, ["undo"])
    assert undone.exit_code == 0, undone.output

    assert "mixhand undo began taking this run back at " in explain().stdout


def test_an_undo_of_an_earlier_run_is_not_pinned_on_the_last_one(logic, monkeypatch):
    produce(monkeypatch, doubled(), reply("Done."))
    idle = produce(monkeypatch, reply("Nothing needs changing."))
    assert idle.stdout.splitlines() == ["Nothing needs changing.", *FINISHED, NOTHING_TO_UNDO]
    assert CliRunner().invoke(app, ["undo"]).exit_code == 0

    explained = explain().stdout.splitlines()
    assert explained[1:] == ["Nothing needs changing.", NOTHING_TO_UNDO]


def test_a_run_stopped_by_a_failed_action_replays_the_failure(logic, monkeypatch):
    logic("set_pan", "", fails="Double's pan landed at -38; check Logic before undoing")
    shown = produce(monkeypatch, doubled(), reply(call("set_pan", 2, track="Double", value=-40)))
    assert shown.exit_code == 1

    replayed = explain().stdout.splitlines()
    assert replayed[1:] == [
        "I'll double the lead, take 1.",
        "✔ Duplicated Lead Vocal → Double — reason 1",
        "✖ Double's pan landed at -38; check Logic before undoing",
    ]


@pytest.mark.parametrize(
    "last", [reply("Done."), reply("Cut off.", status="incomplete", incomplete="max_output_tokens")], ids=["finished", "cut-off"]
)
def test_a_run_whose_end_could_not_be_logged_replays_the_error_produce_showed(logic, monkeypatch, last):
    def stalled():
        raise ExecutorError("Logic stopped answering")

    monkeypatch.setattr("mixhand.executor.group.read_routes", stalled)
    shown = produce(monkeypatch, doubled(), last)
    assert shown.exit_code == 1
    assert "✖ Logic stopped answering" in shown.stderr

    assert explain().stdout.splitlines()[-2:] == ["✖ Logic stopped answering", NO_END]


def test_explain_with_no_run_logged_says_so(logic):
    result = explain()
    assert result.exit_code == 1
    assert "holds no Mixhand run yet" in result.stderr


def test_a_run_logged_before_reasons_were_recorded_is_not_explained_as_empty(logic):
    LOG_PATH.parent.mkdir()
    LOG_PATH.write_text(json.dumps({"ts": "2026-09-24T17:00:00+00:00", "event": "group.begin", "label": "old", "undo_title": "Undo"}) + "\n")
    result = explain()
    assert result.exit_code == 1
    assert "older Mixhand" in result.stderr


def test_a_broken_line_inside_the_last_run_is_refused_but_one_before_it_is_not(logic, monkeypatch):
    produce(monkeypatch, doubled(1), reply("Done."))
    with LOG_PATH.open("a") as f:
        f.write('{"ts": "2026-09-24T17:00:00+00:00", "event": "planner.act\n')
    result = explain()
    assert result.exit_code == 1
    assert f"line {len(LOG_PATH.read_text().splitlines())} of logs/actions.jsonl" in result.stderr

    produce(monkeypatch, doubled(2), reply("Done."))
    assert explain().stdout.splitlines()[1] == "I'll double the lead, take 2."


def test_produce_asks_for_no_follow_up_without_a_terminal(logic, monkeypatch):
    monkeypatch.setattr("mixhand.cli._interactive", lambda: False)
    shown = produce(monkeypatch, doubled(), reply("Done."))
    assert shown.exit_code == 0, shown.output
    assert FOLLOW_UP not in shown.stdout
    assert shown.stdout.splitlines()[-1] == "mixhand undo reverses this run."


def test_a_run_killed_during_a_follow_up_is_not_explained_as_ended(logic, monkeypatch):
    produce(monkeypatch, doubled(), reply("Done."), reply(call("set_pan", 2, track="Double", value=-40)), reply("Panned."), typed="pan it\n")
    lines = LOG_PATH.read_text().splitlines()
    cut = next(n for n, line in enumerate(lines) if json.loads(line)["event"] == "planner.follow_up")
    LOG_PATH.write_text("\n".join(lines[: cut + 1]) + "\n")
    assert explain().stdout.splitlines()[-3:] == [FOLLOW_UP, "› pan it", NO_END]


def test_a_refused_follow_up_leaves_the_last_reply_explained_as_ended(logic, monkeypatch):
    titles = iter(["Undo Create Tracks", "Undo Create Tracks", "Undo Volume"])
    monkeypatch.setattr("mixhand.executor.group.undo_title", lambda: next(titles))
    shown = produce(monkeypatch, doubled(), reply("Done."), typed="pan it\n")
    assert shown.exit_code == 1
    replayed = explain().stdout.splitlines()
    assert replayed[-3:-1] == [FOLLOW_UP, "› pan it"]
    assert replayed[-1].startswith("✖ Logic changed since Mixhand's last reply")
