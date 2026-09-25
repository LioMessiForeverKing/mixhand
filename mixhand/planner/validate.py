from dataclasses import dataclass, field

from mixhand.executor import ActionResult, ExecutorError
from mixhand.executor.primitives import (
    PAN_MAX,
    PAN_MIN,
    SEND_DB_MAX,
    SEND_DB_MIN,
    VOLUME_DB_MAX,
    VOLUME_DB_MIN,
    _delay_param,
    _plugin_param,
)
from mixhand.planner.tools import TOOLS
from mixhand.state.models import Session

MAX_ACTIONS = 14
FIELDS = {t["name"]: t["parameters"]["properties"] for t in TOOLS}


class InvalidAction(Exception):
    pass


@dataclass
class Plan:
    session: Session
    tracks: set[str]
    sends: set[tuple[str, str]]
    plugins: dict[str, list[str]]
    created: set[str] = field(default_factory=set)
    inserted: set[tuple[str, str]] = field(default_factory=set)
    added: set[tuple[str, str]] = field(default_factory=set)
    panned: set[str] = field(default_factory=set)
    crossfed: set[tuple[str, str]] = field(default_factory=set)
    actions: int = 0

    @classmethod
    def of(cls, session: Session) -> "Plan":
        return cls(
            session=session,
            tracks={c.name for c in session.tracks},
            sends={(c.name, s.aux) for c in session.tracks for s in c.sends if s.aux},
            plugins={c.name: list(c.plugins) for c in session.tracks},
            panned={c.name for c in session.tracks if c.pan},
        )

    def apply(self, tool: str, args: dict, result: ActionResult) -> None:
        if tool in ("duplicate_track", "create_aux"):
            name = args.get("new_name") or args["name"]
            self.tracks.add(name)
            self.created.add(name)
        if tool == "duplicate_track":
            self.plugins[args["new_name"]] = list(self.plugins.get(args["source"], []))
            self.sends |= {(args["new_name"], aux) for track, aux in self.sends if track == args["source"]}
            if args["source"] in self.panned:
                self.panned.add(args["new_name"])
            self.crossfed |= {(args["new_name"], side) for track, side in self.crossfed if track == args["source"]}
        if tool in ("insert_plugin", "create_aux") and result.undo_steps:
            track = args.get("track") or args["name"]
            self.inserted.add((track, args["plugin"]))
            self.plugins.setdefault(track, []).append(args["plugin"])
        if tool == "add_send":
            self.sends.add((args["track"], args["aux"]))
        if tool == "add_send" and result.undo_steps:
            self.added.add((args["track"], args["aux"]))
        if tool == "set_pan" and args["value"]:
            self.panned.add(args["track"])
        if tool == "set_pan" and not args["value"]:
            self.panned.discard(args["track"])
        if tool == "set_plugin_param" and args["param"].startswith("Crossfeed") and args["value"]:
            self.crossfed.add((args["track"], args["param"]))
        if tool == "set_plugin_param" and args["param"].startswith("Crossfeed") and not args["value"]:
            self.crossfed.discard((args["track"], args["param"]))


def validate(tool: str, args: object, plan: Plan) -> None:
    if tool not in FIELDS:
        raise InvalidAction(f"there is no tool named {tool!r}")
    if not isinstance(args, dict) or set(args) != set(FIELDS[tool]):
        raise InvalidAction(f"{tool} takes exactly {sorted(FIELDS[tool])}")
    for name, spec in FIELDS[tool].items():
        _typed(name, args[name], spec)
    if not args["reason"].strip():
        raise InvalidAction("every action needs a reason")
    if plan.actions >= MAX_ACTIONS:
        raise InvalidAction(f"a request takes at most {MAX_ACTIONS} actions, and this one has used them all; stop here")
    for name in ("track", "source", "aux"):
        if name in args and args[name] not in plan.tracks:
            raise InvalidAction(f"there is no track named {args[name]!r}; the tracks are {sorted(plan.tracks)}")
    for name in ("new_name", "name"):
        if name in args and (args[name] != args[name].strip() or not args[name]):
            raise InvalidAction(f"{args[name]!r} is not a usable track name")
        if name in args and args[name] in plan.tracks:
            raise InvalidAction(f"a track named {args[name]!r} already exists; track names must be unique")
    if "plugin" in args and args["plugin"] not in plan.session.available_plugins:
        raise InvalidAction(f"{args['plugin']!r} is not one of {plan.session.available_plugins}")
    _bounds(tool, args, plan)


