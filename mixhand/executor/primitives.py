import time

from mixhand.executor import ActionResult, ExecutorError
from mixhand.executor.actionlog import log
from mixhand.executor.ax import click_menu, click_mixer_menu, pick_plugin, require_inspector, require_mixer, set_track_name
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
AUX_MENU = ("Options", "Create New Auxiliary Channel Strip")
AUX_TRACK_MENU = ("Options", "Create Tracks for Selected Channel Strips")
INSERTABLE = ("Gain", "Channel EQ", "Compressor")
PLUGIN_MENU = {"ChromaVerb": ("Reverb", "ChromaVerb"), "Stereo Delay": ("Delay", "Stereo Delay")}
# A new aux offers only mono inputs, and a reverb or delay return must stay stereo.
PLUGIN_FORMATS = ("Stereo", "Mono->Stereo")
HIDE_PLUGIN_WINDOWS = ("Window", "Hide All Plug-in Windows")
SLOT_LABEL = {"Stereo Delay": "St-Delay"}
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
    if plugin not in INSERTABLE and plugin not in PLUGIN_MENU:
        raise ExecutorError(f"Mixhand inserts only {', '.join([*INSERTABLE, *PLUGIN_MENU])}, not {plugin!r}")
    project = logic.require_project()
    index = track_index(logic, track)
    attempt = 0
    while True:
        attempt += 1
        slots = inserts(logic, index, track)
        present = [s["insert"] for s in slots if s["name"] == SLOT_LABEL.get(plugin, plugin)]
        if present and attempt == 1:
            log("insert_plugin.skipped", track=track, plugin=plugin, slot=present[0])
            return ActionResult(ok=True, detail=f"{plugin} is already on {track} slot {present[0]}", verified=True)
        if present:
            log("insert_plugin.done", track=track, plugin=plugin, slot=present[0], verified=True, found_on_rescrape=True)
            return ActionResult(ok=True, detail=f"Inserted {plugin} on {track} slot {present[0]}", verified=True)
        empty = [s["insert"] for s in slots if not s["occupied"]]
        if not empty:
            raise ExecutorError(f"{track!r} has no empty insert slot")
        if plugin in PLUGIN_MENU:
            return _pick_once(logic, track, plugin, slots, empty[0])
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


def _pick_once(logic: LogicPro, track: str, plugin: str, before: list[dict], slot: int) -> ActionResult:
    log("insert_plugin.start", track=track, plugin=plugin, slot=slot, via="menu")
    try:
        pick_plugin(track, *PLUGIN_MENU[plugin], PLUGIN_FORMATS)
    except ExecutorError as e:
        log("insert_plugin.refused", track=track, plugin=plugin, slot=slot, error=str(e))
        raise ExecutorError(f"{e}; check {track} in the Mixer and undo 1 only if {plugin} is on it") from e
    chain = {s["insert"]: s["name"] for s in before if s["occupied"]}
    deadline = time.monotonic() + SETTLES_WITHIN_S
    while True:
        after = logic.call("logic_plugins", "get_inventory", track=track_index(logic, track))
        landed = {s["insert"]: s["name"] for s in after.get("plugins", []) if s["occupied"]}
        if after.get("complete") and landed != chain:
            break
        if time.monotonic() >= deadline:
            raise ExecutorError(
                f"no plugin appeared on {track!r} within {SETTLES_WITHIN_S:g}s; check it in the Mixer and undo 1 only if {plugin} is on it"
            )
        time.sleep(POLL_S)
    if landed != {**chain, slot: SLOT_LABEL.get(plugin, plugin)}:
        raise ExecutorError(
            f"picking {plugin} left {track!r} with {landed}, not {plugin} added on slot {slot}; check Logic before undoing"
        )
    log("insert_plugin.done", track=track, plugin=plugin, slot=slot, verified=True, via="menu")
    try:
        click_menu(*HIDE_PLUGIN_WINDOWS)
    except ExecutorError as e:
        log("insert_plugin.window_left_open", track=track, plugin=plugin, error=str(e))
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


