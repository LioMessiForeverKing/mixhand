import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from mixhand.executor import ActionResult, ExecutorError
from mixhand.executor.actionlog import log
from mixhand.executor.ax import (
    click_menu,
    click_mixer_menu,
    pick_plugin,
    pick_route,
    read_routes,
    read_send_level,
    require_inspector,
    require_mixer,
    set_delay_param,
    set_track_name,
    step_send_level,
)
from mixhand.executor.fader import (
    DB_AT_RAW,
    PAN_CENTRE_RAW,
    pan_at_contract,
    pan_contract,
    raw_at_contract,
    raw_nearest,
    volume_contract,
)
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
BUSES = range(1, 257)
BUS_PAGE = 32
INPUT_SLOT = "Input slot"
SEND_SLOT = "Send slot"
EMPTY_SEND = "send button"
SEND_DB_MIN = -60.0
SEND_DB_MAX = 0.0
SEND_STEPS = 300
EQ_BANDS = ("Peak 1", "Peak 2", "Peak 3", "Peak 4")
# Below 100 Hz Logic shows a decimal (98.5 Hz) but LogicProMCP targets 99 as "99 Hz", so it never matches.
EQ_HZ_MIN = 100
# LogicProMCP's walk first steps up, so a band left at the 20000 Hz ceiling can never move again.
EQ_HZ_MAX = 19000
EQ_DB_MIN = -24.0
EQ_DB_MAX = 24.0
EQ_GAIN_RAW_AT_0_DB = 240
DELAY_PERCENT = ("Left Feedback", "Right Feedback", "Crossfeed L->R", "Crossfeed R->L")
DELAY_NOTE = ("Left Note", "Right Note")
# A note is given in beats; triplets have no exact decimal, so none is offered.
DELAY_NOTES = {0.25: "1/16", 0.375: "1/16 dotted", 0.5: "1/8", 0.75: "1/8 dotted", 1: "1/4", 1.5: "1/4 dotted", 2: "1/2", 3: "1/2 dotted"}
DELAY_STEPS = 110


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
            return ActionResult(ok=True, detail=f"Inserted {plugin} on {track} slot {present[0]}", verified=True, undo_steps=1)
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
    return ActionResult(ok=True, detail=f"Inserted {plugin} on {track} slot {slot}", verified=True, undo_steps=1)


def _pick_once(logic: LogicPro, track: str, plugin: str, before: list[dict], slot: int) -> ActionResult:
    log("insert_plugin.start", track=track, plugin=plugin, slot=slot, via="menu")
    try:
        chosen = pick_plugin(track, *PLUGIN_MENU[plugin], PLUGIN_FORMATS)
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
    try:
        logic.require_project()
    except ExecutorError as e:
        raise ExecutorError(
            f"{e}; {plugin} landed on {track} slot {slot}, but the front project could not be confirmed afterwards, "
            "so check which project it is in before undoing"
        ) from e
    log("insert_plugin.done", track=track, plugin=plugin, slot=slot, format=chosen, verified=True, via="menu")
    try:
        click_menu(*HIDE_PLUGIN_WINDOWS)
    except ExecutorError as e:
        log("insert_plugin.window_left_open", track=track, plugin=plugin, error=str(e))
    return ActionResult(ok=True, detail=f"Inserted {plugin} ({chosen}) on {track} slot {slot}", verified=True, undo_steps=1)


def set_volume(logic: LogicPro, track: str, db: float) -> ActionResult:
    if not VOLUME_DB_MIN <= db <= VOLUME_DB_MAX:
        raise ExecutorError(f"volume {db:g} dB is outside {VOLUME_DB_MIN:g}..{VOLUME_DB_MAX:g} dB")
    logic.require_project()
    target = raw_nearest(db)
    raw, before = _move(logic, "set_volume", track, volume_contract(target + TIE_BREAK_RAW), target, requested=db, level="volume")
    was = None if before is None else DB_AT_RAW[raw_at_contract(before)]
    return ActionResult(
        ok=True, detail=f"Set {track} to {DB_AT_RAW[raw]:+.1f} dB (asked {db:+.1f} dB)", verified=True, was=was
    )


