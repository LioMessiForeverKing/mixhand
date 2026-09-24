import json
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path("logs/actions.jsonl")


def log(event: str, **fields: object) -> None:
    line = json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields})
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as f:
        f.write(line + "\n")
