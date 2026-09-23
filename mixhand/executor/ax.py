import subprocess

from mixhand.executor import ExecutorError

CLICK_WITHIN_S = 10.0
ESCAPE = "key code 53"


def click_menu(*path: str) -> None:
    bar, *items = path
    menu = f'menu 1 of menu bar item "{bar}" of menu bar 1'
    # A menu's enabled states are only re-validated when it opens, and pressing a stale disabled item does nothing.
    steps = [f'click menu bar item "{bar}" of menu bar 1']
    for item in items[:-1]:
        steps.append(f'click menu item "{item}" of {menu}')
        menu = f'menu 1 of menu item "{item}" of {menu}'
    last = f'menu item "{items[-1]}" of {menu}'
    steps += [
        f"if not (enabled of {last}) then",
        ESCAPE,
        ESCAPE,
        f'error "{" > ".join(path)} is disabled in Logic"',
        "end if",
        f"click {last}",
    ]
    script = ['tell application "System Events"', 'tell process "Logic Pro"', *steps, "end tell", "end tell"]
    args = [arg for line in script for arg in ("-e", line)]
    try:
        done = subprocess.run(["osascript", *args], capture_output=True, text=True, timeout=CLICK_WITHIN_S)
    except subprocess.TimeoutExpired as e:
        raise ExecutorError(f"clicking {' > '.join(path)} did not finish within {CLICK_WITHIN_S:g}s") from e
    if done.returncode != 0:
        raise ExecutorError(f"could not click {' > '.join(path)}: {done.stderr.strip()}")
