import json
import os
import plistlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from mixhand.executor import ExecutorError
from mixhand.executor.logicpro import BINARY_ENV, PROJECT_ENV, LogicPro, same_path

PINNED_LOGICPROMCP = "3.16.0"
VERIFIED_LOGIC = "12.3.1"
LOGIC_INFO_PLIST = Path("/Applications/Logic Pro.app/Contents/Info.plist")
UPSTREAM_CHECKS = {
    "permissions.accessibility": "Accessibility permission",
    "permissions.post_event_access": "PostEvent permission",
    "permissions.automation_logic_pro": "Automation → Logic Pro",
    "permissions.automation_system_events": "Automation → System Events",
    "logic.application_state": "Logic Pro running with a window",
    "logic.blocking_dialog": "No blocking Logic dialog",
}

Status = Literal["pass", "fail", "unknown"]


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str


def run() -> list[Check]:
    checks = [logic_version()]
    binary, binary_check = logicpromcp_binary()
    checks.append(binary_check)
    if binary is None:
        needs = "needs a working LogicProMCP binary"
        checks += [Check(name, "unknown", needs) for name in UPSTREAM_CHECKS.values()]
        checks.append(Check("Front project", "unknown", needs))
        return checks
    checks += upstream(binary)
    checks.append(front_project(binary))
    return checks


def logic_version() -> Check:
    name = "Logic Pro version"
    try:
        with LOGIC_INFO_PLIST.open("rb") as f:
            version = plistlib.load(f).get("CFBundleShortVersionString")
    except FileNotFoundError:
        return Check(name, "fail", f"Logic Pro is not installed ({LOGIC_INFO_PLIST} is missing)")
    if version != VERIFIED_LOGIC:
        return Check(
            name, "fail", f"{version} is installed; Mixhand is verified on {VERIFIED_LOGIC}. Re-verify, then update SETUP.md"
        )
    return Check(name, "pass", version)


def logicpromcp_binary() -> tuple[str | None, Check]:
    name = "LogicProMCP binary"
    binary = os.environ.get(BINARY_ENV, "").strip()
    if not binary:
        return None, Check(name, "fail", f"{BINARY_ENV} is not set; see SETUP.md")
    if not os.access(binary, os.X_OK):
        return None, Check(name, "fail", f"{binary} is not an executable file")
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, Check(name, "fail", f"{binary} --version did not run: {e}")
    lines = out.stdout.strip().splitlines()
    version = lines[-1] if lines else ""
    if version != PINNED_LOGICPROMCP:
        return None, Check(name, "fail", f"version {version!r}; Mixhand pins {PINNED_LOGICPROMCP}")
    return binary, Check(name, "pass", f"{version} at {binary}")


def upstream(binary: str) -> list[Check]:
    try:
        out = subprocess.run([binary, "doctor", "--json"], capture_output=True, text=True, timeout=120)
        report = {c["id"]: c for c in json.loads(out.stdout)["checks"]}
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError) as e:
        return [Check(name, "unknown", f"LogicProMCP doctor did not report: {e}") for name in UPSTREAM_CHECKS.values()]
    checks = []
    for check_id, name in UPSTREAM_CHECKS.items():
        found = report.get(check_id)
        if found is None:
            checks.append(Check(name, "unknown", f"LogicProMCP doctor has no {check_id} check"))
        elif found["status"] == "pass":
            checks.append(Check(name, "pass", found.get("summary", "")))
        elif found["status"] == "fail":
            checks.append(Check(name, "fail", found.get("summary", "")))
        else:
            checks.append(Check(name, "unknown", f"LogicProMCP reported {found['status']}: {found.get('summary', '')}"))
    return checks


def front_project(binary: str) -> Check:
    name = "Front project"
    expected = os.environ.get(PROJECT_ENV, "").strip()
    if not expected:
        return Check(name, "fail", f"{PROJECT_ENV} is not set; see SETUP.md")
    try:
        with LogicPro(binary) as logic:
            front = logic.front_project()
    except ExecutorError as e:
        return Check(name, "unknown", str(e))
    if front is None:
        return Check(name, "unknown", "LogicProMCP could not say which project is in front")
    if not same_path(front, expected):
        return Check(name, "fail", f"{front} is in front, not {PROJECT_ENV}={expected}")
    return Check(name, "pass", front)
