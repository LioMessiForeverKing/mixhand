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
        try
            set choice to menu item itemName of menu 1 of opener
            set usable to enabled of choice
        on error failure
            perform action "AXCancel" of menu 1 of opener
            error failure
        end try
        if not usable then
            perform action "AXCancel" of menu 1 of opener
            error menuName & " > " & itemName & " is disabled in the Mixer"
        end if
        click choice
        return attempt
    end tell
end run
"""


# The strips are a layout area beside the Mixer's menu bar, and a slot's plug-in menu opens as a menu of that area.
# A strip's description keeps the name Logic gave it, so a renamed aux is found by its name field.
MIXER_STRIPS = """
on mixerStrips()
    tell application "System Events" to tell process "Logic Pro"
        set found to {}
        repeat with w in windows
            repeat with g in (groups of w)
                if (count of (groups of g whose description is "Mixer")) > 0 then
                    repeat with a in (UI elements of g whose role is "AXLayoutArea" and description is "Mixer")
                        set end of found to contents of a
                    end repeat
                end if
            end repeat
        end repeat
        if (count of found) is not 1 then error "found " & (count of found) & " Mixers; show exactly one, in the Tracks window (X) or its own (Window > Open Mixer)"
        return item 1 of found
    end tell
end mixerStrips
"""

PICK_PLUGIN = MIXER_STRIPS + """
on run argv
    set {stripName, category, plugin} to items 1 thru 3 of argv
    set formats to items 4 thru -1 of argv
    set area to my mixerStrips()
    considering case
    tell application "System Events" to tell process "Logic Pro"
        set strips to {}
        repeat with s in UI elements of area
            if (value of text fields of s whose description is "name") is {stripName} then set end of strips to contents of s
        end repeat
        if (count of strips) is not 1 then error "found " & (count of strips) & " Mixer strips named " & stripName
        set slots to buttons of (item 1 of strips) whose description is "audio plug-in"
        if (count of slots) is not 1 then error "found " & (count of slots) & " empty insert slots on " & stripName
        set slot to item 1 of slots
        if (count of menus of area) > 0 then error "a plug-in menu is already open in the Mixer; close it and run again"
        set opener to missing value
        repeat with a in actions of slot
            if name of a starts with "Name:Open plug-in menu" then set opener to contents of a
        end repeat
        if opener is missing value then error stripName & "'s empty slot offers no plug-in menu"
        perform opener
        repeat 20 times
            if (count of menus of area) > 0 then exit repeat
            delay 0.1
        end repeat
        if (count of menus of area) is 0 then error "the plug-in menu on " & stripName & " did not open"
        set m to menu 1 of area
        try
            set {menuX, menuY} to position of m
            set {menuW, menuH} to size of m
            set {slotX, slotY} to position of slot
            set {slotW, slotH} to size of slot
            set slotMid to slotY + slotH / 2
            if slotMid < menuY - 96 or slotMid > menuY + menuH + 96 or menuX < slotX - 140 or menuX > slotX + slotW + 360 then
                error "the plug-in menu did not open at " & stripName & "'s empty slot"
            end if
            set choices to menu 1 of menu item plugin of menu 1 of menu item category of m
            set leaf to missing value
            repeat with fmt in formats
                if leaf is missing value and (exists menu item (contents of fmt) of choices) then set leaf to menu item (contents of fmt) of choices
            end repeat
            if leaf is missing value then error category & " > " & plugin & " offers " & (title of menu items of choices) & ", none of " & formats
            if not (enabled of leaf) then error category & " > " & plugin & " > " & (title of leaf) & " is disabled"
        on error failure
            perform action "AXCancel" of m
            error failure
        end try
        set chosen to title of leaf
        perform action "AXPick" of leaf
        return chosen
    end tell
    end considering
end run
"""


# A slot's description is its current setting, so input, output and send slots are told apart by their help text.
READ_ROUTES = MIXER_STRIPS + """
on run
    set area to my mixerStrips()
    set out to {}
    tell application "System Events" to tell process "Logic Pro"
        repeat with s in UI elements of area
            set {stripName, inputs, outputs, sends, empty} to {"", {}, {}, {}, 0}
            repeat with f in (text fields of s whose description is "name")
                set stripName to value of f
            end repeat
            repeat with b in buttons of s
                set h to ""
                try
                    set h to help of b
                end try
                if h starts with "Input slot." then set end of inputs to description of b
                if h starts with "Output slot." then set end of outputs to description of b
                if h starts with "Send slot." then set empty to empty + 1
            end repeat
            repeat with g in groups of s
                if (count of (checkboxes of g whose description is "bypass")) is 1 and description of g starts with "Bus " then set end of sends to description of g
            end repeat
            set AppleScript's text item delimiters to "|"
            set end of out to stripName & tab & (inputs as text) & tab & (outputs as text) & tab & (sends as text) & tab & empty
        end repeat
    end tell
    set AppleScript's text item delimiters to linefeed
    return out as text
