# Mixhand — AI Vocal Producer for Logic Pro — Weekend Build Spec

This file is the source of truth for Claude Code. Read it fully before touching code. When in doubt, re-read §2 (Scope) and §10 (Rules).

## 1. Goal

Build a terminal agent that lets a producer select vocal tracks in Logic Pro, type one sentence ("make my chorus vocals bigger and more professional, keep my voice upfront"), and watch Logic transform: tracks duplicated, plugins inserted, buses and sends created, faders moved, every action logged with a one-line reason, all undoable with one Cmd+Z.

The deliverable is **one clean 40-second screen recording** of that happening on a real session. Everything in this spec serves that recording. Nothing else matters this weekend.

Name: **Mixhand**. Positioning: "Claude Code for Logic Pro."

## 2. Scope

### In scope (build in this order — see §9)
1. Executor: six primitives that move Logic (§4)
2. State reader: session → JSON (§5)
3. Planner: Claude API with a strict tool schema (§6)
4. CLI: terminal UI that streams the plan and action log (§7)
5. Demo run + recording (§8)

### Out of scope (do not build, do not scaffold, do not "leave a hook for")
- Automation curves (not exposed by Logic's Accessibility tree)
- Harmony / vocal generation, MIDI generation, any audio generation model
- Third-party plugins (stock Logic plugins only)
- Any DAW other than Logic Pro on macOS
- GUI, web UI, Electron, menu bar app. Terminal only.
- Reading MIDI note data inside regions
- Multi-user, cloud, auth, packaging, installer, Homebrew formula

If a task seems to require something out of scope, stop and ask.

## 3. Architecture

```
Terminal CLI (Python, rich/textual)
    │  prompt + streamed plan/log
    ▼
Planner  ──── Anthropic API, tool_use, model claude-sonnet-4-6 or latest ────
    │  validated actions (JSON)
    ▼
Executor ──── Python: pyobjc AX API, osascript/JXA, CGEvent keystrokes, mido→IAC
    │
    ▼
Logic Pro (Mixer window open, fixed layout)

State reader (Python, AX) ── session JSON ──▶ Planner
```

Language: **Python 3.11+** for everything. One repo, one venv.

Prior art to reuse, not rewrite: `koltyj/logic-pro-mcp` (Swift, MIT) and its fork `MongLong0214/logic-pro-mcp` already implement transport, track create/select/mute, mixer volume/pan, `insert_plugin`, `bypass_plugin`, undo/copy/paste, bounce, and an AX state reader for tracks/mixer. **Before implementing any primitive, check whether the LogicProMCP binary already does it reliably.** If it does, call it (subprocess or MCP client) and wrap it. Only hand-write what's missing or broken. Known gaps in prior art: aux/send creation, region duplication to a named track. Expect to write those.

Control channels, in order of preference for each operation:
1. **Logic key commands** via CGEvent keystrokes (most reliable — `Cmd+D` duplicate track, `Cmd+C/V`, `Cmd+Z`, etc.)
2. **Mackie Control emulation** via IAC Driver + `mido` (fader/pan/send levels — smooth, visible on camera)
3. **Accessibility API** via `pyobjc` (`ApplicationServices` AX*) for clicking plugin slots / send slots and navigating popup menus, and for reading state
4. **AppleScript** only for app lifecycle (launch, activate, open project)

Never touch the `.logicx` package contents. Never parse `ProjectData`.

## 4. Executor — the six primitives

Module: `mixhand/executor/`. Each primitive is a function that returns `ActionResult(ok: bool, detail: str, verified: bool)`. Every primitive must:
- log one line before acting and one after (`logging` → both stdout and `logs/actions.jsonl`)
- verify via the state reader after acting (re-scrape the affected track/strip)
- raise `ExecutorError` on failure — never silently continue
- be idempotent-safe: calling twice must not create two of something (check state first)

| # | Primitive | Signature | Strategy |
|---|-----------|-----------|----------|
| 1 | Duplicate track with region | `duplicate_track(source: str, new_name: str) -> ActionResult` | Select source track (LogicProMCP `select`, confirmed), Track › Other › New Track With Duplicate Settings and Content (copies the regions; answered in `SETUP.md`), rename the copy in the Inspector's `Track:` field through System Events, never by typing (`SETUP.md`). |
| 2 | Insert plugin | `insert_plugin(track: str, plugin: str, slot: int \| None = None) -> ActionResult` | In Mixer: AX-find channel strip by name → first empty Audio FX slot (or `slot`) → click → walk popup menu path (e.g. `EQ > Channel EQ`, `Dynamics > Compressor`, `Reverb > ChromaVerb`, `Delay > Stereo Delay`, `Dynamics > DeEsser 2`). Maintain a menu-path map in `executor/plugin_paths.py`. Close the plugin window that opens. |
| 3 | Set pan / volume | `set_pan(track, value: int)` (−64..63), `set_volume(track, db: float)` | Primary: Mackie Control via IAC (select strip bank, send fader/V-pot). Fallback: AX double-click value field in Mixer, type number, Enter. |
| 4 | Create aux with plugin | `create_aux(name: str, plugin: str) -> ActionResult` | Mixer › Options › Create New Auxiliary Channel Strip, then Create Tracks for Selected Channel Strips so LogicProMCP can see and bind it (answered in `SETUP.md`), rename it in the Inspector like primitive 1, then `insert_plugin`. No key command needed. The plugin is any primitive 2 can insert. |
| 5 | Add send | `add_send(track: str, aux: str) -> ActionResult` | AX, because LogicProMCP 3.16.0 refuses `set_send` and reads no sends. A new aux listens on `Input 1`, not a bus, so first set its Input slot to the lowest bus no strip uses (skipped when it already listens on one), then the track's first empty Send slot → `Bus > Bus N → <aux>`. The level is left where Logic puts it (−∞) and set by `set_send_level(track, aux, db)`, its own PR (`SETUP.md`). |
| 6 | Set plugin parameter | `set_plugin_param(track, plugin, param: str, value: float) -> ActionResult` | **Demo-limited.** Pre-map a small fixed set via Logic Controller Assignments → MIDI CC on IAC, documented in `SETUP.md`: Channel EQ high-pass freq, Compressor threshold + ratio, ChromaVerb mix/decay. Anything outside the map → `ExecutorError("param not mapped")`. Do not attempt AX control of plugin windows. |

Plus three utilities:
- `delete_track(track)` — LogicProMCP `logic_tracks.delete` bound by `track_ref`, then a fresh read must show exactly that track gone. Not a planner tool; it lets live tests clean up after themselves.
- `undo(n: int = 1)` — `Cmd+Z` × n.
- `begin_group(label) / end_group()` — records the count of Logic actions performed so `undo_group()` can reverse the whole AI action. (Logic has no native undo grouping we can drive; count and replay `Cmd+Z`.)

Environment assumptions (enforce in a `doctor` command, fail loudly if not met):
- Logic Pro is frontmost, Mixer is open (`X`), window at fixed size, screen at fixed resolution
- Terminal app has Accessibility + Automation permissions
- IAC Driver enabled with a bus named `Producer`, Logic has a Mackie Control surface pointed at it
- Project has a cycle region set to the target section
- Track names are unique

## 5. State reader

Module: `mixhand/state/`. `read_session() -> Session` (pydantic). Read via AX from the Mixer and LCD:

```json
{
  "project": {"tempo": 92, "key": "A major", "time_sig": "4/4"},
  "selection": {"start_bar": 33, "end_bar": 49, "label": "Chorus"},
  "tracks": [
    {"name": "Lead Vocal", "kind": "audio", "volume_db": -3.2, "pan": 0,
     "plugins": ["Pitch Correction"], "sends": [], "bus": null},
    {"name": "Adlib", "kind": "audio", "volume_db": -6.0, "pan": 0,
     "plugins": [], "sends": [], "bus": null}
  ],
  "auxes": [],
  "available_plugins": ["Channel EQ","Compressor","DeEsser 2","ChromaVerb",
                        "Space Designer","Stereo Delay","Tape Delay","Pitch Correction"]
}
```

Selection: read the cycle range from the LCD. If the LCD can't be read reliably, accept `--start-bar/--end-bar` CLI flags. If key can't be read, accept `--key`. **Do not spend more than 2 hours on the reader.** It is invisible on camera; a hand-written `session.json` with `--session-file` is an acceptable fallback for the demo.

Optional audio analysis (only after §9 milestone 4 is done): `analyze_stems(tracks) -> dict` — bounce the selected regions per track (`Cmd+B` / bounce-in-place to a temp folder), then `librosa` + `pyloudnorm`: integrated LUFS, crest factor, spectral centroid, energy in bands (80–250 Hz, 250–500, 2–4 kHz, 8 kHz+). Feed into the planner prompt. This is what makes explanations specific ("your loud phrases are ~7 dB above the quiet ones").

## 6. Planner

Module: `mixhand/planner/`. Uses the Anthropic Python SDK with `tools=` set to exactly the six primitives plus `explain`. Streaming on.

System prompt essentials (write in `planner/system_prompt.md`, keep it editable):
- You are a vocal producer working inside the user's Logic Pro session. You can only act through the provided tools. Stock Logic plugins only, from `available_plugins`.
- Before calling tools, write a short plan in plain English (3–6 sentences) describing what you'll do and why. Then execute.
- For every tool call, include a `reason` argument: one sentence, specific to this session (reference the track, the problem, the number).
- Typical "bigger, more professional chorus vocal" plan: lead chain (Channel EQ HPF ~80 Hz, Compressor ~3:1, DeEsser), two doubles panned L/R with EQ, a reverb aux (ChromaVerb) and a delay aux (Stereo Delay) with sends from all vocal tracks, doubles 4–8 dB under the lead. Adapt to what's actually in the session.
- Never delete, never touch non-vocal tracks unless asked, never change tempo/key/project settings.
- Keep it to ≤ 14 actions. A demo must complete in under 45 seconds of Logic activity.

Tool schema (`planner/tools.py`) — every tool has `reason: str` as a required field. Validation before execution (`planner/validate.py`):
1. Referenced track exists in the current session (or was created earlier in this plan)
2. Plugin is in `available_plugins`
3. Numeric values within bounds (pan −64..63, volume −17..+6 dB — Logic's fader is too coarse below −17 dB to land near a request, see `SETUP.md` — send −60..0 dB)
4. No duplicate track names
5. Reject and re-prompt the model with the validation error (max 2 retries), never execute an invalid action

Execution loop: plan → validate → execute one action → verify → feed `ActionResult` back as `tool_result` → next. If an action fails, tell the model; it may adapt or stop. On any `ExecutorError`, print the failure and offer `undo group`.

Conversational follow-ups ("make the doubles quieter") reuse the same loop with prior messages retained.

## 7. CLI

`mixhand` entry point (`typer`). Commands:
- `doctor` — checks §4 environment assumptions, prints pass/fail
- `state` — prints session JSON
- `produce "<prompt>"` — the main loop, streamed with `rich`: plan text, then a live action log (`✔ Duplicated Lead Vocal → Chorus Double L — panned −40 so the lead stays centered`)
- `undo` — undoes the last AI action group
- `explain` — reprints the reasons for the last group

Look: dark terminal, monospace, minimal color (green ✔, red ✖, dim reasons). This window sits on top of Logic in the recording — it is the product's face. No spinners that hide the log.

## 8. Demo

Session: 8–16 bar chorus, tracks `Lead Vocal`, `Adlib`, optionally `Vocal Double` (dry, Pitch Correction on lead only). Cycle set to the chorus.

Prompt: `Make my chorus vocals sound bigger and more professional. Keep my voice upfront.`

Shot list (single take, no cuts if possible):
1. 5s: play the dry chorus
2. type the prompt in the terminal
3. ~30s: plan streams, Logic transforms behind it
4. play the produced chorus
5. `undo` — everything vanishes

Run it ≥15 times before recording. Capture with Screen Studio; audio via Loopback or a Logic bounce dropped in post. Final: 35–45s, captions only.

## 9. Milestones — do them in order, don't skip ahead

| # | Milestone | Done when |
|---|-----------|-----------|
| 1 | `doctor` passes; `insert_plugin("Lead Vocal", "Channel EQ")` works 10/10 runs | reliability, not features |
| 2 | All six primitives pass a manual checklist (`tests/manual_checklist.md`) 10/10 each | |
| 3 | `state` prints a correct session for the demo project | or `--session-file` fallback in place |
| 4 | `produce` runs the full vocal plan end-to-end with validation, verification, and `undo` working | the core thesis proven |
| 5 | Demo project + prompt produce an audibly better chorus in a clean 45s run | |
| 6 | (stretch) stem analysis feeding specific reasons | only if 1–5 are done by Sunday noon |
| 7 | (stretch) `add_midi_part` — Claude writes a chord-following MIDI part, import via MIDI file, Logic stock instrument | closing shot for the video |

## 10. Rules for Claude Code

- **Reliability over features.** A primitive that works 7/10 is not done. Add retries with re-scrape, add waits keyed to AX state changes rather than fixed sleeps.
- **Delete only what was named, never save.** Mixhand deletes only inside `MIXHAND_PROJECT`, and only a target named and bound to LogicProMCP's `track_ref`. It confirms each delete from a fresh read and names the undo. No overwrite, no save: the tool never calls `Cmd+S`. Test on a copy of the project.
- **Log everything.** Every AX click, keystroke, and MIDI message goes to `logs/actions.jsonl` with timestamp and outcome.
- **Fail loudly.** No bare `except`. No `pass`. If Logic isn't frontmost, stop.
- **Don't fight Logic's UI.** If a menu path is unstable, check the `SETUP.md` fallback: save a `Lead Vocal Chain` and `Double Chain` channel strip setting by hand and implement `load_channel_strip_setting(track, name)` as an alternate path for primitive 2. Prefer that over hours of AX debugging.
- **Ask before expanding scope.** If a milestone needs something in §2 out-of-scope, stop and ask.
- **Keep the planner prompt editable in Markdown**, not buried in Python strings.
- **Verify AX element paths against the running Logic version** — don't trust paths from prior-art repos blindly; they drift between versions. Record the Logic version in `SETUP.md`.
- **Commit after every milestone** with a message that names the milestone.

## 11. Repo layout

```
mixhand/
  SPEC.md                 ← this file
  SETUP.md                ← permissions, IAC, Mackie, key commands, CC map, Logic version
  pyproject.toml
  mixhand/
    cli.py
    executor/  {__init__, ax.py, keys.py, mackie.py, plugin_paths.py, primitives.py, group.py}
    state/     {__init__, reader.py, models.py, analyze.py}
    planner/   {__init__, system_prompt.md, tools.py, validate.py, loop.py}
  tests/
    manual_checklist.md   ← 10-run checklist per primitive, filled in by hand
    test_validate.py      ← the only automated tests worth writing this weekend
  logs/
  demo/
    README.md             ← shot list, prompt, project notes
```

## 12. Open questions (answer by experiment in milestone 1, record the answer in SETUP.md)

- Does `Cmd+D` reliably create a duplicate track *without* regions, so region paste is deterministic?
- Which AX role/identifier exposes empty Audio FX and Send slots on a channel strip in the current Logic version?
- Does Mackie Control fader movement over IAC reflect in the Mixer without focus issues?
- Can the LCD cycle range be read via AX, or do we take bars from CLI flags?
- Does LogicProMCP's `insert_plugin` work for stock plugins by name on this Logic version? If yes, wrap it and skip hand-writing primitive 2.
