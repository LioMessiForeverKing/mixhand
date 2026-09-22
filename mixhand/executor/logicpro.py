import json
import os
import subprocess
import time
from pathlib import Path

from mixhand.executor import ExecutorError

BINARY_ENV = "MIXHAND_LOGICPROMCP"
PROJECT_ENV = "MIXHAND_PROJECT"
TRACKS_READABLE_WITHIN_S = 10.0
POLL_S = 0.25


def configured(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ExecutorError(f"{name} is not set; see SETUP.md")
    return value


def same_path(a: str, b: str) -> bool:
    return Path(a).expanduser().resolve() == Path(b).expanduser().resolve()


class LogicPro:
    def __init__(self, binary: str):
        try:
            self._proc = subprocess.Popen(
                [binary],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except OSError as e:
            raise ExecutorError(f"could not start LogicProMCP at {binary}: {e}") from e
        self._last_id = 0
        self._request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "mixhand", "version": "0.1.0"},
            },
        )
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    @classmethod
    def from_env(cls) -> "LogicPro":
        return cls(configured(BINARY_ENV))

    def __enter__(self) -> "LogicPro":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._proc.stdin.close()
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()

    def _send(self, message: dict) -> None:
        self._proc.stdin.write(json.dumps(message) + "\n")
        self._proc.stdin.flush()

    def _request(self, method: str, params: dict) -> dict:
        self._last_id += 1
        self._send({"jsonrpc": "2.0", "id": self._last_id, "method": method, "params": params})
        while True:
            line = self._proc.stdout.readline()
            if not line:
                raise ExecutorError(f"LogicProMCP exited during {method}")
            reply = json.loads(line)
            if reply.get("id") != self._last_id:
                continue
            if "error" in reply:
                raise ExecutorError(f"LogicProMCP {method}: {reply['error'].get('message')}")
            return reply["result"]

    def call(self, tool: str, command: str, **params: object) -> dict:
        result = self._request(
            "tools/call", {"name": tool, "arguments": {"command": command, "params": params}}
        )
        text = result["content"][0]["text"]
        if result.get("isError"):
            try:
                payload = json.loads(text)
            except ValueError:
                payload = {}
            raise ExecutorError(f"{tool}.{command} failed: {text}", payload)
        return json.loads(text)

    def read(self, uri: str) -> dict:
        result = self._request("resources/read", {"uri": uri})
        return json.loads(result["contents"][0]["text"])

    def track_names(self) -> list[str]:
        deadline = time.monotonic() + TRACKS_READABLE_WITHIN_S
        while True:
            tracks = self.read("logic://tracks")
            if tracks.get("readable") and tracks.get("source") == "ax_live":
                return [track["name"] for track in tracks["data"]]
            if time.monotonic() >= deadline:
                raise ExecutorError(
                    f"Logic's track list was not readable within {TRACKS_READABLE_WITHIN_S:g}s "
                    f"({tracks.get('reason')})"
                )
            time.sleep(POLL_S)

    def front_project(self) -> str | None:
        return self.read("logic://project/info").get("data", {}).get("filePath")

    def require_project(self) -> str:
        expected = configured(PROJECT_ENV)
        front = self.front_project()
        if front is None or not same_path(front, expected):
            raise ExecutorError(
                f"the front Logic project is {front}, not {expected}; Mixhand only touches {PROJECT_ENV}"
            )
        return expected
