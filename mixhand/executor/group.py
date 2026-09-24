import json
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path

from mixhand.executor import ActionResult, ExecutorError
from mixhand.executor.actionlog import log
from mixhand.executor.ax import undo_title
from mixhand.executor.logicpro import LogicPro
from mixhand.executor.primitives import set_pan, set_send_level, set_volume, undo

GROUP_PATH = Path("logs/group.json")
RESTORED = ("set_volume", "set_pan", "set_send_level")


@dataclass
class Action:
    tool: str
    args: dict
    undo_steps: int
    was: float | str | None


@dataclass
class Group:
    label: str
    tracks_before: list[str]
    undo_title_before: str
    actions: list[Action] = field(default_factory=list)
    undo_title_after: str | None = None
    failed: str | None = None

    def save(self) -> None:
        GROUP_PATH.parent.mkdir(parents=True, exist_ok=True)
        GROUP_PATH.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False))


def begin_group(label: str, tracks_before: list[str]) -> Group:
    group = Group(label=label, tracks_before=tracks_before, undo_title_before=undo_title())
    group.save()
    log("group.begin", label=label, undo_title=group.undo_title_before)
    return group


def record(group: Group, tool: str, args: dict, result: ActionResult) -> None:
    group.actions.append(Action(tool=tool, args=args, undo_steps=result.undo_steps, was=result.was))
    group.save()


def end_group(group: Group, failed: str | None = None) -> None:
    group.failed = failed
    if failed is None:
        group.undo_title_after = undo_title()
    group.save()
    log("group.end", label=group.label, actions=len(group.actions), failed=failed, undo_title=group.undo_title_after)


def load_group() -> Group | None:
    if not GROUP_PATH.exists():
        return None
    body = json.loads(GROUP_PATH.read_text())
    return Group(**{**body, "actions": [Action(**a) for a in body["actions"]]})


def undo_group(logic: LogicPro) -> Iterator[tuple[str, str]]:
    group = load_group()
    if group is None:
        raise ExecutorError("there is no Mixhand run to undo")
    if group.failed is not None or group.undo_title_after is None:
        raise ExecutorError(
            f"the last run stopped partway ({group.failed or 'it never finished'}), so Mixhand cannot tell how far "
            f"its last action got; undo it in Logic by hand. {_by_hand(group)}"
        )
    logic.require_project()
    now = undo_title()
    if now != group.undo_title_after:
        raise ExecutorError(
            f"Logic's Undo reads {now!r}, not {group.undo_title_after!r} as when the run ended, "
            f"so Logic changed since; undo in Logic by hand. {_by_hand(group)}"
        )
    GROUP_PATH.unlink()
    log("group.undo.start", label=group.label)
    created = set(logic.track_names()) - set(group.tracks_before)
    for action in _first_writes(group.actions):
        yield _restore(logic, action, created, _sends_added(group))
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
            first.setdefault((a.tool, a.args["track"], a.args.get("aux")), a)
    return list(first.values())


def _sends_added(group: Group) -> set[tuple[str, str]]:
    return {(a.args["track"], a.args["aux"]) for a in group.actions if a.tool == "add_send" and a.undo_steps}


def _restore(logic: LogicPro, action: Action, created: set[str], sends: set[tuple[str, str]]) -> tuple[str, str]:
    track, aux, what = action.args["track"], action.args.get("aux"), _what(action)
    if track in created or aux in created or (track, aux) in sends:
        return "pass", f"{what} goes with the undo steps"
    if action.was is None:
        return "fail", f"{what} could not be read before the run, so it was not put back"
    try:
        if action.tool == "set_volume":
            result = set_volume(logic, track, action.was)
        elif action.tool == "set_pan":
            result = set_pan(logic, track, action.was)
        else:
            result = set_send_level(logic, track, aux, float(str(action.was).replace("-∞", "-inf")))
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
    track, aux = action.args["track"], action.args.get("aux")
    return f"{track}'s send to {aux}" if aux else f"{track}'s {'volume' if action.tool == 'set_volume' else 'pan'}"
