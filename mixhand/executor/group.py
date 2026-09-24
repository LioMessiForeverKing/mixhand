import json
import uuid
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path

from mixhand.executor import ActionResult, ExecutorError
from mixhand.executor.actionlog import log
from mixhand.executor.ax import read_routes, undo_title
from mixhand.executor.logicpro import LogicPro, same_path
from mixhand.executor.primitives import set_pan, set_volume, undo

GROUP_PATH = Path("logs/group.json")
RESTORED = ("set_volume", "set_pan")


@dataclass
class Action:
    tool: str
    args: dict
    undo_steps: int
    was: float | str | None


@dataclass
class Group:
    label: str
    project: str
    tracks_before: list[str]
    undo_title_before: str
    actions: list[Action] = field(default_factory=list)
    undo_title_after: str | None = None
    tracks_after: list[str] | None = None
    routes_after: str | None = None
    failed: str | None = None
    run: str | None = None

    def save(self) -> None:
        GROUP_PATH.parent.mkdir(parents=True, exist_ok=True)
        GROUP_PATH.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False))


def begin_group(label: str, project: str, tracks_before: list[str]) -> Group:
    group = Group(label=label, project=project, tracks_before=tracks_before, undo_title_before=undo_title(), run=uuid.uuid4().hex)
    log("group.begin", run=group.run, label=label, undo_title=group.undo_title_before)
    return group


def record(group: Group, tool: str, args: dict, result: ActionResult) -> None:
    group.actions.append(Action(tool=tool, args=args, undo_steps=result.undo_steps, was=result.was))
    if _changed(group):
        group.save()


# A run that changed nothing undo tracks is not saved, so it cannot replace the last run that did.
def end_group(logic: LogicPro, group: Group, failed: str | None = None) -> bool:
    if not _changed(group):
        log("group.end", run=group.run, label=group.label, actions=len(group.actions), failed=failed, saved=False)
        return False
    group.failed = failed
    if failed is None:
        group.undo_title_after = undo_title()
        group.tracks_after = logic.track_names()
        group.routes_after = read_routes()
    group.save()
    log("group.end", run=group.run, label=group.label, actions=len(group.actions), failed=failed, saved=True, undo_title=group.undo_title_after)
    return True


def _changed(group: Group) -> bool:
    return any(a.undo_steps or a.tool in RESTORED for a in group.actions)


# A follow-up extends the run, so an edit made in Logic between turns would be undone as the run's own.
def resume_group(logic: LogicPro, group: Group) -> None:
    if group.undo_title_after is None:
        group.undo_title_before = undo_title()
        group.tracks_before = logic.track_names()
        return
    now = _now(logic)
    if now != (group.undo_title_after, group.tracks_after, group.routes_after):
        raise ExecutorError(
            f"Logic changed since Mixhand's last reply (its Undo reads {now[0]!r}, and the tracks or routing may differ), "
            "so a follow-up would fold that edit into this run's undo; start a new mixhand produce instead"
        )


def _now(logic: LogicPro) -> tuple[str, list[str], str]:
    return undo_title(), logic.track_names(), read_routes()


def load_group() -> Group | None:
    if not GROUP_PATH.exists():
        return None
    body = json.loads(GROUP_PATH.read_text())
    try:
        return Group(**{**body, "actions": [Action(**a) for a in body["actions"]]})
    except TypeError as e:
        raise ExecutorError(f"{GROUP_PATH} was written by an older Mixhand, so undo cannot trust it; undo that run in Logic by hand") from e


def undo_group(logic: LogicPro) -> Iterator[tuple[str, str]]:
    group = load_group()
    if group is None:
        raise ExecutorError("there is no Mixhand run to undo")
    if group.failed is not None or group.undo_title_after is None:
        raise ExecutorError(
            f"the last run stopped partway ({group.failed or 'it never finished'}), so Mixhand cannot tell how far "
            f"its last action got; undo it in Logic by hand. {_by_hand(group)}"
        )
    project = logic.require_project()
    if not same_path(project, group.project):
        raise ExecutorError(f"the last run was in {group.project}, not {project}; open that project to undo it")
    # An Undo title names an operation, not whose it was, so the tracks and routing must match too.
    now = _now(logic)
    if now != (group.undo_title_after, group.tracks_after, group.routes_after):
        raise ExecutorError(
            f"Logic changed after the run ended (its Undo reads {now[0]!r}, and the tracks or routing may differ), "
            f"so undo could take back someone else's edit; undo in Logic by hand. {_by_hand(group)}"
        )
    created = set(now[1]) - set(group.tracks_before)
    GROUP_PATH.unlink()
    log("group.undo.start", run=group.run, label=group.label)
    for action in _first_writes(group.actions):
        yield _restore(logic, action, created)
    steps = sum(a.undo_steps for a in group.actions)
    if steps:
        try:
            undo(logic, steps)
        except ExecutorError as e:
            raise ExecutorError(f"{e}; the run made {steps} undo steps, so check Logic's Edit menu before undoing the rest") from e
        yield "pass", f"Sent {steps} undo step{'' if steps == 1 else 's'}"
    yield _settled(group)


# Only a target's first write in the run holds the value from before it, so later writes are not restored.
def _first_writes(actions: list[Action]) -> list[Action]:
    first: dict[tuple, Action] = {}
    for a in actions:
        if a.tool in RESTORED:
            first.setdefault((a.tool, a.args["track"]), a)
    return list(first.values())


def _restore(logic: LogicPro, action: Action, created: set[str]) -> tuple[str, str]:
    track, what = action.args["track"], _what(action)
    if track in created:
        return "pass", f"{what} goes with the undo steps"
    if action.was is None:
        return "fail", f"{what} could not be read before the run, so it was not put back"
    try:
        if action.tool == "set_volume":
            result = set_volume(logic, track, action.was)
        else:
            result = set_pan(logic, track, action.was)
    except ExecutorError as e:
        return "fail", f"{e}; put {what} back to {action.was} by hand"
    return "pass", f"Put back: {result.detail}"


def _settled(group: Group) -> tuple[str, str]:
    try:
        now = undo_title()
    except ExecutorError as e:
        return "unknown", f"{e}; check that Logic's Undo reads {group.undo_title_before!r} again"
    if now == group.undo_title_before:
        return "pass", f"Logic's Undo reads {now!r}, as before the run"
    return "unknown", f"Logic's Undo reads {now!r}, not {group.undo_title_before!r} as before the run; Logic can lag after an undo (SETUP.md), so check it"


def _by_hand(group: Group) -> str:
    steps = sum(a.undo_steps for a in group.actions)
    values = [f"{_what(a)} (was {a.was})" for a in _first_writes(group.actions)]
    return f"Its finished actions made {steps} undo steps" + (f" and changed {', '.join(values)}" if values else "") + "."


def _what(action: Action) -> str:
    return f"{action.args['track']}'s {'volume' if action.tool == 'set_volume' else 'pan'}"
