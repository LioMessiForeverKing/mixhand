# Mixhand

Claude Code for Logic Pro. Type one sentence about your vocals in the terminal, and Mixhand plans
the change and carries it out in Logic, one logged action at a time, each with its reason. One
command takes a finished run back.

```console
$ mixhand produce "Make my chorus vocals sound bigger and more professional. Keep my voice upfront."
```

Mixhand is a terminal agent for macOS. It drives Logic through
[LogicProMCP](https://github.com/MongLong0214/logic-pro-mcp) and the Accessibility API, and plans
with the OpenAI Responses API.

## What it can do

The planner has eight tools, each run and checked against Logic before the next one starts:

| Tool | Does |
|---|---|
| `duplicate_track` | Copies a track with its regions and settings, under a new name |
| `insert_plugin` | Adds Gain, Channel EQ, Compressor, ChromaVerb or Stereo Delay to a track |
| `set_volume` | Moves a fader, −17 to +6 dB |
| `set_pan` | Pans a track, −64 to 63 |
| `create_aux` | Makes a named aux with one of those plugins on it |
| `add_send` | Sends a track to an aux over the first free bus |
| `set_send_level` | Sets a send this run added, or one on a track it created, −60 to 0 dB |
| `set_plugin_param` | On a plugin this run inserted, or a track it created: Compressor Threshold, Channel EQ Peak 1–4 Frequency and Gain, Stereo Delay Feedback, Crossfeed and Note |

Stock Logic plugins only. No automation, no audio or MIDI generation, no other DAW. `SPEC.md` has the
full scope and the validation rules every action passes before it runs.

## Requirements

Mixhand is pinned to the versions it was measured on, and `mixhand doctor` fails on any other:

- macOS with **Logic Pro 12.3.1**
- **LogicProMCP 3.16.0**
- Python 3.11 or later, with [uv](https://docs.astral.sh/uv/)
- An OpenAI API key, for `produce`

`SETUP.md` walks through installing LogicProMCP, granting Accessibility and Automation permissions,
and why each version is pinned.

## Install and configure

```bash
uv sync
export MIXHAND_LOGICPROMCP=~/.local/bin/LogicProMCP
export MIXHAND_PROJECT=~/Music/Logic/my-copy.logicx
export OPENAI_API_KEY=...
uv run mixhand doctor
```

`MIXHAND_MODEL` picks the planner's model; unset or blank, it is `gpt-6-sol`.

`MIXHAND_PROJECT` is the only project Mixhand will touch. Every write and every undo first checks
that it is the front document, and refuses otherwise. Point it at a copy, never at a song you care
about. Mixhand never saves.

## Commands

| Command | Does |
|---|---|
| `mixhand doctor` | Checks that this Mac and Logic are ready, and names what is missing |
| `mixhand state` | Prints the session Mixhand sees, as JSON |
| `mixhand produce "<prompt>"` | Plans and runs the change, then asks for follow-ups |
| `mixhand undo` | Undoes the last run that changed something, follow-ups included |
| `mixhand explain` | Reprints the last run's plan, actions and reasons |

`state` and `produce` take `--key` (Logic does not expose the song's key) and `--start-bar` with
`--end-bar` to name the section to work on.

Mixhand writes to `logs/` in the directory you run it from: every action to `actions.jsonl`, which
`explain` reads, and the undo record of the last run that changed something to `group.json`, which
`undo` reads.

## Tests

```bash
uv run pytest
MIXHAND_LIVE=1 uv run pytest -m live
```

The first command runs against a fake LogicProMCP and needs no Logic. The live suite drives the real
Logic on `MIXHAND_PROJECT`, which needs a `Lead Vocal` track with at least one region and no
plugins. Keep your hands off Logic while it runs. `SETUP.md` says what each live test does.

## More

- `SPEC.md` — the goal, scope, architecture and rules
- `SETUP.md` — machine setup, and everything measured on Logic 12.3.1
