from mixhand.executor.primitives import (
    EQ_BANDS,
    EQ_DB_MAX,
    EQ_DB_MIN,
    EQ_HZ_MAX,
    EQ_HZ_MIN,
    INSERTABLE,
    PAN_MAX,
    PAN_MIN,
    PLUGIN_MENU,
    SEND_DB_MAX,
    SEND_DB_MIN,
    VOLUME_DB_MAX,
    VOLUME_DB_MIN,
)

PLUGINS = [*INSERTABLE, *PLUGIN_MENU]
PARAMS = ["Threshold", *(f"{band} {kind}" for band in EQ_BANDS for kind in ("Frequency", "Gain"))]
TRACK = {"type": "string", "description": "The exact name of a track in the session, or one this run created."}
NEW_NAME = {"type": "string", "description": "A name no track has yet."}
REASON = {"type": "string", "description": "One sentence, specific to this session: the track, the problem, the number."}


def _tool(name: str, description: str, /, **properties: dict) -> dict:
    properties = {**properties, "reason": REASON}
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


TOOLS = [
    _tool(
        "duplicate_track",
        "Copy a track with its regions, plugins and settings to a new track placed right after it.",
        source=TRACK,
        new_name=NEW_NAME,
    ),
    _tool(
        "insert_plugin",
        "Put a stock plugin in the track's first empty insert slot. A plugin already on the track is left as it is.",
        track=TRACK,
        plugin={"type": "string", "enum": PLUGINS},
    ),
    _tool(
        "set_volume",
        f"Move the track's fader, {VOLUME_DB_MIN:g} to {VOLUME_DB_MAX:+g} dB. Below {VOLUME_DB_MIN:g} dB Logic's fader is too coarse to land.",
        track=TRACK,
        db={"type": "number"},
    ),
    _tool(
        "set_pan",
        f"Pan the track, {PAN_MIN} hard left to {PAN_MAX} hard right, 0 centre. It lands within 5 of the value asked.",
        track=TRACK,
        value={"type": "integer"},
    ),
    _tool(
        "create_aux",
        "Create an aux channel, with a track so it shows in the session, carrying one plugin. Its fader starts at -inf, so it is silent until set_volume.",
        name=NEW_NAME,
        plugin={"type": "string", "enum": PLUGINS},
    ),
    _tool(
        "add_send",
        "Send a track to an aux on a free bus. The send starts at -inf, so nothing reaches the aux until set_send_level.",
        track=TRACK,
        aux=TRACK,
    ),
    _tool(
        "set_send_level",
        f"Set the level of a track's existing send to an aux, {SEND_DB_MIN:g} to {SEND_DB_MAX:g} dB.",
        track=TRACK,
        aux=TRACK,
        db={"type": "number"},
    ),
    _tool(
        "set_plugin_param",
        "Set one parameter on a Compressor or Channel EQ this run inserted. Compressor Threshold is a whole percent of its "
        f"slider, 0 to 100. Channel EQ {EQ_BANDS[0]} to {EQ_BANDS[-1]} Frequency is whole Hz, {EQ_HZ_MIN} to {EQ_HZ_MAX}; "
        f"Gain is {EQ_DB_MIN:g} to {EQ_DB_MAX:+g} dB in tenths. Nothing else can be set.",
        track=TRACK,
        plugin={"type": "string", "enum": ["Compressor", "Channel EQ"]},
        param={"type": "string", "enum": PARAMS},
        value={"type": "number"},
    ),
]
