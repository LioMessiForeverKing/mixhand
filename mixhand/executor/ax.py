import subprocess

from mixhand.executor import ExecutorError

CLICK_WITHIN_S = 10.0
STEP_WITHIN_S = 30.0
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

# Once any strip holds a plugin, every strip shows a second, clipped empty row, and AX lists it first.
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
        if (count of slots) is 0 then error "found no empty insert slot on " & stripName
        set slot to item 1 of slots
        set {topmost, lowest} to {item 2 of (position of slot as list), item 2 of (position of slot as list)}
        repeat with b in slots
            set y to item 2 of (position of b as list)
            if y < topmost then set {slot, topmost} to {contents of b, y}
            if y > lowest then set lowest to y
        end repeat
        repeat with g in groups of (item 1 of strips)
            set y to item 2 of (position of g as list)
            if y > topmost and y < lowest then error stripName & " has an empty insert slot above a plug-in; close the gap in Logic and run again"
        end repeat
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


# A send's knob is not inside its row's group, so it is matched to the row by where it sits.
# Each write moves the knob one step toward the value written, so the far ends step it up or down.
SEND_KNOB = MIXER_STRIPS + """
on sendKnob(stripName, busName)
    set area to my mixerStrips()
    considering case
    tell application "System Events" to tell process "Logic Pro"
        set strips to {}
        repeat with s in UI elements of area
            if (value of text fields of s whose description is "name") is {stripName} then set end of strips to contents of s
        end repeat
        if (count of strips) is not 1 then error "found " & (count of strips) & " Mixer strips named " & stripName
        set s to item 1 of strips
        set sendRows to {}
        repeat with g in groups of s
            if description of g is busName and (count of (checkboxes of g whose description is "bypass")) is 1 then set end of sendRows to contents of g
        end repeat
        if (count of sendRows) is not 1 then error "found " & (count of sendRows) & " sends on " & busName & " on " & stripName
        set {rowX, rowY} to position of item 1 of sendRows
        set {rowW, rowH} to size of item 1 of sendRows
        set knobs to {}
        repeat with k in (sliders of s whose description is "send knob")
            set {knobX, knobY} to position of k
            set {knobW, knobH} to size of k
            set centreY to knobY + knobH / 2
            if centreY >= rowY and centreY <= rowY + rowH then set end of knobs to contents of k
        end repeat
        if (count of knobs) is not 1 then error "found " & (count of knobs) & " send knobs beside " & stripName & "'s send on " & busName
        return item 1 of knobs
    end tell
    end considering
end sendKnob

on level(k)
    tell application "System Events" to tell process "Logic Pro"
        repeat 20 times
            try
                set d to value of attribute "AXValueDescription" of k
                if d is not missing value and d is not "" then return d
            end try
            delay 0.05
        end repeat
    end tell
    error "the send knob gave no level"
end level

on tenths(d)
    if d is "-∞" then return -10000
    set AppleScript's text item delimiters to "."
    set parts to text items of d
    set AppleScript's text item delimiters to ""
    if (count of parts) is not 2 or length of (item 2 of parts) is not 1 then error "the send knob reads " & d & ", not a level in dB"
    return ((item 1 of parts) & (item 2 of parts)) as integer
end tenths
"""

READ_SEND_LEVEL = SEND_KNOB + """
on run {stripName, busName}
    return my level(my sendKnob(stripName, busName))
end run
"""

STEP_SEND_LEVEL = SEND_KNOB + """
on run {stripName, busName, target, tolerance, limit}
    set {target, tolerance, limit} to {target as integer, tolerance as integer, limit as integer}
    set k to my sendKnob(stripName, busName)
    set was to my level(k)
    set d to was
    set steps to 0
    set still to 0
    repeat
        set gap to (my tenths(d)) - target
        if gap <= tolerance and gap >= -tolerance then exit repeat
        if steps >= limit then error "the send still read " & d & " dB after " & steps & " steps"
        tell application "System Events" to tell process "Logic Pro"
            if gap < 0 then
                set value of k to 2.130706432E+9
            else
                set value of k to 0
            end if
        end tell
        set steps to steps + 1
        set now to my level(k)
        if now is d then
            set still to still + 1
            if still >= 3 then error "the send knob stopped moving at " & d & " dB"
        else
            set still to 0
        end if
        set d to now
    end repeat
    return was & tab & d & tab & steps
end run
"""

