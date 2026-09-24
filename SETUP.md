# Setup

What this Mac needs before Mixhand can drive Logic. `mixhand doctor` checks all of it and says
which part is missing.

## Verified on

| | |
|---|---|
| Logic Pro | **12.3.1** — `doctor` fails on any other version until it is re-verified |
| macOS | 26.6.2 |
| LogicProMCP | **3.16.0** — `doctor` fails on any other version |
| Python | 3.12 via uv (`requires-python >= 3.11`) |

## 1. Install LogicProMCP

Mixhand drives Logic through [LogicProMCP](https://github.com/MongLong0214/logic-pro-mcp)
(MIT). Its `insert_plugin` works on Logic 12.3.1, so primitive 2 wraps it instead of
hand-writing the menu walk (SPEC §12).

Use the release binary. Building from source needs a working Swift toolchain, and on this Mac
`swift build` crashes inside Command Line Tools (`swift-package`: symbol not found in
`BuildServerProtocol`).

```bash
gh release download v3.16.0 -R MongLong0214/logic-pro-mcp -p 'LogicProMCP-macOS-arm64.tar.gz' -p SHA256SUMS.txt
shasum -a 256 -c SHA256SUMS.txt --ignore-missing
tar xzf LogicProMCP-macOS-arm64.tar.gz
install -m 755 LogicProMCP ~/.local/bin/LogicProMCP
```

The tarball's SHA-256 is `30f632d83f70e02e67754e6cfe8e809a4d218b78a4f47bc58ff374fbdd74bf91`.

## 2. Grant permissions

macOS charges permissions to the app that hosts the terminal, not the shell inside it. For
Mixhand run from Superset, that app is **Superset**.

- **Privacy & Security → Accessibility:** turn the host app on. This also covers PostEvent.
- **Automation → Logic Pro** and **Automation → System Events:** macOS asks the first time.

`LogicProMCP --check-permissions` prints all four. No restart of the host app was needed.

## 3. Configure

```bash
export MIXHAND_LOGICPROMCP=~/.local/bin/LogicProMCP
export MIXHAND_PROJECT=~/Music/Logic/testprojectformixhand.logicx
```

`MIXHAND_PROJECT` is the only project Mixhand will touch. Every write and every undo first
checks that it is the front document, and refuses otherwise. Point it at a copy, never at a
song you care about. Mixhand never saves.

## 4. Check

```bash
uv sync
uv run mixhand doctor
MIXHAND_LIVE=1 uv run pytest -m live
```

The live suite inserts Channel EQ on `Lead Vocal`, checks a second call adds nothing, undoes it,
and checks the slot is empty again, ten times. It then sets `Lead Vocal`'s volume to ten levels
and its pan to ten positions, checks a repeat call lands in the same place, and ends at 0 dB and
roughly centre. Next, it duplicates `Lead Vocal` to `Lead Vocal Double` with its regions, checks a second
call adds nothing, undoes both steps and checks the session is back where it started, ten times.
It then duplicates `Lead Vocal Double` again, deletes it, undoes the delete, deletes it once more and
checks a second delete is refused, ten times. Either test deletes a leftover `Lead Vocal Double` if
it fails partway.
`Lead Vocal` needs at least one region for that part.
Last, it creates `Mixhand Aux` with Channel EQ, ChromaVerb and Stereo Delay in turn, checks a
second call adds nothing, then undoes it one step at a time and checks the fourth undo is *Create
New Auxiliary Channel Strip* and the session and Edit menu are back where they started, ten times
each. Then it creates `Mixhand Aux` with ChromaVerb and `Mixhand Delay` with Stereo Delay, checks
Stereo Delay sits in the second aux's top slot, and undoes all eight steps by title, ten times. It
then puts Stereo Delay under an empty slot on `Lead Vocal`, checks ChromaVerb is refused there with
nothing changed, and undoes five steps, ten times. If it fails partway it says so rather than
guessing how many undos to send.
Keep your hands off Logic while it runs, and keep the screen awake (`caffeinate -d`).

## SPEC §12, answered

| Question | Answer |
|---|---|
| Does LogicProMCP's `insert_plugin` work for stock plugins on this Logic? | **Yes.** `logic_plugins.insert_verified` walks `EQ > Channel EQ` and reads the slot back. Before retries, 17 of 20 live runs passed; each failure was a refusal before any plugin was chosen (`slot_popup_menu_not_found`, `safe_to_retry`). Mixhand now re-reads the slot and retries up to three times, and the next 20 runs all passed without needing a retry. It supports only Gain, Channel EQ and Compressor; Mixhand picks ChromaVerb and Stereo Delay from the menu itself (see Known issues). |
| Which AX element exposes empty Audio FX and Send slots? | Empty inserts: LogicProMCP's `get_inventory` reports them (`read_status: empty`). Empty sends: LogicProMCP says an empty send slot exposes no `AXValue`, `AXValueDescription` or `AXTitle`. Mixhand reads the Mixer strip itself: a slot's description is its current setting and its help text names its kind (`Input slot.`, `Output slot.`, `Send slot.`), so an empty send is a button with help `Send slot.` and a filled one is a group described `Bus N` holding a `bypass` checkbox. |
| Can the cycle range be read via AX? | Only whether cycle is on. `logic://transport/state` has `isCycleEnabled` but no start or end bar. Plan on `--start-bar/--end-bar` (milestone 3). |
| Does `Cmd+D` create a duplicate track without regions? | **Yes**, and primitive 1 doesn't use it. `Cmd+D` is Track › Other › New Track With Duplicate Settings: an empty track under the source, with the source's name. The item beside it, **New Track With Duplicate Settings and Content**, copies the regions too, so `duplicate_track` clicks that one and needs no copy and paste. LogicProMCP 3.16.0 can't call it, so Mixhand clicks it through System Events after LogicProMCP has selected the source and confirmed the selection. The menu names are English; another Logic language needs them re-read. |
| Can LogicProMCP create an aux? | **No.** 3.16.0 creates audio, instrument, drummer and external MIDI tracks only, and has no send write either. `create_aux` clicks the Mixer's own Options menu: *Create New Auxiliary Channel Strip*, then *Create Tracks for Selected Channel Strips*. An aux without a track never appears in `logic://tracks`, so it gets one; LogicProMCP then lists it with `type: aux` and a `track_ref`, and `insert_plugin` reaches it. Creating it takes four undo steps: the strip, its track, the rename and the plugin; three when Logic already named it as asked, so there is no rename. 10 of 10 live runs passed. |
| Does Mackie Control over IAC move faders without focus issues? | Not needed for primitive 3. LogicProMCP's `logic_mixer.set_volume` and `set_pan` move the strip by AX increments and read it back, 20 of 20 live runs, without Logic in front. Logic moves by 10 raw units per increment, so pan lands within ±5 and volume within 0.5 dB from −5 dB up and 1.1 dB down to −17 dB. Below −17 dB one increment is 2.5 to 25 dB, so `set_volume` refuses it rather than land far from the request; this narrows SPEC's −60 dB floor on purpose. Mixhand reports where it actually landed. Mackie is untested. |

## Known issues

- LogicProMCP reads the track list live only while Logic is the frontmost app. With the terminal in
  front, `logic://tracks` stays on names from the project file (`Track 1`), and Mixhand fails after
  10 s. Bring Logic to the front before running anything.
- `duplicate_track` does not use LogicProMCP's `logic_tracks.rename`. That command falls back to
  typing the name after Track › Rename Track, and when the field is not focused yet, the keystrokes
  reach Logic as key commands: about 3 in 80 live duplicates came out misnamed (` Double`), and one
  of those runs lost a region. Mixhand sets the name in the Inspector's `Track:` field through
  System Events instead, with no keystrokes. It first requires that the copy's header
  (`Track N “name”`) is the only selected track and that the Inspector names it. Afterwards it
  waits for the header's label to change, then checks by ref that no other track changed. An
  Inspector rename is one undo step (Edit shows *Undo Renaming*). The `Track:` label is English;
  another Logic language needs it re-read.
- Keep the Inspector shown (View › Show Inspector). `duplicate_track` checks for its `Track:` field
  before touching anything, and refuses if it is hidden.
- The Inspector renames whichever track is selected. If you click another track in the tenth of a
  second between Mixhand's selection check and its write, that track is renamed instead. Mixhand
  then reports the mismatch and says to check Logic before undoing.

- `create_aux` needs exactly one Mixer showing: the pane in the Tracks window (`X`) or the Mixer
  window, not both. It refuses before touching anything otherwise. The Mixer's Edit, Options and
  View menus are buttons inside it, not menu-bar items, and their names are English.
- Straight after a strip is created, Logic drops a click on a Mixer menu button, and the menu never
  opens. `create_aux` repeats the click until the menu shows, up to ten times; live, the second
  click needed two. Close a Logic menu opened this way with AXCancel; System Events' Escape left
  it open. Before the retry, a failed second click once left Logic not answering AppleScript until
  it was restarted, and LogicProMCP then reports no front project. The cause was not pinned down.
- LogicProMCP inserts only Gain, Channel EQ and Compressor, so Mixhand picks ChromaVerb and Stereo
  Delay from the slot's plug-in menu itself (`pick_plugin`): the strip found by its name field, not
  its description, which keeps `Aux 1` after a rename; its topmost empty slot; that slot's own
  *Open plug-in menu* action; a check that the menu opened beside it; then *Reverb › ChromaVerb*
  or *Delay › Stereo Delay*. A new aux offers only *Mono* and *Mono->Stereo*, so it takes *Stereo*
  where offered and *Mono->Stereo* otherwise, never *Mono*. The slot is read back through
  `get_inventory` by name, and the plug-in window that opens is closed with Window › Hide All
  Plug-in Windows. Logic labels a Stereo Delay slot `St-Delay`, so the readback expects that.
  Once any strip holds a plugin, every strip shows a second, clipped empty row, and AX lists it
  before the first, so the pick takes the empty slot highest on the strip. `get_inventory` numbers
  a plugin's slot from 0 even with an empty slot above it, and lists plugins out of Mixer order
  when one fills such a gap, so the readback would confirm the wrong position: the pick refuses a
  strip with an empty slot above a plugin before opening the menu. Any other plugin is refused before anything is created. The menu names are English. 30 of 30 live
  aux runs passed, 10 each with Channel EQ, ChromaVerb and Stereo Delay, and the 20 menu runs
  passed again after review; each picked *Mono->Stereo*, and the result names the format picked.
- An aux strip left without a track, for example by a run killed between the two clicks, is invisible
  to LogicProMCP, so `create_aux` cannot see it. Undo it, or delete the strip in the Mixer.
- `add_send` routes through the Mixer, because LogicProMCP 3.16.0 refuses `set_send` ("not yet
  deterministic") and its routing graph covers no sends. A new aux listens on `Input 1`, and choosing
  a send to a bus nothing listens on makes Logic create another aux, so the aux's Input slot is set
  first: to the lowest bus no strip's input, output or send uses, or kept when it is already a bus.
  A hidden track's strip leaves the Mixer (View › Follow Hide) and would make its bus look free,
  as does a collapsed track stack's subtrack, so `add_send` refuses unless every track has a strip
  showing, counted per name so a visible namesake cannot stand in for a hidden copy.
  The send is then picked as *Bus › Bus N → aux*, a label that names the aux, so the pick itself
  checks the route. Each change is one undo step (*Change Input in Channel Strip*, *Change Send in
  Channel Strip*), and Logic rebuilds the strips after each, so a read in the next second can fail;
  `add_send` waits until the Mixer reads back the one route asked for. 10 of 10 live runs passed,
  each on Bus 1. Buses past 32 sit in submenus (*33 - 64* …) in the send menu; that the Input menu
  nests the same way is assumed, not yet seen live.
- A new send starts at −∞, and the aux's own fader does too, so it is silent until both are raised.
  The send knob takes an `AXValue` from 0 to 2,130,706,432 and reads back Logic's dB in
  `AXValueDescription`, but each write moves it one step toward the value written, however far that
  is: 2 dB below −48 dB, 1 dB down to −48 dB, 0.1 dB from −6 dB up. So `set_send_level` steps it
  inside one `osascript` (−∞ to 0 dB is 127 steps, under 3 s) until it reads within half a step of
  the level asked: tenths from −6 dB, whole dB below. Where it lands depends on the direction it came
  from (up lands on −8, −7; down from −6 lands on −7.1, −8.1), so below −6 dB it can read 0.1 dB off.
  The knob is not inside its send's `Bus N` group but beside it, so it is matched to that row by
  position. Knob writes make no undo step, in one script or many, fast or slow, straight after the
  send or after another edit, and neither do LogicProMCP's volume and pan moves: undoing *Change Send
  in Channel Strip* removes the send with its level. `set_send_level` names the level it replaced
  instead. −60..0 dB, as SPEC §6 bounds a send.
- After a run that creates and then undoes an aux, Logic can stop answering Apple events for 100 to
  more than 400 s while System Events still reach it. Meanwhile LogicProMCP reports the front
  project as `None`, so every primitive refuses, and the Edit menu and the Mixer can show state
  from before the last edits, so an Undo title read then cannot be trusted. Wait for
  `tell application "Logic Pro" to get name of front document` to answer before the next live run;
  once, only a restart of Logic cleared it.

- LogicProMCP speaks volume as a 0..1 contract, never dB. `mixhand/executor/fader.py` holds
  Logic's dB at each of the fader's 234 raw positions, read off the fader's AX value text on
  12.3.1, and mirrors LogicProMCP's contract curve. Re-measure both if either version changes.

- A fresh LogicProMCP process reports a placeholder track list (`Track 1`) for the first second or
  two. Mixhand polls until the list reads live, and fails loudly after 10 s rather than
  treating the placeholder as the session.
- LogicProMCP's own `doctor` warns that 12.3.1 is newer than its validated 12.3. The runs
  above are the evidence that it works here.