def set_pan(logic: LogicPro, track: str, value: int) -> ActionResult:
    if not PAN_MIN <= value <= PAN_MAX:
        raise ExecutorError(f"pan {value} is outside {PAN_MIN}..{PAN_MAX}")
    logic.require_project()
    raw, before = _move(logic, "set_pan", track, pan_contract(value + TIE_BREAK_RAW), value + PAN_CENTRE_RAW, requested=value, level="pan")
    # LogicProMCP reports an unreadable pan knob as 0.0, which no real pan maps to.
    was = None if before in (None, 0.0) else pan_at_contract(before)
    return ActionResult(
        ok=True, detail=f"Panned {track} to {raw - PAN_CENTRE_RAW} (asked {value})", verified=True, was=was
    )


def _move(
    logic: LogicPro, command: str, track: str, contract: float, target: int, requested: float, level: str
) -> tuple[int, float | None]:
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
    return round(raw), entry.get(level)


def set_plugin_param(logic: LogicPro, track: str, plugin: str, param: str, value: float) -> ActionResult:
    if plugin == "Stereo Delay":
        return _set_delay_param(logic, track, param, value)
    command, params, wanted, unit = _plugin_param(plugin, param, value)
    project = logic.require_project()
    index, entry = _track(logic, track)
    if not entry.get("track_ref"):
        raise ExecutorError(f"Logic gave no track_ref for {track!r}, so the write could not be bound to it")
    found = [s for s in inserts(logic, index, track) if s["name"] == plugin]
    if len(found) != 1:
        raise ExecutorError(f"{track!r} has {len(found)} {plugin} plugins, not one")
    log("set_plugin_param.start", track=track, plugin=plugin, param=param, requested=value, slot=found[0]["insert"])
    try:
        result = logic.call(
            "logic_plugins",
            command,
            track=index,
            insert=found[0]["insert"],
            target_ref=entry["track_ref"],
            mode="duplicate_applyback",
            project_expected_path=project,
            **params,
        )
    except ExecutorError as e:
        log("set_plugin_param.refused", track=track, plugin=plugin, param=param, requested=value, error=e.payload.get("error"))
        raise ExecutorError(f"{e}; {_left_as(e.payload, track, plugin, param)}", e.payload) from e
    shown = result.get("observed_display")
    confirmed = result.get("state") == "A" and result.get("verified") is True and shown == wanted
    log(
        "set_plugin_param.done",
        track=track,
        plugin=plugin,
        param=param,
        requested=value,
        shown=shown,
        verified=confirmed,
        trace_id=result.get("trace_id"),
    )
    if not confirmed:
        raise ExecutorError(
            f"{track}'s {plugin} {param} reads {shown!r}, not {wanted!r} (state {result.get('state')}); "
            "check it in the plugin, undo does not restore it"
        )
    return ActionResult(
        ok=True,
        detail=f"Set {track}'s {plugin} {param} to {shown} (asked {value:g} {unit})",
        verified=True,
    )


def _plugin_param(plugin: str, param: str, value: float) -> tuple[str, dict, str, str]:
    band, _, kind = param.rpartition(" ")
    if (plugin, param) == ("Compressor", "Threshold"):
        if value != round(value) or not 0 <= value <= 100:
            raise ExecutorError(f"Compressor Threshold is a whole percent from 0 to 100, not {value:g}")
        percent = round(value)
        return "set_param_verified", {"plugin": "Compressor", "param": "threshold", "value": percent}, f"{percent} %", "%"
    if plugin == "Channel EQ" and band in EQ_BANDS and kind == "Frequency":
        if value != round(value) or not EQ_HZ_MIN <= value <= EQ_HZ_MAX:
            raise ExecutorError(f"Channel EQ {param} is a whole number of Hz from {EQ_HZ_MIN} to {EQ_HZ_MAX}, not {value:g}")
        hz = round(value)
        return "set_eq_band_verified", {"band": band, "parameter": kind, "value": hz, "unit": "Hz"}, f"{hz} Hz", "Hz"
    if plugin == "Channel EQ" and band in EQ_BANDS and kind == "Gain":
        if not EQ_DB_MIN <= value <= EQ_DB_MAX:
            raise ExecutorError(f"Channel EQ {param} {value:g} dB is outside {EQ_DB_MIN:g}..{EQ_DB_MAX:g} dB")
        tenths = round(value * 10)
        shown = "0.0 dB" if tenths == 0 else f"{tenths / 10:+.1f} dB"
        params = {"band": band, "parameter": kind, "value": EQ_GAIN_RAW_AT_0_DB + tenths, "unit": "raw_ax_value"}
        return "set_eq_band_verified", params, shown, "dB"
    raise ExecutorError(
        f"param not mapped: {plugin} {param}; Mixhand sets only Compressor Threshold, "
        f"Channel EQ {', '.join(EQ_BANDS)} Frequency and Gain, and Stereo Delay {', '.join([*DELAY_PERCENT, *DELAY_NOTE])}"
    )


