# Test spec format

Each script has one spec, at `tests/specs/<game folder>/<script name>.yaml`. The spec says:
- how to set up a test run;
- when the runner saves frames;
- what the judge should see in those frames;
- what counts as a pass.

`tools/testing/gamepro_test.py` reads the spec to run the test. The judge (Phase 4) and the promotion step (Phase 5) read it too.

Check the specs after you edit one:

```
python tools/testing/spec_check.py            # every spec
python tools/testing/spec_check.py tests/specs/gen_3_frlg/FRLG_Shiny_Lapras.yaml
```

The checker reads the script itself and cross-checks it against the spec. It catches problems that would otherwise only show up on the robot, such as:
- a `cycle_log` that never matches;
- a `cycle_log` that matches two lines, so cycles are counted twice;
- a target pattern that would miss the script's "found it" line;
- checkpoint patterns that match no log line.

The self-tests also run it on every spec in the repo.

## Example

```yaml
script: "Release/gen_3_frlg/FRLG_Shiny_Lapras.py"
name: "FRLG Shiny Lapras"
game: "FireRed / LeafGreen (NSO GBA)"
console: "Switch"
draft: false
kind: "soft-reset hunt"
start_state: "FRLG (NSO): one Pokemon in the party, saved in Silph Co. directly above the employee ..."
cycles: 5
timeout_minutes: 30
cycle_log: "Attempt\\s*(\\d+)"
target_log: "DETECTED!"
max_static_seconds: 120
calibration:
  - {mode: "corners", prompt: "Click the 4 corners of the Switch / Switch 2 screen"}
checkpoints:
  - name: "checked screen"
    when: {log: "Watching\\.\\.\\. \\S+ px match", delay_seconds: 1}
    per_cycle: 1
    expect: "Lapras's summary screen; the star region left of the name is empty for a normal Lapras"
  - name: "overview"
    when: {every_minutes: 10}
    expect: "the game running the script's loop, not stuck on a menu, error or HOME screen"
detection_proof:
  reference_image: "Shiny_Images/FRLG Lapras.png"
  result: "star region scores 46 px on the real shiny vs threshold 10; a normal Lapras scores 0"
success: "Every cycle restarts the game, receives Lapras, opens its summary and logs no DETECTED!"
notes: "Validated 2026-11-13: 2 x 5 headless cycles, no false shinies."
```

Strings are in double quotes, so a regex backslash is written twice (`\\d`). Single quotes (`'\d'`) also work.

## Fields

### What it is

| field | required | meaning |
|---|---|---|
| `script` | yes | path in gamepro-scripts, e.g. `Beta/gen_8_bdsp/bdsp_shiny_arceus.py`. Update it when the script is promoted. |
| `name`, `game`, `console` | yes | for people and for grouping runs by cartridge (Phase 4) |
| `draft` | yes | `true` until Carl has confirmed the spec. Phase 5 refuses to promote on a draft spec. |
| `kind` | yes | `soft-reset hunt`, `wild encounters`, `breeding / hatching`, `item / BP farm` or `online trade`. The kind sets the default pass rule and the checkpoints that get drafted. |
| `start_state` | yes | where the game must be before Run, in plain English, including anything easy to get wrong (party size, facing direction, pending system update) |
| `notes` | no | known quirks and history |

### How the run goes

| field | default | meaning |
|---|---|---|
| `cycles` | (required) | stop after this many cycles. `--cycles` overrides it; the ceiling is 1000. |
| `timeout_minutes` | (required) | hard stop, whatever happens |
| `cycle_log` | none | regex for the **one** log line that marks a finished cycle. Each match counts as one cycle. Without it, the run can only end on the timeout. |
| `target_log` | catches `*** SHINY`, `Press Stop when done`, `shiny found` | regex for the line that announces a find. It stops the run and saves frames at once and again 2 s later. |
| `max_static_seconds` | 120 | abort if the picture doesn't change for this long |
| `static_threshold`, `camera_lost_seconds`, `stop_grace_seconds`, `max_frames_per_checkpoint` | 1.5, 10, 20, 20 | safety tuning (tools/testing/README.md) |
| `settings` | the script's defaults | answers for the settings window, e.g. `{mode: "Hatch only"}` |
| `directions` | none | answers, in order, when the script asks for a D-pad direction |
| `calibration` | none | the script's calibration prompts, for reference. Corners are detected automatically, with the rig's `corners.default` as the backup. Rectangles are answered from the rig file (`rects.<script>.<n>`). Add `rig_key` only to share one answer between scripts. |
| `interactive` | none | anything a headless run must answer or watch for (notes only) |

### What to capture and what it should show

`checkpoints` is a list. Each entry has these fields:

| field | meaning |
|---|---|
| `name` | unique in the spec. It becomes part of the frame file name. |
| `when` | `{log: <regex>}` saves a frame when a matching line is logged, after `delay_seconds` if given. `{every_minutes: N}` saves one every N minutes. |
| `per_cycle` | optional: at most this many frames per cycle. Use 1 for a line the script logs many times per cycle (e.g. "Watching..."), so every cycle gets a frame instead of only the first few. |
| `expect` | what the judge should see in the frame, in plain English. Be specific: name the Pokémon, the screen and the area. |
| `reference_image` | optional: a frame showing what a good result looks like |

A frame grabbed on a log line shows the screen at the moment of that line. The log call runs in the script's thread, so the script can't press anything until the frame is saved.

### Proof that detection works (hunts)

A shiny hunt can't wait for a real shiny, so a confirmed hunt needs `detection_proof`:

| field | meaning |
|---|---|
| `reference_image` | a real shiny screenshot (gamepro-scripts `Shiny_Images/`), with the script's check area run against it |
| `result` | what the check scored on the reference and on a normal Pokémon, against its threshold, and where that was recorded (PR, date) |

Use a **real** shiny screenshot, not a painted-in one. The Lapras star area was first placed from a painted star. It sat too high and caught only 3 px of the real star (2026-11-16, gamepro-scripts PR #8).

### What a pass is

| field | meaning |
|---|---|
| `success` | the pass rule in plain English. Drafts get the default for their `kind` (DECISIONS.md, Phase 1). |

## Drafts

`triage.py --specs` writes a draft spec for each new Beta script. The checkpoints and `cycle_log` are drafted from the script's own log lines:

| checkpoint | drafted from |
|---|---|
| **checked screen** (hunts) | the line logged with each check's colour values, else the "not shiny" line, else the FRLG "Watching..." line, else a timing result ("text appeared in N ms") |
| **cycle end** (farms, trades) | the cycle line |
| **overview** (all) | a frame every 10 minutes, to show whether the game is stuck |

When `cycle_log` matches several lines (start, retry, result), the drafter keeps only the line that ends a normal cycle ("not shiny", "complete").

`python tools/testing/spec_check.py --draft-checkpoints` redrafts the checkpoints of all **draft** specs in place. It never touches a confirmed spec (`draft: false`).

### To confirm a spec

1. Check `start_state` against the game.
2. Do one run and look at the checkpoint frames. Do they show what `expect` says?
3. For a hunt, add `detection_proof`.
4. Set `draft: false` and run `spec_check.py`. A confirmed spec must have:
   - no `TODO`;
   - checkpoints with `expect` text;
   - a proof, if it's a hunt.