def delete_track(logic: LogicPro, track: str) -> ActionResult:
    logic.require_project()
    before = logic.tracks()
    index, entry = _named(before, track)
    if not entry.get("track_ref"):
        raise ExecutorError(f"Logic gave no track_ref for {track!r}, so the delete could not be bound to it")
    log("delete_track.start", track=track, position=index + 1)
    try:
        result = logic.call("logic_tracks", "delete", index=index, target_ref=entry["track_ref"])
    except ExecutorError as e:
        log("delete_track.refused", track=track, error=e.payload.get("error"))
        raise
    if not (result.get("state") == "A" and result.get("verified") is True):
        raise ExecutorError(
            f"deleting {track!r} was not confirmed (state {result.get('state')}: {result.get('reason')}); "
            "check Logic before undoing"
        )
    # LogicProMCP reissues every track_ref after a delete, so the result is checked by name, unique above.
    names = [t["name"] for t in before]
    deadline = time.monotonic() + SETTLES_WITHIN_S
    while True:
        after = logic.tracks()
        if [t["name"] for t in after] != names:
            break
        if time.monotonic() >= deadline:
            raise ExecutorError(f"Logic still shows {track!r} {SETTLES_WITHIN_S:g}s after deleting it; check Logic before undoing")
        time.sleep(POLL_S)
    if [t["name"] for t in after] != names[:index] + names[index + 1 :]:
        raise ExecutorError(
            f"deleting {track!r} left {[t['name'] for t in after]}, not the session without it; check Logic before undoing"
        )
    log("delete_track.done", track=track, position=index + 1, verified=True, trace_id=result.get("trace_id"))
    return ActionResult(ok=True, detail=f"Deleted {track}; undo 1 restores it", verified=True)


def duplicate_track(logic: LogicPro, source: str, new_name: str) -> ActionResult:
    logic.require_project()
    before = logic.tracks()
    index, entry = _named(before, source)
    if any(t["name"] == new_name for t in before):
        raise ExecutorError(f"a track named {new_name!r} already exists; Mixhand cannot tell whether it is a copy of {source!r}")
    spans = _spans(logic, index)
    if not entry.get("track_ref"):
        raise ExecutorError(f"Logic gave no track_ref for {source!r}, so the duplicate could not be bound to it")
    require_inspector()
    log("duplicate_track.start", source=source, new_name=new_name, regions=len(spans))
    selected = logic.call("logic_tracks", "select", index=index, target_ref=entry["track_ref"])
    if not (selected.get("state") == "A" and selected.get("verified") is True):
        raise ExecutorError(
            f"selecting {source!r} was not confirmed (state {selected.get('state')}: {selected.get('reason')})"
        )
    try:
        click_menu(*DUPLICATE_MENU)
    except ExecutorError as e:
        raise ExecutorError(f"{e}; check Logic and undo 1 if a copy was made") from e
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
        raise ExecutorError(f"the copy of {source!r} moved before it could be renamed; check Logic before undoing")
    # LogicProMCP's rename falls back to typing, and keystrokes that miss the name field reach Logic as key commands.
    try:
        set_track_name(index + 1, source, new_name)
    except ExecutorError as e:
        raise ExecutorError(f"renaming the copy of {source!r} to {new_name!r} failed ({e}); check Logic before undoing") from e
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