def _set_delay_param(logic: LogicPro, track: str, param: str, value: float) -> ActionResult:
    label, wanted, target, unit = _delay_param(param, value)
    logic.require_project()
    index = track_index(logic, track)
    found = [s for s in inserts(logic, index, track) if s["name"] == SLOT_LABEL["Stereo Delay"]]
    if len(found) != 1:
        raise ExecutorError(f"{track!r} has {len(found)} Stereo Delay plugins, not one")
    log("set_plugin_param.start", track=track, plugin="Stereo Delay", param=param, requested=value, slot=found[0]["insert"])
    try:
        was, shown, steps = set_delay_param(track, label, wanted, target, DELAY_STEPS)
    except ExecutorError as e:
        log("set_plugin_param.refused", track=track, plugin="Stereo Delay", param=param, requested=value, error=str(e))
        raise ExecutorError(
            f"{e}; {track}'s Stereo Delay {param} may have moved, and undo does not restore it: check it in the plugin"
        ) from e
    confirmed = shown == wanted
    log(
        "set_plugin_param.done",
        track=track,
        plugin="Stereo Delay",
        param=param,
        requested=value,
        was=was,
        shown=shown,
        steps=steps,
        verified=confirmed,
    )
    if not confirmed:
        raise ExecutorError(
            f"{track}'s Stereo Delay {param} reads {shown!r}, not {wanted!r}, and read {was!r} before; "
            "check it in the plugin, undo does not restore it"
        )
    if steps == 0:
        return ActionResult(ok=True, detail=f"{track}'s Stereo Delay {param} is already {shown}", verified=True)
    return ActionResult(
        ok=True,
        detail=f"Set {track}'s Stereo Delay {param} to {shown} (asked {value:g} {unit}, was {was})",
        verified=True,
    )


def _delay_param(param: str, value: float) -> tuple[str, str, str, str]:
    if param in DELAY_PERCENT:
        if value != round(value) or not 0 <= value <= 100:
            raise ExecutorError(f"Stereo Delay {param} is a whole percent from 0 to 100, not {value:g}")
        percent = round(value)
        return f"{param}:", f"{percent} %", str(percent), "%"
    if param in DELAY_NOTE:
        if value not in DELAY_NOTES:
            beats = ", ".join(f"{b:g} ({n})" for b, n in DELAY_NOTES.items())
            raise ExecutorError(f"Stereo Delay {param} is a note length in beats, one of {beats}, not {value:g}")
        return f"{param}:", DELAY_NOTES[value], "", "beats"
    raise ExecutorError(f"param not mapped: Stereo Delay {param}; Mixhand sets only its {', '.join([*DELAY_PERCENT, *DELAY_NOTE])}")


def _left_as(payload: dict, track: str, plugin: str, param: str) -> str:
    if payload.get("write_attempted") is False:
        return "nothing changed"
    if payload.get("rollback_succeeded") is True and payload.get("last_observed_display"):
        return f"Logic stopped at {payload['last_observed_display']}, and it was put back where it was"
    if payload.get("rollback_succeeded") is True:
        return "it was put back where it was"
    return f"{track}'s {plugin} {param} may have moved, and undo does not restore it: check it in the plugin"


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
        ok=True,
        detail=f"Duplicated {source} to {new_name} with {len(spans)} region{'' if len(spans) == 1 else 's'}",
        verified=True,
        undo_steps=2,
    )


