import time

from mixhand.executor import ActionResult, ExecutorError
from mixhand.executor.actionlog import log
from mixhand.executor.ax import click_menu
from mixhand.executor.fader import DB_AT_RAW, PAN_CENTRE_RAW, pan_contract, raw_nearest, volume_contract
from mixhand.executor.logicpro import POLL_S, LogicPro

INSERT_ATTEMPTS = 3
VOLUME_DB_MIN = -17.0
VOLUME_DB_MAX = 6.0
PAN_MIN = -64
PAN_MAX = 63
LANDS_WITHIN_RAW = 5
# A target exactly between two detents makes LogicProMCP land on alternate sides on repeat calls.
TIE_BREAK_RAW = 0.25
DUPLICATE_MENU = ("Track", "Other", "New Track With Duplicate Settings and Content")
# LogicProMCP's track list trails a write by about three seconds in the same process.
SETTLES_WITHIN_S = 10.0


def track_index(logic: LogicPro, track: str) -> int:
    return _track(logic, track)[0]


def _track(logic: LogicPro, track: str) -> tuple[int, dict]:
    return _named(logic.tracks(), track)


def _named(tracks: list[dict], track: str) -> tuple[int, dict]:
    names = [t["name"] for t in tracks]
    matches = [i for i, name in enumerate(names) if name == track]
    if not matches:
        raise ExecutorError(f"no track named {track!r}; Logic has {names}")
    if len(matches) > 1:
        raise ExecutorError(f"{len(matches)} tracks are named {track!r}; track names must be unique")
    return matches[0], tracks[matches[0]]


def inserts(logic: LogicPro, index: int, track: str) -> list[dict]:
    inventory = logic.call("logic_plugins", "get_inventory", track=index)
    if not inventory.get("complete"):
        raise ExecutorError(f"could not read every insert slot on {track!r}")
    return inventory["plugins"]


def insert_plugin(logic: LogicPro, track: str, plugin: str) -> ActionResult:
    project = logic.require_project()
    index = track_index(logic, track)
    attempt = 0
    while True:
        attempt += 1
        slots = inserts(logic, index, track)
        present = [s["insert"] for s in slots if s["name"] == plugin]
        if present and attempt == 1:
            log("insert_plugin.skipped", track=track, plugin=plugin, slot=present[0])
            return ActionResult(ok=True, detail=f"{plugin} is already on {track} slot {present[0]}", verified=True)
        if present:
            log("insert_plugin.done", track=track, plugin=plugin, slot=present[0], verified=True, found_on_rescrape=True)
            return ActionResult(ok=True, detail=f"Inserted {plugin} on {track} slot {present[0]}", verified=True)
        empty = [s["insert"] for s in slots if not s["occupied"]]
        if not empty:
            raise ExecutorError(f"{track!r} has no empty insert slot")
        try:
            return _insert_once(logic, project, index, track, plugin, empty[0], attempt)
        except ExecutorError as e:
            if e.payload.get("safe_to_retry") is not True or attempt == INSERT_ATTEMPTS:
                raise


def _insert_once(logic: LogicPro, project: str, index: int, track: str, plugin: str, slot: int, attempt: int) -> ActionResult:
    log("insert_plugin.start", track=track, plugin=plugin, slot=slot, attempt=attempt)
    try:
        result = logic.call(
            "logic_plugins",
            "insert_verified",
            track=index,
            insert=slot,
            plugin=plugin,
            expected_name=track,
            mode="duplicate_applyback",
            project_expected_path=project,
        )
    except ExecutorError as e:
        log(
            "insert_plugin.refused",
            track=track,
            plugin=plugin,
            slot=slot,
            attempt=attempt,
            error=e.payload.get("error"),
            stage=e.payload.get("setup_stage"),
            safe_to_retry=e.payload.get("safe_to_retry"),
        )
        raise
    confirmed = (
        result.get("state") == "A"
        and result.get("verified") is True
        and result.get("observed_plugin_name") == plugin
        and result.get("observed_slot") == slot
    )
    log(
        "insert_plugin.done",
        track=track,
        plugin=plugin,
        slot=slot,
        state=result.get("state"),
        verified=confirmed,
        trace_id=result.get("trace_id"),
    )
    if not confirmed:
        raise ExecutorError(
            f"inserting {plugin} on {track!r} slot {slot} was not confirmed "
            f"(state {result.get('state')}: {result.get('reason') or result.get('error')})"
        )
    return ActionResult(ok=True, detail=f"Inserted {plugin} on {track} slot {slot}", verified=True)