def create_aux(logic: LogicPro, name: str, plugin: str) -> ActionResult:
    if plugin not in INSERTABLE and plugin not in PLUGIN_MENU:
        raise ExecutorError(f"Mixhand inserts only {', '.join([*INSERTABLE, *PLUGIN_MENU])}, so an aux cannot carry {plugin!r}")
    logic.require_project()
    before = logic.tracks()
    if any(t["name"] == name for t in before):
        _, entry = _named(before, name)
        if entry.get("type") != "aux":
            raise ExecutorError(f"a track named {name!r} already exists and is not an aux")
        log("create_aux.skipped", name=name, plugin=plugin)
        inserted = insert_plugin(logic, name, plugin)
        return ActionResult(ok=True, detail=f"Aux {name} already exists; {inserted.detail}", verified=inserted.verified)
    require_mixer()
    require_inspector()
    log("create_aux.start", name=name, plugin=plugin)
    try:
        click_mixer_menu(*AUX_MENU)
    except ExecutorError as e:
        raise ExecutorError(f"{e}; check the Mixer and undo 1 if an aux strip was made") from e
    try:
        click_mixer_menu(*AUX_TRACK_MENU)
    except ExecutorError as e:
        raise ExecutorError(f"{e}; check the Mixer: undo 1 if the new aux strip has no track, undo 2 if it has one") from e
    index, ref, current = _new_aux(logic, before)
    selected = logic.call("logic_tracks", "select", index=index, target_ref=ref)
    if not (selected.get("state") == "A" and selected.get("verified") is True):
        raise ExecutorError(
            f"selecting the new aux was not confirmed (state {selected.get('state')}: {selected.get('reason')}); "
            "undo 2 removes it"
        )
    steps = 2
    if current != name:
        _rename_aux(logic, index, ref, current, name)
        steps = 3
    try:
        inserted = insert_plugin(logic, name, plugin)
    except ExecutorError as e:
        raise ExecutorError(
            f"{e}; aux {name} exists, and {plugin} may or may not be on it: check Logic, then run again "
            f"to finish, or undo {steps} to remove it without the plugin, {steps + 1} with it"
        ) from e
    log("create_aux.done", name=name, plugin=plugin, position=index + 1, verified=inserted.verified)
    return ActionResult(ok=True, detail=f"Created aux {name} with {plugin}; undo {steps + 1} removes it", verified=inserted.verified)


def _new_aux(logic: LogicPro, before: list[dict]) -> tuple[int, str, str]:
    refs = [t.get("track_ref") for t in before]
    deadline = time.monotonic() + SETTLES_WITHIN_S
    while True:
        after = logic.tracks()
        added = [i for i, t in enumerate(after) if t.get("track_ref") not in refs]
        if added:
            break
        if time.monotonic() >= deadline:
            raise ExecutorError(f"no aux track appeared within {SETTLES_WITHIN_S:g}s; check the Mixer before undoing")
        time.sleep(POLL_S)
    kept = [t.get("track_ref") for i, t in enumerate(after) if i not in added]
    if len(added) != 1 or kept != refs or after[added[0]].get("type") != "aux":
        raise ExecutorError(
            f"creating the aux left {[t['name'] for t in after]}, not one new aux track; check Logic before undoing"
        )
    new = after[added[0]]
    if not new.get("track_ref"):
        raise ExecutorError("Logic gave no track_ref for the new aux, so it could not be bound; check Logic before undoing")
    return added[0], new["track_ref"], new["name"]


def _rename_aux(logic: LogicPro, index: int, ref: str, current: str, name: str) -> None:
    before = logic.tracks()
    if index >= len(before) or before[index].get("track_ref") != ref:
        raise ExecutorError("the new aux moved before it could be renamed; check Logic before undoing")
    try:
        set_track_name(index + 1, current, name)
    except ExecutorError as e:
        raise ExecutorError(f"renaming the new aux to {name!r} failed ({e}); check Logic before undoing") from e
    deadline = time.monotonic() + SETTLES_WITHIN_S
    while True:
        after = logic.tracks()
        if [t["name"] for t in after] != [t["name"] for t in before]:
            break
        if time.monotonic() >= deadline:
            raise ExecutorError(f"Logic still shows no {name!r} {SETTLES_WITHIN_S:g}s after renaming; check Logic before undoing")
        time.sleep(POLL_S)
    others = [t.get("track_ref") for i, t in enumerate(before) if i != index]
    if (
        len(after) != len(before)
        or after[index]["name"] != name
        or [t.get("track_ref") for i, t in enumerate(after) if i != index] != others
    ):
        raise ExecutorError(
            f"{name!r} did not land on the new aux (Logic shows {[t['name'] for t in after]}); undo 3 removes it"
        )