def _spans(logic: LogicPro, index: int) -> list[tuple[str, str]]:
    regions = logic.read(f"logic://tracks/{index}/regions")
    if not isinstance(regions, list):
        raise ExecutorError(f"the regions on track {index + 1} could not be read")
    return sorted((r["startPosition"], r["endPosition"]) for r in regions)


def _new_track(logic: LogicPro, before: list[dict], index: int, source: str) -> tuple[int, str]:
    refs = [t["track_ref"] for t in before]
    names = [t["name"] for t in before]
    deadline = time.monotonic() + SETTLES_WITHIN_S
    while True:
        after = logic.tracks()
        if len(after) != len(before):
            break
        if time.monotonic() >= deadline:
            raise ExecutorError(
                f"no copy of {source!r} appeared within {SETTLES_WITHIN_S:g}s; "
                "check Logic and undo 1 if a copy was made"
            )
        time.sleep(POLL_S)
    # LogicProMCP keys a track_ref on position and name, so only the tracks above the copy keep theirs.
    if (
        [t["name"] for t in after] != names[: index + 1] + [source] + names[index + 1 :]
        or [t.get("track_ref") for t in after[: index + 1]] != refs[: index + 1]
    ):
        raise ExecutorError(
            f"duplicating {source!r} left {[t['name'] for t in after]}, not one copy after it; check Logic before undoing"
        )
    return index + 1, after[index + 1]["track_ref"]


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
        return ActionResult(
            ok=True, detail=f"Aux {name} already exists; {inserted.detail}", verified=inserted.verified, undo_steps=inserted.undo_steps
        )
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
    return ActionResult(
        ok=True,
        detail=f"Created aux {name} with {plugin}; undo {steps + 1} removes it",
        verified=inserted.verified,
        undo_steps=steps + 1,
    )


def _new_aux(logic: LogicPro, before: list[dict]) -> tuple[int, str, str]:
    refs = [t.get("track_ref") for t in before]
    names = [t["name"] for t in before]
    deadline = time.monotonic() + SETTLES_WITHIN_S
    while True:
        after = logic.tracks()
        if len(after) != len(before):
            break
        if time.monotonic() >= deadline:
            raise ExecutorError(f"no aux track appeared within {SETTLES_WITHIN_S:g}s; check the Mixer before undoing")
        time.sleep(POLL_S)
    at = next((i for i, name in enumerate(names) if after[i]["name"] != name), len(names))
    # Logic puts the aux's track after the selected one, and LogicProMCP reissues the refs of every track below it.
    if (
        [t["name"] for i, t in enumerate(after) if i != at] != names
        or [t.get("track_ref") for t in after[:at]] != refs[:at]
        or after[at].get("type") != "aux"
    ):
        raise ExecutorError(
            f"creating the aux left {[t['name'] for t in after]}, not one new aux track; check Logic before undoing"
        )
    new = after[at]
    if not new.get("track_ref"):
        raise ExecutorError("Logic gave no track_ref for the new aux, so it could not be bound; check Logic before undoing")
    return at, new["track_ref"], new["name"]


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


@dataclass(frozen=True)
class Strip:
    name: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    sends: tuple[str, ...]
    empty_sends: int


