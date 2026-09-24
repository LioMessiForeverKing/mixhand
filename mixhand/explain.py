import json
from dataclasses import dataclass, field
from datetime import datetime

from mixhand.executor.actionlog import LOG_PATH

SHOWN = ("planner.text", "planner.refused", "planner.action", "planner.stopped")


class Unexplained(Exception):
    pass


@dataclass
class Run:
    label: str
    began: datetime
    shown: list[dict] = field(default_factory=list)
    end: dict | None = None
    undo_began: datetime | None = None


def last_run() -> Run:
    lines = LOG_PATH.read_text().splitlines() if LOG_PATH.exists() else []
    after: list[dict] = []
    for n in range(len(lines) - 1, -1, -1):
        try:
            event = json.loads(lines[n])
        except ValueError:
            event = None
        if not isinstance(event, dict):
            raise Unexplained(f"line {n + 1} of {LOG_PATH} is not a logged event, so the last run cannot be read whole")
        if event.get("event") == "group.begin":
            return _run(event, reversed(after))
        after.append(event)
    raise Unexplained(f"{LOG_PATH} holds no Mixhand run yet")


def _run(begin: dict, after) -> Run:
    if begin.get("run") is None:
        raise Unexplained("the last run was logged by an older Mixhand, which did not record its reasons")
    run = Run(label=begin["label"], began=_time(begin))
    for event in after:
        if event.get("run") != begin["run"]:
            continue
        if event["event"] in SHOWN:
            run.shown.append(event)
        elif event["event"] == "group.end":
            run.end = event
        elif event["event"] == "group.undo.start":
            run.undo_began = _time(event)
    return run


def _time(event: dict) -> datetime:
    return datetime.fromisoformat(event["ts"]).astimezone()