def set_volume(logic: LogicPro, track: str, db: float) -> ActionResult:
    if not VOLUME_DB_MIN <= db <= VOLUME_DB_MAX:
        raise ExecutorError(f"volume {db:g} dB is outside {VOLUME_DB_MIN:g}..{VOLUME_DB_MAX:g} dB")
    logic.require_project()
    target = raw_nearest(db)
    raw = _move(logic, "set_volume", track, volume_contract(target + TIE_BREAK_RAW), target, requested=db)
    return ActionResult(
        ok=True, detail=f"Set {track} to {DB_AT_RAW[raw]:+.1f} dB (asked {db:+.1f} dB)", verified=True
    )


def set_pan(logic: LogicPro, track: str, value: int) -> ActionResult:
    if not PAN_MIN <= value <= PAN_MAX:
        raise ExecutorError(f"pan {value} is outside {PAN_MIN}..{PAN_MAX}")
    logic.require_project()
    raw = _move(logic, "set_pan", track, pan_contract(value + TIE_BREAK_RAW), value + PAN_CENTRE_RAW, requested=value)
    return ActionResult(
        ok=True, detail=f"Panned {track} to {raw - PAN_CENTRE_RAW} (asked {value})", verified=True
    )


def _move(logic: LogicPro, command: str, track: str, contract: float, target: int, requested: float) -> int:
    index, entry = _track(logic, track)
    if not entry.get("track_ref"):
        raise ExecutorError(f"Logic gave no track_ref for {track!r}, so a move could not be bound to it")
    log(f"{command}.start", track=track, requested=requested, target_raw=target)
    try:
        result = logic.call("logic_mixer", command, track=index, target_ref=entry["track_ref"], value=contract)
    except ExecutorError as e:
        log(f"{command}.refused", track=track, requested=requested, error=e.payload.get("error"))
        raise
    raw = result.get("observed_raw")
    read_back = result.get("state") == "A" and result.get("verified") is True and isinstance(raw, (int, float))
    landed = read_back and abs(raw - target) <= LANDS_WITHIN_RAW
    log(
        f"{command}.done",
        track=track,
        requested=requested,
        target_raw=target,
        observed_raw=raw,
        verified=landed,
        trace_id=result.get("trace_id"),
    )
    if not read_back:
        raise ExecutorError(
            f"{command} on {track!r} could not be read back "
            f"(state {result.get('state')}: {result.get('reason') or result.get('error')})"
        )
    if not landed:
        raise ExecutorError(f"{command} on {track!r} landed at raw {raw:g}, not within {LANDS_WITHIN_RAW} of {target}")
    return round(raw)


def undo(logic: LogicPro, n: int = 1) -> ActionResult:
    logic.require_project()
    for i in range(n):
        log("undo.start", step=i + 1, of=n)
        result = logic.call("logic_edit", "undo")
        log("undo.done", step=i + 1, of=n, sent=result.get("sent"), trace_id=result.get("trace_id"))
        if result.get("sent") is not True:
            raise ExecutorError(f"undo {i + 1} of {n} was not sent: {result.get('reason')}")
    return ActionResult(ok=True, detail=f"Sent {n} undo", verified=False)


