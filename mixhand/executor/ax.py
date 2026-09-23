import subprocess

from mixhand.executor import ExecutorError

CLICK_WITHIN_S = 10.0


def click_menu(*path: str) -> None:
    bar, *items = path
    target = f'menu bar item "{bar}" of menu bar 1'
    for item in items:
        target = f'menu item "{item}" of menu 1 of {target}'
    script = f'tell application "System Events" to tell process "Logic Pro" to click {target}'
    try:
        done = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=CLICK_WITHIN_S)
    except subprocess.TimeoutExpired as e:
        raise ExecutorError(f"clicking {' > '.join(path)} did not finish within {CLICK_WITHIN_S:g}s") from e
    if done.returncode != 0:
        raise ExecutorError(f"could not click {' > '.join(path)}: {done.stderr.strip()}")