# Only the plug-in window's Controls view labels its rows, and its View menu never appears in AX, so it is picked by keyboard.
SET_DELAY_PARAM = MIXER_STRIPS + """
on run {stripName, label, wanted, target, limit}
    set area to my mixerStrips()
    considering case
    tell application "System Events" to tell process "Logic Pro"
        set strips to {}
        repeat with s in UI elements of area
            if (value of text fields of s whose description is "name") is {stripName} then set end of strips to contents of s
        end repeat
        if (count of strips) is not 1 then error "found " & (count of strips) & " Mixer strips named " & stripName
        set slots to groups of (item 1 of strips) whose description is "St-Delay"
        if (count of slots) is not 1 then error "found " & (count of slots) & " Stereo Delay slots on " & stripName
        set opened to false
        if (count of (my windowsTitled(stripName))) is 0 then
            click (first button of (item 1 of slots) whose description is "open")
            set opened to true
            repeat 30 times
                if (count of (my windowsTitled(stripName))) > 0 then exit repeat
                delay 0.1
            end repeat
        end if
        set wins to my windowsTitled(stripName)
        if (count of wins) is not 1 then error "found " & (count of wins) & " plug-in windows titled " & stripName
        set w to item 1 of wins
        try
            if (value of static texts of w) does not contain {"Stereo Delay"} then error "the window titled " & stripName & " is not its Stereo Delay"
            my controlsView(w)
            set out to my setRow(w, label, wanted, target, limit as integer)
        on error failure
            if opened then click (first button of w whose description is "close")
            error failure
        end try
        if opened then click (first button of w whose description is "close")
        return out
    end tell
    end considering
end run

on windowsTitled(stripName)
    tell application "System Events" to tell process "Logic Pro"
        set found to {}
        repeat with x in windows
            set t to title of x
            if t is stripName then set end of found to contents of x
        end repeat
        return found
    end tell
end windowsTitled

on controlsView(w)
    tell application "System Events" to tell process "Logic Pro"
        set v to first menu button of w whose description is "view"
        if title of v is "Controls" then return
        set frontmost to true
        perform action "AXRaise" of w
        delay 0.2
        perform action "AXShowMenu" of v
        delay 0.5
        key code 125
        delay 0.2
        key code 36
        repeat 20 times
            if title of v is "Controls" then return
            delay 0.1
        end repeat
        error "the plug-in window did not switch to its Controls view"
    end tell
end controlsView

on labelled(w, label)
    tell application "System Events" to tell process "Logic Pro"
        set hits to {}
        repeat with r in rows of table 1 of scroll area 1 of w
            set c to UI element 1 of r
            if (value of static texts of c) contains {label} then set end of hits to c
        end repeat
        if (count of hits) is not 1 then error "found " & (count of hits) & " rows labelled " & label
        return item 1 of hits
    end tell
end labelled

on readout(s)
    tell application "System Events" to tell process "Logic Pro"
        repeat 20 times
            try
                set d to value of attribute "AXValueDescription" of s
                if d is not missing value and d is not "" then return d
            end try
            delay 0.05
        end repeat
    end tell
    error "the slider gave no value"
end readout

on setRow(w, label, wanted, target, limit)
    tell application "System Events" to tell process "Logic Pro"
        set c to my labelled(w, label)
        if target is "" then
            set p to pop up button 1 of c
            set was to value of p
            if was is wanted then return was & tab & was & tab & 0
            set synced to value of checkbox 1 of my labelled(w, "Beat Sync:")
            if synced as text is not in {"1", "true"} then error "Beat Sync is off, so a note does not set the delay time"
            perform action "AXPress" of p
            repeat 20 times
                if (count of (UI elements of p whose role is "AXMenu")) > 0 then exit repeat
                delay 0.1
            end repeat
            if (count of (UI elements of p whose role is "AXMenu")) is 0 then error "the " & label & " menu did not open"
            set m to item 1 of (UI elements of p whose role is "AXMenu")
            set hits to {}
            repeat with i in menu items of m
                set t to title of i
                if t is wanted then set end of hits to contents of i
            end repeat
            if (count of hits) is not 1 then
                perform action "AXCancel" of m
                error "the " & label & " menu offers " & (count of hits) & " items titled " & wanted
            end if
            perform action "AXPress" of item 1 of hits
            repeat 20 times
                if value of p is wanted then exit repeat
                delay 0.1
            end repeat
            return was & tab & (value of p) & tab & 1
        end if
        set s to slider 1 of group 1 of c
        set was to my readout(s)
        set d to was
        set steps to 0
        set still to 0
        repeat while d is not wanted
            if steps >= limit then error label & " still read " & d & " after " & steps & " steps"
            set value of s to (target as integer)
            set steps to steps + 1
            set now to my readout(s)
            if now is d then
                set still to still + 1
                if still >= 3 then error label & " stopped moving at " & d
            else
                set still to 0
            end if
            set d to now
        end repeat
        return was & tab & d & tab & steps
    end tell
end setRow
"""

