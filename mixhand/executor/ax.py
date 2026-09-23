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


# A header's own name field has no settable value; the Inspector's "Track:" field renames whichever track is selected.
SET_TRACK_NAME = """
on run {position, current, wanted}
    set label to "Track " & position & " “" & current & "”"
    set renamed to "Track " & position & " “" & wanted & "”"
    considering case
    tell application "System Events" to tell process "Logic Pro"
        set rails to {}
        repeat with g in (groups of window 1)
            repeat with t in (groups of g whose description is "Tracks")
                repeat with s1 in splitter groups of t
                    repeat with s2 in splitter groups of s1
                        repeat with sa in scroll areas of s2
                            set rails to rails & (groups of sa whose description is "Tracks header")
                        end repeat
                    end repeat
                end repeat
            end repeat
        end repeat
        if (count of rails) is not 1 then error "found " & (count of rails) & " track header rails"
        set headers to UI elements of item 1 of rails whose description is label
        if (count of headers) is not 1 then error "found " & (count of headers) & " track headers labelled " & label
        set header to item 1 of headers
        set chosen to {}
        repeat with h in UI elements of item 1 of rails
            if (get value of attribute "AXSelected" of h) is true then set end of chosen to (get description of h)
        end repeat
        if chosen is not {label} then error "the selected tracks are " & chosen & ", not only " & label
        set fields to {}
        repeat with i in (groups of window 1 whose description is "Inspector")
            repeat with l in lists of i
                repeat with s in groups of l
                    if (value of static texts of s) contains "Track:" then set fields to fields & (text fields of s)
                end repeat
            end repeat
        end repeat
        if (count of fields) is not 1 then error "found " & (count of fields) & " Track: fields in the Inspector"
        set field to item 1 of fields
        if (get value of field) is not current then error "the Inspector names the selected track " & (get value of field) & ", not " & current
        set value of attribute "AXValue" of field to wanted
        perform action "AXConfirm" of field
        repeat 20 times
            if description of header is renamed then return
            delay 0.1
        end repeat
        error "Logic still labels it " & (description of header)
    end tell
    end considering
end run
"""


def set_track_name(position: int, current: str, wanted: str) -> None:
    try:
        done = subprocess.run(
            ["osascript", "-e", SET_TRACK_NAME, str(position), current, wanted],
            capture_output=True,
            text=True,
            timeout=CLICK_WITHIN_S,
        )
    except subprocess.TimeoutExpired as e:
        raise ExecutorError(f"setting track {position}'s name did not finish within {CLICK_WITHIN_S:g}s") from e
    if done.returncode != 0:
        raise ExecutorError(f"could not set track {position}'s name: {done.stderr.strip()}")