def add_send(logic: LogicPro, track: str, aux: str) -> ActionResult:
    logic.require_project()
    tracks = logic.tracks()
    _named(tracks, track)
    _, target = _named(tracks, aux)
    if target.get("type") != "aux":
        raise ExecutorError(f"{aux!r} is not an aux, so {track!r} cannot send to it")
    if track == aux:
        raise ExecutorError(f"{aux!r} cannot send to itself")
    before = routes()
    shown = Counter(s.name for s in before)
    hidden = [name for name, n in Counter(t["name"] for t in tracks).items() if n > shown[name]]
    if hidden:
        raise ExecutorError(
            f"the Mixer shows no strip for {hidden}, so a bus only they use would look free; "
            "show every track's strip (unhide the tracks, expand collapsed track stacks, check the Mixer's View filters) and run again"
        )
    source, ret = _strip(before, track), _strip(before, aux)
    if len(ret.inputs) != 1:
        raise ExecutorError(f"{aux!r} shows {len(ret.inputs)} input slots in the Mixer, not one")
    bus = _bus(ret.inputs[0])
    if bus is not None and f"Bus {bus}" in source.sends:
        log("add_send.skipped", track=track, aux=aux, bus=bus)
        return ActionResult(ok=True, detail=f"{track} already sends to {aux} on Bus {bus}", verified=True)
    if source.empty_sends == 0:
        raise ExecutorError(f"{track!r} has no empty send slot")
    steps = 0
    if bus is None:
        bus = _free_bus(before)
        log("add_send.start", track=track, aux=aux, bus=bus, aux_input=ret.inputs[0])
        try:
            pick_route(aux, INPUT_SLOT, ret.inputs[0], _bus_path(f"Bus {bus}", bus))
        except ExecutorError as e:
            raise ExecutorError(f"{e}; check {aux}'s input in the Mixer and undo 1 only if it reads Bus {bus}") from e
        seen = _await_routes(lambda strips: _strip(strips, aux).inputs == (f"Bus {bus}",))
        if seen:
            raise ExecutorError(
                f"{aux}'s input did not read Bus {bus} within {SETTLES_WITHIN_S:g}s ({seen}); "
                f"check it in the Mixer and undo 1 only if it reads Bus {bus}"
            )
        steps = 1
    else:
        log("add_send.start", track=track, aux=aux, bus=bus)
    try:
        pick_route(track, SEND_SLOT, EMPTY_SEND, _bus_path(f"Bus {bus} → {aux}", bus))
    except ExecutorError as e:
        raise ExecutorError(
            f"{e}; check {track}'s sends in the Mixer: undo {steps + 1} if one reads Bus {bus}, "
            + (f"otherwise undo {steps} to put {aux}'s input back" if steps else "otherwise nothing changed")
        ) from e
    wanted = [
        (s.name, (f"Bus {bus}",) if s is ret else s.inputs, s.outputs, sorted([*s.sends, f"Bus {bus}"] if s is source else s.sends))
        for s in before
    ]
    seen = _await_routes(lambda strips: [(s.name, s.inputs, s.outputs, sorted(s.sends)) for s in strips] == wanted)
    if seen:
        raise ExecutorError(
            f"sending {track!r} to {aux!r} on Bus {bus} was not confirmed within {SETTLES_WITHIN_S:g}s ({seen}); "
            "check Logic before undoing"
        )
    try:
        logic.require_project()
    except ExecutorError as e:
        raise ExecutorError(
            f"{e}; {track} sends to {aux} on Bus {bus}, but the front project could not be confirmed afterwards, "
            "so check which project it is in before undoing"
        ) from e
    log("add_send.done", track=track, aux=aux, bus=bus, verified=True)
    return ActionResult(
        ok=True, detail=f"Sent {track} to {aux} on Bus {bus}; undo {steps + 1} removes it", verified=True, undo_steps=steps + 1
    )


def set_send_level(logic: LogicPro, track: str, aux: str, db: float) -> ActionResult:
    if not SEND_DB_MIN <= db <= SEND_DB_MAX:
        raise ExecutorError(f"send level {db:g} dB is outside {SEND_DB_MIN:g}..{SEND_DB_MAX:g} dB")
    target, tolerance = _send_grid(db)
    logic.require_project()
    tracks = logic.tracks()
    _named(tracks, track)
    _, entry = _named(tracks, aux)
    if entry.get("type") != "aux":
        raise ExecutorError(f"{aux!r} is not an aux, so {track!r} has no send to it")
    bus = _send_bus(routes(), track, aux)
    was = read_send_level(track, bus)
    if abs(_tenths(was) - target) <= tolerance:
        log("set_send_level.skipped", track=track, aux=aux, bus=bus, requested=db, level=was)
        return ActionResult(ok=True, detail=f"{track}'s send to {aux} is already at {was} dB", verified=True)
    log("set_send_level.start", track=track, aux=aux, bus=bus, requested=db, was=was)
    try:
        _, landed, steps = step_send_level(track, bus, target, tolerance, SEND_STEPS)
    except ExecutorError as e:
        raise ExecutorError(
            f"{e}; {track}'s send to {aux} read {was} dB before and may have moved, and undo does not restore it: "
            "check it in the Mixer"
        ) from e
    try:
        seen = read_send_level(track, bus)
        still = _send_bus(routes(), track, aux)
        logic.require_project()
    except ExecutorError as e:
        raise ExecutorError(f"{e}; {track}'s send to {aux} was moved from {was} dB but could not be read back: check it in the Mixer") from e
    if still != bus or abs(_tenths(seen) - target) > tolerance:
        raise ExecutorError(
            f"{track}'s send on Bus {bus} reads {seen} dB after stepping to {landed} dB, and {aux} listens on Bus {still}; "
            f"it read {was} dB before: check it in the Mixer"
        )
    log("set_send_level.done", track=track, aux=aux, bus=bus, requested=db, was=was, level=seen, steps=steps, verified=True)
    return ActionResult(
        ok=True,
        detail=f"Set {track}'s send to {aux} to {seen} dB (asked {db:g} dB, was {was} dB)",
        verified=True,
        was=was,
    )