def duplicate_track(logic: LogicPro, source: str, new_name: str) -> ActionResult:
    logic.require_project()
    before = logic.tracks()
    index, entry = _named(before, source)
    spans = _spans(logic, index)
    if any(t["name"] == new_name for t in before):
        existing, _ = _named(before, new_name)
        if _spans(logic, existing) != spans:
            raise ExecutorError(f"a track named {new_name!r} already exists and does not carry {source}'s regions")
        log("duplicate_track.skipped", source=source, new_name=new_name)
        return ActionResult(ok=True, detail=f"{new_name} already duplicates {source}", verified=True)
    if not entry.get("track_ref"):
        raise ExecutorError(f"Logic gave no track_ref for {source!r}, so the duplicate could not be bound to it")
    log("duplicate_track.start", source=source, new_name=new_name, regions=len(spans))
    selected = logic.call("logic_tracks", "select", index=index, target_ref=entry["track_ref"])
    if not (selected.get("state") == "A" and selected.get("verified") is True):
        raise ExecutorError(
            f"selecting {source!r} was not confirmed (state {selected.get('state')}: {selected.get('reason')})"
        )
    click_menu(*DUPLICATE_MENU)
    copy_index, copy_ref = _new_track(logic, before, index, source)
    if _spans(logic, copy_index) != spans:
        raise ExecutorError(f"the copy of {source!r} does not carry its regions; undo 1 to remove it")
    _rename_copy(logic, copy_index, copy_ref, source, new_name)
    log("duplicate_track.done", source=source, new_name=new_name, position=copy_index + 1, regions=len(spans), verified=True)
    return ActionResult(
        ok=True, detail=f"Duplicated {source} to {new_name} with {len(spans)} region{'' if len(spans) == 1 else 's'}", verified=True
    )


def _spans(logic: LogicPro, index: int) -> list[tuple[str, str]]:
    regions = logic.read(f"logic://tracks/{index}/regions")
    if not isinstance(regions, list):
        raise ExecutorError(f"the regions on track {index + 1} could not be read")
    return sorted((r["startPosition"], r["endPosition"]) for r in regions)


def _new_track(logic: LogicPro, before: list[dict], index: int, source: str) -> tuple[int, str]:
    refs = [t["track_ref"] for t in before]
    deadline = time.monotonic() + SETTLES_WITHIN_S
    while True:
        after = logic.tracks()
        added = [t.get("track_ref") for t in after if t.get("track_ref") not in refs]
        if added:
            break
        if time.monotonic() >= deadline:
            raise ExecutorError(
                f"no copy of {source!r} appeared within {SETTLES_WITHIN_S:g}s; "
                "check Logic and undo 1 if a copy was made"
            )
        time.sleep(POLL_S)
    expected = refs[: index + 1] + added[:1] + refs[index + 1 :]
    if [t.get("track_ref") for t in after] != expected or after[index + 1]["name"] != source:
        raise ExecutorError(
            f"duplicating {source!r} left {[t['name'] for t in after]}, not one copy after it; check Logic before undoing"
        )
    return index + 1, added[0]


def _rename_copy(logic: LogicPro, index: int, copy_ref: str, source: str, new_name: str) -> None:
    current = logic.tracks()
    if index >= len(current) or current[index].get("track_ref") != copy_ref:
        raise ExecutorError(f"the copy of {source!r} moved before it could be renamed; undo 1 to remove it")
    # LogicProMCP refuses a target_ref rename while two tracks share a name, and reissues the renamed track's ref.
    renamed = logic.call("logic_tracks", "rename", index=index, name=new_name)
    if renamed.get("verified") is not True:
        raise ExecutorError(f"renaming the copy of {source!r} to {new_name!r} was not confirmed; check Logic before undoing")
    deadline = time.monotonic() + SETTLES_WITHIN_S
    while True:
        after = logic.tracks()
        if [t["name"] for t in after] != [t["name"] for t in current]:
            break
        if time.monotonic() >= deadline:
            raise ExecutorError(f"Logic still shows no {new_name!r} {SETTLES_WITHIN_S:g}s after renaming; check Logic before undoing")
        time.sleep(POLL_S)
    others = [t.get("track_ref") for i, t in enumerate(current) if i != index]
    if (
        len(after) != len(current)
        or after[index]["name"] != new_name
        or [t.get("track_ref") for i, t in enumerate(after) if i != index] != others
    ):
        raise ExecutorError(
            f"{new_name!r} did not land on the copy of {source!r} (Logic shows {[t['name'] for t in after]}); "
            "undo 2 to return to where it started"
        )
