import json
import os
import select
import subprocess
import time
from pathlib import Path

from mixhand.executor import ExecutorError

BINARY_ENV = "MIXHAND_LOGICPROMCP"
PROJECT_ENV = "MIXHAND_PROJECT"
TRACKS_READABLE_WITHIN_S = 10.0
POLL_S = 0.25
RESPONSE_WITHIN_S = 60.0


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
                bufsize=0,
            )
        except OSError as e:
            raise ExecutorError(f"could not start LogicProMCP at {binary}: {e}") from e
        self._last_id = 0
        self._buffer = b""
        try:
            self._request(
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "mixhand", "version": "0.1.0"},
                },
            )
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except BaseException:
            self.close()
            raise

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
        try:
            self._proc.stdin.write((json.dumps(message) + "\n").encode())
        except BrokenPipeError as e:
            raise ExecutorError("LogicProMCP is not running") from e

    def _readline(self, method: str, deadline: float) -> bytes:
        while b"\n" not in self._buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self._proc.stdout], [], [], remaining)[0]:
                raise ExecutorError(f"LogicProMCP did not answer {method} within {RESPONSE_WITHIN_S:g}s")
            chunk = os.read(self._proc.stdout.fileno(), 65536)
            if not chunk:
                raise ExecutorError(f"LogicProMCP exited during {method}")
            self._buffer += chunk
        line, self._buffer = self._buffer.split(b"\n", 1)
        return line

    def _request(self, method: str, params: dict) -> dict:
        self._last_id += 1
        self._send({"jsonrpc": "2.0", "id": self._last_id, "method": method, "params": params})
        deadline = time.monotonic() + RESPONSE_WITHIN_S
        while True:
            try:
                reply = json.loads(self._readline(method, deadline))
            except ValueError as e:
                raise ExecutorError(f"LogicProMCP sent a line that is not JSON during {method}") from e
            if reply.get("id") != self._last_id:
                continue
            if "error" in reply:
                raise ExecutorError(f"LogicProMCP {method}: {reply['error'].get('message')}")
            return reply["result"]

    def call(self, tool: str, command: str, **params: object) -> dict:
        result = self._request(
            "tools/call", {"name": tool, "arguments": {"command": command, "params": params}}
        )
        try:
            text = result["content"][0]["text"]
        except (KeyError, IndexError, TypeError) as e:
            raise ExecutorError(f"{tool}.{command} returned no content") from e
        if result.get("isError"):
            try:
                payload = json.loads(text)
            except ValueError:
                payload = {}
            raise ExecutorError(f"{tool}.{command} failed: {text}", payload)
        try:
            return json.loads(text)
        except ValueError as e:
            raise ExecutorError(f"{tool}.{command} returned text that is not JSON: {text}") from e

    def read(self, uri: str) -> dict:
        result = self._request("resources/read", {"uri": uri})
        try:
            return json.loads(result["contents"][0]["text"])
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise ExecutorError(f"{uri} could not be read") from e

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
        return (self.read("logic://project/info").get("data") or {}).get("filePath")

    def require_project(self) -> str:
        expected = configured(PROJECT_ENV)
        front = self.front_project()
        if front is None or not same_path(front, expected):
            raise ExecutorError(
                f"the front Logic project is {front}, not {expected}; Mixhand only touches {PROJECT_ENV}"
            )
        return expected