def _typed(name: str, value: object, spec: dict) -> None:
    wanted = {"string": (str,), "number": (int, float), "integer": (int,)}[spec["type"]]
    if isinstance(value, bool) or not isinstance(value, wanted):
        raise InvalidAction(f"{name} must be {'an' if spec['type'] == 'integer' else 'a'} {spec['type']}, not {value!r}")
    if "enum" in spec and value not in spec["enum"]:
        raise InvalidAction(f"{name} must be one of {spec['enum']}, not {value!r}")


def _bounds(tool: str, args: dict, plan: Plan) -> None:
    if tool == "set_volume":
        if not VOLUME_DB_MIN <= args["db"] <= VOLUME_DB_MAX:
            raise InvalidAction(f"volume {args['db']:g} dB is outside {VOLUME_DB_MIN:g}..{VOLUME_DB_MAX:g} dB")
        now = next((c.volume_db for c in plan.session.tracks if c.name == args["track"]), None)
        if args["track"] not in plan.created and now is not None and now < VOLUME_DB_MIN:
            raise InvalidAction(
                f"{args['track']} sits at {now:g} dB, below the {VOLUME_DB_MIN:g} dB Mixhand can set, so undo could not put it back"
            )
    if tool == "insert_plugin" and plan.plugins.get(args["track"]) and args["plugin"] not in plan.plugins[args["track"]]:
        raise InvalidAction(
            f"{args['track']} already holds {', '.join(plan.plugins[args['track']])}, and Mixhand can put only one "
            "plugin on a track (SETUP.md); choose which one this track needs"
        )
    if tool == "set_pan" and not PAN_MIN <= args["value"] <= PAN_MAX:
        raise InvalidAction(f"pan {args['value']} is outside {PAN_MIN}..{PAN_MAX}")
    if tool == "set_pan" and args["value"] and any(track == args["track"] for track, _ in plan.crossfed):
        raise InvalidAction(
            f"{args['track']} is a ping-pong delay: its crossfeed already moves the echoes between the sides, so it stays centred"
        )
    if tool == "add_send" and args["track"] == args["aux"]:
        raise InvalidAction(f"{args['aux']!r} cannot send to itself")
    if tool == "set_send_level":
        if not SEND_DB_MIN <= args["db"] <= SEND_DB_MAX:
            raise InvalidAction(f"send level {args['db']:g} dB is outside {SEND_DB_MIN:g}..{SEND_DB_MAX:g} dB")
        if (args["track"], args["aux"]) not in plan.sends:
            raise InvalidAction(f"{args['track']!r} has no send to {args['aux']!r}; add_send first")
        if (args["track"], args["aux"]) not in plan.added and args["track"] not in plan.created:
            raise InvalidAction(
                f"{args['track']}'s send to {args['aux']} was there before the run, and undo could not always put its "
                "level back; set levels only on sends this run adds"
            )
    if tool == "set_plugin_param":
        if (args["track"], args["plugin"]) not in plan.inserted and args["track"] not in plan.created:
            raise InvalidAction(
                f"this run did not put {args['plugin']} on {args['track']!r}, and undo could not put back a parameter "
                "on a plugin that was there before"
            )
        try:
            if args["plugin"] == "Stereo Delay":
                _delay_param(args["param"], args["value"])
            else:
                _plugin_param(args["plugin"], args["param"], args["value"])
        except ExecutorError as e:
            raise InvalidAction(str(e)) from e
        if args["plugin"] == "Stereo Delay" and args["param"].startswith("Crossfeed") and args["value"] and args["track"] in plan.panned:
            raise InvalidAction(f"{args['track']} is panned off centre, and a ping-pong delay stays centred; set_pan it to 0 first")
