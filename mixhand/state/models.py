import json
import math
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Send:
    bus: int
    aux: str | None


@dataclass(frozen=True)
class Channel:
    name: str
    volume_db: float
    pan: int
    plugins: list[str]
    sends: list[Send]
    bus: int | None


@dataclass(frozen=True)
class Project:
    path: str
    tempo: float
    time_sig_saved: str | None
    key: str | None


@dataclass(frozen=True)
class Selection:
    start_bar: int
    end_bar: int


@dataclass(frozen=True)
class Session:
    project: Project
    selection: Selection | None
    tracks: list[Channel]
    available_plugins: list[str]


def as_json(session: Session) -> str:
    body = asdict(session)
    for channel in body["tracks"]:
        if math.isinf(channel["volume_db"]):
            channel["volume_db"] = "-inf"
    return json.dumps(body, indent=2, ensure_ascii=False)
