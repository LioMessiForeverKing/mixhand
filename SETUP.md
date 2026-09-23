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
roughly centre. Last, it duplicates `Lead Vocal` to `Lead Vocal Double` with its regions, checks a second
call adds nothing, undoes both steps and checks the session is back where it started, ten times.
It then duplicates `Lead Vocal Double` again, deletes it, undoes the delete, deletes it once more and
checks a second delete is refused, ten times. Either test deletes a leftover `Lead Vocal Double` if
it fails partway.
`Lead Vocal` needs at least one region for that part. Keep your hands off Logic while it runs.

## SPEC §12, answered

| Question | Answer |
|---|---|
| Does LogicProMCP's `insert_plugin` work for stock plugins on this Logic? | **Yes.** `logic_plugins.insert_verified` walks `EQ > Channel EQ` and reads the slot back. Before retries, 17 of 20 live runs passed; each failure was a refusal before any plugin was chosen (`slot_popup_menu_not_found`, `safe_to_retry`). Mixhand now re-reads the slot and retries up to three times, and the next 20 runs all passed without needing a retry. It supports only Gain, Channel EQ and Compressor. |
| Which AX element exposes empty Audio FX and Send slots? | Empty inserts: LogicProMCP's `get_inventory` reports them (`read_status: empty`). Empty sends: LogicProMCP says an empty send slot exposes no `AXValue`, `AXValueDescription` or `AXTitle`. Primitive 5 has to find another way (milestone 2). |
| Can the cycle range be read via AX? | Only whether cycle is on. `logic://transport/state` has `isCycleEnabled` but no start or end bar. Plan on `--start-bar/--end-bar` (milestone 3). |
| Does `Cmd+D` create a duplicate track without regions? | **Yes**, and primitive 1 doesn't use it. `Cmd+D` is Track › Other › New Track With Duplicate Settings: an empty track under the source, with the source's name. The item beside it, **New Track With Duplicate Settings and Content**, copies the regions too, so `duplicate_track` clicks that one and needs no copy and paste. LogicProMCP 3.16.0 can't call it, so Mixhand clicks it through System Events after LogicProMCP has selected the source and confirmed the selection. The menu names are English; another Logic language needs them re-read. |
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

- LogicProMCP speaks volume as a 0..1 contract, never dB. `mixhand/executor/fader.py` holds
  Logic's dB at each of the fader's 234 raw positions, read off the fader's AX value text on
  12.3.1, and mirrors LogicProMCP's contract curve. Re-measure both if either version changes.

- A fresh LogicProMCP process reports a placeholder track list (`Track 1`) for the first second or
  two. Mixhand polls until the list reads live, and fails loudly after 10 s rather than
  treating the placeholder as the session.
- LogicProMCP's own `doctor` warns that 12.3.1 is newer than its validated 12.3. The runs
  above are the evidence that it works here.
