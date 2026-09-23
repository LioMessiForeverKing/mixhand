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
TRACK_PANES = """
on trackPanes()
    tell application "System Events" to tell process "Logic Pro"
        set found to {}
        repeat with w in windows
            set rails to {}
            repeat with g in (groups of w)
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
            if rails is not {} then set end of found to {rails, contents of w}
        end repeat
        if (count of found) is not 1 then error "found " & (count of found) & " windows with track headers"
        set {rails, home} to item 1 of found
        if (count of rails) is not 1 then error "found " & (count of rails) & " track header rails"
        set fields to {}
        repeat with i in (groups of home whose description is "Inspector")
            repeat with l in lists of i
                repeat with s in groups of l
                    if (value of static texts of s) contains "Track:" then set fields to fields & (text fields of s)
                end repeat
            end repeat
        end repeat
        if (count of fields) is not 1 then error "found " & (count of fields) & " Track: fields; show the Inspector (View > Show Inspector)"
        return {item 1 of rails, item 1 of fields}
    end tell
end trackPanes
"""

REQUIRE_INSPECTOR = TRACK_PANES + """
on run
    my trackPanes()
end run
"""

SET_TRACK_NAME = TRACK_PANES + """
on run {position, current, wanted}
    set label to "Track " & position & " “" & current & "”"
    set renamed to "Track " & position & " “" & wanted & "”"
    considering case
    set {rail, field} to my trackPanes()
    tell application "System Events" to tell process "Logic Pro"
        set headers to UI elements of rail whose description is label
        if (count of headers) is not 1 then error "found " & (count of headers) & " track headers labelled " & label
        set header to item 1 of headers
        set chosen to {}
        repeat with h in UI elements of rail
            if (get value of attribute "AXSelected" of h) is true then set end of chosen to (get description of h)
        end repeat
        if chosen is not {label} then error "the selected tracks are " & chosen & ", not only " & label
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


# The Mixer's Edit, Options and View menus are menu buttons inside its own window, not items of the menu bar.
MIXER_BAR = """
on mixerBar()
    tell application "System Events" to tell process "Logic Pro"
        set found to {}
        repeat with w in windows
            repeat with g in (groups of w)
                repeat with m in (groups of g whose description is "Mixer")
                    repeat with b in (groups of m)
                        if (count of (menu buttons of b whose description is "Options")) is 1 then set end of found to contents of b
                    end repeat
                end repeat
            end repeat
        end repeat
        if (count of found) is not 1 then error "found " & (count of found) & " Mixers; show exactly one, in the Tracks window (X) or its own (Window > Open Mixer)"
        return item 1 of found
    end tell
end mixerBar
"""

REQUIRE_MIXER = MIXER_BAR + """
on run
    my mixerBar()
end run
"""

# Straight after a strip is created, Logic drops a click on a Mixer menu button, so the click repeats until the menu opens.
CLICK_MIXER_MENU = MIXER_BAR + """
on run {menuName, itemName}
    set bar to my mixerBar()
    tell application "System Events" to tell process "Logic Pro"
        set opener to first menu button of bar whose description is menuName
        repeat with attempt from 1 to 10
            click opener
            repeat 5 times
                if exists menu 1 of opener then exit repeat
                delay 0.1
            end repeat
            if exists menu 1 of opener then exit repeat
        end repeat
        if not (exists menu 1 of opener) then error "the Mixer's " & menuName & " menu did not open"
        set choice to menu item itemName of menu 1 of opener
        if not (enabled of choice) then
            perform action "AXCancel" of menu 1 of opener
            error menuName & " > " & itemName & " is disabled in the Mixer"
        end if
        click choice
        return attempt
    end tell
end run
"""


def require_mixer() -> None:
    _run(REQUIRE_MIXER, "finding the Mixer")


def click_mixer_menu(menu: str, item: str) -> None:
    _run(CLICK_MIXER_MENU, f"clicking Mixer {menu} > {item}", menu, item)


def require_inspector() -> None:
    _run(REQUIRE_INSPECTOR, "finding the Inspector's Track: field")


def set_track_name(position: int, current: str, wanted: str) -> None:
    _run(SET_TRACK_NAME, f"setting track {position}'s name", str(position), current, wanted)


def _run(script: str, what: str, *args: str) -> None:
    try:
        done = subprocess.run(["osascript", "-e", script, *args], capture_output=True, text=True, timeout=CLICK_WITHIN_S)
    except subprocess.TimeoutExpired as e:
        raise ExecutorError(f"{what} did not finish within {CLICK_WITHIN_S:g}s") from e
    if done.returncode != 0:
        raise ExecutorError(f"{what} failed: {done.stderr.strip()}")
