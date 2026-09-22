from dataclasses import dataclass


class ExecutorError(Exception):
    def __init__(self, message: str, payload: dict | None = None):
        super().__init__(message)
        self.payload = payload or {}


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    detail: str
    verified: bool
