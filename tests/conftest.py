import json
import sys
from pathlib import Path

import pytest

PROJECT = "/tmp/Mixhand Test.logicx"
FAKE = Path(__file__).parent / "fake_logicpromcp.py"


def tracks(*names, readable=True):
    return {
        "readable": readable,
        "source": "ax_live" if readable else "project_file",
        "reason": None if readable else "track_names_synthesised_from_project_file",
        "data": [{"id": i, "name": name} for i, name in enumerate(names)],
    }


def slot(insert, name=None):
    return {"insert": insert, "name": name, "occupied": name is not None}


def inventory(*slots):
    return {"state": "A", "complete": True, "plugins": list(slots)}


class Fake:
    def __init__(self, root: Path):
        self.binary = root / "LogicProMCP"
        self.scenario = root / "scenario.json"
        self.calls_path = root / "calls.jsonl"
        self.log_path = root / "logs" / "actions.jsonl"
        self.binary.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
        self.binary.chmod(0o755)

    def serve(self, resources=None, tools=None, version="3.16.0", doctor=None):
        resources = {"logic://project/info": [{"data": {"filePath": PROJECT}}], **(resources or {})}
        self.scenario.write_text(
            json.dumps({"resources": resources, "tools": tools or {}, "version": version, "doctor": doctor or {}})
        )

    def calls(self):
        if not self.calls_path.exists():
            return []
        return [json.loads(line) for line in self.calls_path.read_text().splitlines()]

    def log(self):
        return [json.loads(line) for line in self.log_path.read_text().splitlines()]


@pytest.fixture
def fake(tmp_path, monkeypatch):
    f = Fake(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FAKE_SCENARIO", str(f.scenario))
    monkeypatch.setenv("FAKE_CALLS", str(f.calls_path))
    monkeypatch.setenv("MIXHAND_LOGICPROMCP", str(f.binary))
    monkeypatch.setenv("MIXHAND_PROJECT", PROJECT)
    monkeypatch.setattr("mixhand.executor.logicpro.POLL_S", 0.01)
    return f