# A menu item's title is only refreshed when its menu opens.
UNDO_TITLE = """
tell application "System Events" to tell process "Logic Pro"
    set edit to menu 1 of menu bar item "Edit" of menu bar 1
    click menu bar item "Edit" of menu bar 1
    delay 0.3
    set undoing to name of menu item 1 of edit
    perform action "AXCancel" of edit
    return undoing
end tell
"""


def pick_plugin(strip: str, category: str, plugin: str, formats: tuple[str, ...]) -> str:
    return _run(PICK_PLUGIN, f"picking {category} > {plugin} on {strip}", strip, category, plugin, *formats)


def read_routes() -> str:
    return _run(READ_ROUTES, "reading the Mixer's inputs, outputs and sends")


def pick_route(strip: str, slot_help: str, slot: str, path: tuple[str, ...]) -> None:
    _run(PICK_ROUTE, f"picking {' > '.join(path)} on {strip}'s {slot_help.lower()}", strip, slot_help, slot, *path)


def read_send_level(strip: str, bus: int) -> str:
    return _run(READ_SEND_LEVEL, f"reading {strip}'s send on Bus {bus}", strip, f"Bus {bus}")


def step_send_level(strip: str, bus: int, target: int, tolerance: int, limit: int) -> tuple[str, str, int]:
    out = _run(
        STEP_SEND_LEVEL, f"moving {strip}'s send on Bus {bus}", strip, f"Bus {bus}", str(target), str(tolerance), str(limit),
        timeout=STEP_WITHIN_S,
    )
    was, landed, steps = out.split("\t")
    return was, landed, int(steps)


def set_delay_param(strip: str, label: str, wanted: str, target: str, limit: int) -> tuple[str, str, int]:
    out = _run(
        SET_DELAY_PARAM, f"setting {strip}'s Stereo Delay {label.rstrip(':')}", strip, label, wanted, target, str(limit),
        timeout=STEP_WITHIN_S,
    )
    was, landed, steps = out.split("\t")
    return was, landed, int(steps)


def undo_title() -> str:
    return _run(UNDO_TITLE, "reading Logic's Undo menu item")


def require_mixer() -> None:
    _run(REQUIRE_MIXER, "finding the Mixer")


def click_mixer_menu(menu: str, item: str) -> None:
    _run(CLICK_MIXER_MENU, f"clicking Mixer {menu} > {item}", menu, item)


def require_inspector() -> None:
    _run(REQUIRE_INSPECTOR, "finding the Inspector's Track: field")


def set_track_name(position: int, current: str, wanted: str) -> None:
    _run(SET_TRACK_NAME, f"setting track {position}'s name", str(position), current, wanted)


def _run(script: str, what: str, *args: str, timeout: float = CLICK_WITHIN_S) -> str:
    try:
        done = subprocess.run(["osascript", "-e", script, *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise ExecutorError(f"{what} did not finish within {timeout:g}s") from e
    if done.returncode != 0:
        raise ExecutorError(f"{what} failed: {done.stderr.strip()}")
    return done.stdout.strip()