def _send_bus(strips: list[Strip], track: str, aux: str) -> int:
    source, ret = _strip(strips, track), _strip(strips, aux)
    bus = _bus(ret.inputs[0]) if len(ret.inputs) == 1 else None
    if bus is None or f"Bus {bus}" not in source.sends:
        raise ExecutorError(f"{track!r} does not send to {aux!r}; add the send first")
    return bus


# The knob steps 0.1 dB from -6 dB up, 1 dB down to -48 dB and 2 dB below, and within half a step always lands.
def _send_grid(db: float) -> tuple[int, int]:
    tenths = round(db * 10)
    if tenths >= -60:
        return tenths, 0
    whole = round(db) * 10
    return whole, 0 if whole >= -60 else 5 if whole >= -480 else 10


def _tenths(level: str) -> float:
    if level == "-∞":
        return float("-inf")
    try:
        return round(float(level) * 10)
    except ValueError as e:
        raise ExecutorError(f"the send knob reads {level!r}, not a level in dB") from e


# Logic rebuilds the Mixer's strips after a route changes, and a strip read mid-rebuild fails.
def _await_routes(landed: Callable[[list[Strip]], bool]) -> str | None:
    deadline = time.monotonic() + SETTLES_WITHIN_S
    while True:
        try:
            strips = routes()
            if landed(strips):
                return None
            seen = "the Mixer shows " + "; ".join(f"{s.name or '(unnamed)'}: in {s.inputs}, sends {s.sends}" for s in strips)
        except ExecutorError as e:
            seen = f"the Mixer could not be read: {e}"
        if time.monotonic() >= deadline:
            return seen
        time.sleep(POLL_S)


def routes() -> list[Strip]:
    strips = []
    for line in read_routes().splitlines():
        fields = line.split("\t")
        if len(fields) != 5 or not fields[4].isdigit():
            raise ExecutorError(f"could not read a Mixer strip's routing from {line!r}")
        name, inputs, outputs, sends, empty = fields
        strips.append(Strip(name, _split(inputs), _split(outputs), _split(sends), int(empty)))
    return strips


def _split(joined: str) -> tuple[str, ...]:
    return tuple(joined.split("|")) if joined else ()


def _strip(strips: list[Strip], name: str) -> Strip:
    named = [s for s in strips if s.name == name]
    if len(named) != 1:
        raise ExecutorError(f"the Mixer shows {len(named)} strips named {name!r}, not one")
    return named[0]


def _bus(slot: str) -> int | None:
    number = slot.removeprefix("Bus ")
    return int(number) if number != slot and number.isdigit() else None


def _free_bus(strips: list[Strip]) -> int:
    used = {_bus(slot) for s in strips for slot in (*s.inputs, *s.outputs, *s.sends)}
    free = [n for n in BUSES if n not in used]
    if not free:
        raise ExecutorError(f"every bus from {BUSES[0]} to {BUSES[-1]} is in use")
    return free[0]


def _bus_path(item: str, bus: int) -> tuple[str, ...]:
    if bus <= BUS_PAGE:
        return ("Bus", item)
    first = (bus - 1) // BUS_PAGE * BUS_PAGE + 1
    return ("Bus", f"{first} - {first + BUS_PAGE - 1}", item)