end run
"""

# Straight after a strip is created Logic drops a press on its slots too, so the press repeats until the menu opens.
PICK_ROUTE = MIXER_STRIPS + """
on run argv
    set {stripName, slotHelp, slotName} to items 1 thru 3 of argv
    set picks to items 4 thru -1 of argv
    set area to my mixerStrips()
    considering case
    tell application "System Events" to tell process "Logic Pro"
        set strips to {}
        repeat with s in UI elements of area
            if (value of text fields of s whose description is "name") is {stripName} then set end of strips to contents of s
        end repeat
        if (count of strips) is not 1 then error "found " & (count of strips) & " Mixer strips named " & stripName
        set slots to {}
        repeat with b in (buttons of (item 1 of strips) whose description is slotName)
            set h to ""
            try
                set h to help of b
            end try
            if h starts with slotHelp & "." then set end of slots to contents of b
        end repeat
        if (count of slots) is 0 then error stripName & " has no " & slotHelp & " reading " & slotName
        set slot to item 1 of slots
        if (count of menus of area) > 0 then error "a menu is already open in the Mixer; close it and run again"
        repeat with attempt from 1 to 10
            perform action "AXPress" of slot
            repeat 10 times
                if (count of menus of area) > 0 then exit repeat
                delay 0.1
            end repeat
            if (count of menus of area) > 0 then exit repeat
        end repeat
        if (count of menus of area) is 0 then error "the " & slotHelp & " menu on " & stripName & " did not open"
        set m to menu 1 of area
        try
            set leaf to m
            repeat with i from 1 to count of picks
                set wanted to item i of picks
                if not (exists menu item wanted of leaf) then error wanted & " is not in the menu; it offers " & (name of menu items of leaf)
                set choice to menu item wanted of leaf
                if name of choice is not wanted then error "the menu offers " & (name of choice) & ", not " & wanted
                if i < (count of picks) then set leaf to menu 1 of choice
            end repeat
            if not (enabled of choice) then error (item -1 of picks) & " is disabled"
        on error failure
            perform action "AXCancel" of m
            error failure
        end try
        perform action "AXPick" of choice
    end tell
    end considering
end run
"""


def pick_plugin(strip: str, category: str, plugin: str, formats: tuple[str, ...]) -> str:
    return _run(PICK_PLUGIN, f"picking {category} > {plugin} on {strip}", strip, category, plugin, *formats)


def read_routes() -> str:
    return _run(READ_ROUTES, "reading the Mixer's inputs, outputs and sends")


def pick_route(strip: str, slot_help: str, slot: str, path: tuple[str, ...]) -> None:
    _run(PICK_ROUTE, f"picking {' > '.join(path)} on {strip}'s {slot_help.lower()}", strip, slot_help, slot, *path)


def require_mixer() -> None:
    _run(REQUIRE_MIXER, "finding the Mixer")


def click_mixer_menu(menu: str, item: str) -> None:
    _run(CLICK_MIXER_MENU, f"clicking Mixer {menu} > {item}", menu, item)


def require_inspector() -> None:
    _run(REQUIRE_INSPECTOR, "finding the Inspector's Track: field")


def set_track_name(position: int, current: str, wanted: str) -> None:
    _run(SET_TRACK_NAME, f"setting track {position}'s name", str(position), current, wanted)


def _run(script: str, what: str, *args: str) -> str:
    try:
        done = subprocess.run(["osascript", "-e", script, *args], capture_output=True, text=True, timeout=CLICK_WITHIN_S)
    except subprocess.TimeoutExpired as e:
        raise ExecutorError(f"{what} did not finish within {CLICK_WITHIN_S:g}s") from e
    if done.returncode != 0:
        raise ExecutorError(f"{what} failed: {done.stderr.strip()}")
    return done.stdout.strip()
