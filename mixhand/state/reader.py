import time
from datetime import datetime, timezone

from mixhand.executor import ExecutorError
from mixhand.executor.fader import DB_AT_RAW, pan_at_contract, raw_at_contract
from mixhand.executor.logicpro import POLL_S, LogicPro
from mixhand.executor.primitives import INSERTABLE, PLUGIN_MENU, SLOT_LABEL, Strip, _bus, _strip, inserts, routes
from mixhand.state.models import Channel, Project, Selection, Send, Session

FRESH_WITHIN_S = 10.0
PLUGIN_AT_LABEL = {label: plugin for plugin, label in SLOT_LABEL.items()}


def read_session(logic: LogicPro, key: str | None = None, selection: Selection | None = None) -> Session:
    path = logic.require_project()
    started = datetime.now(timezone.utc)
    before = _fresh(logic, "logic://tracks", started)["data"]
    strips = routes()
    tempo = ((_fresh(logic, "logic://transport/state", started).get("data") or {}).get("state") or {}).get("tempo")
    if not isinstance(tempo, (int, float)):
        raise ExecutorError("Logic's transport gave no tempo")
    time_sig = (logic.read("logic://project/info").get("data") or {}).get("timeSignature")
    channels = [_channel(logic, index, track, strips) for index, track in enumerate(before)]
    after = _fresh(logic, "logic://tracks", datetime.now(timezone.utc))["data"]
    if _identities(after) != _identities(before):
        raise ExecutorError("Logic's tracks changed while the session was read; run mixhand state again")
    return Session(
        project=Project(path=path, tempo=tempo, time_sig_saved=time_sig, key=key),
        selection=selection,
        tracks=[c for c in channels if c.bus is None],
        auxes=[c for c in channels if c.bus is not None],
        available_plugins=[*INSERTABLE, *PLUGIN_MENU],
    )


def _identities(tracks: list[dict]) -> list[tuple[str, str | None]]:
    return [(t["name"], t.get("track_ref")) for t in tracks]


# LogicProMCP serves these from a poll cache that lags a write by up to one poll, about 3.5 s.
def _fresh(logic: LogicPro, uri: str, since: datetime) -> dict:
    deadline = time.monotonic() + FRESH_WITHIN_S
    while True:
        reply = logic.read(uri)
        fetched = reply.get("fetched_at")
        live = reply.get("source") == "ax_live" and reply.get("readable", True)
        if live and fetched and datetime.fromisoformat(fetched) >= since:
            return reply
        if time.monotonic() >= deadline:
            raise ExecutorError(f"{uri} gave no live reading taken after {since:%H:%M:%S} within {FRESH_WITHIN_S:g}s")
        time.sleep(POLL_S)


def _channel(logic: LogicPro, index: int, track: dict, strips: list[Strip]) -> Channel:
    name = track["name"]
    strip = _strip(strips, name)
    return Channel(
        name=name,
        volume_db=DB_AT_RAW[raw_at_contract(track["volume"])],
        pan=pan_at_contract(track["pan"]),
        plugins=_plugins(logic, index, name),
        sends=[Send(bus, _aux_on(strips, bus)) for bus in map(_bus, strip.sends) if bus is not None],
        bus=_bus(strip.inputs[0]) if len(strip.inputs) == 1 else None,
    )


def _plugins(logic: LogicPro, index: int, track: str) -> list[str]:
    occupied = [s for s in inserts(logic, index, track) if s["occupied"]]
    if any(not s.get("name") for s in occupied):
        raise ExecutorError(f"an insert on {track!r} holds a plugin whose name could not be read")
    return [PLUGIN_AT_LABEL.get(s["name"], s["name"]) for s in occupied]


def _aux_on(strips: list[Strip], bus: int) -> str | None:
    listening = [s.name for s in strips if len(s.inputs) == 1 and _bus(s.inputs[0]) == bus]
    return listening[0] or None if len(listening) == 1 else None
