# Headless test mode

Runs one GamePRo script on a robot without the GUI, stops it safely, and records what happened.
This is for the test PC only. `main.py` doesn't import it and it isn't in the exe, so customers never see it.

```
python tools/testing/gamepro_test.py --script Beta/gen_2_vc/crystal_shiny_eevee --rig switch1 --cycles 3
```

The command prints the outcome and the path of `result.json`. The exit code is 0 for `completed` or `target-found` and 1 otherwise.

## What runs

The script is loaded and run by `core/runner.py`, the same code the app's Run button calls. The test mode only stands in for the person at the GUI:

| In the app | In a test run |
|---|---|
| Drawing a rectangle or clicking 4 corners on the video | The answer saved in the rig profile (`tests/rigs/<rig>.yaml`) |
| The settings window | Defaults, then the spec's `settings:`, then `--set key=value` |
| Clicking a D-pad arrow when a script asks | The spec's `directions:` list, in order |
| Pressing Stop | The runner sets the stop flag (see Safety) |

**Sandbox.** Scripts save calibration and other data next to themselves, for example `Beta/calibration/<name>.json`. To keep that out of your real scripts folder, each run copies the scripts into `test_runs/_sandbox/<rig>/` and runs the copy. Calibration therefore carries over between runs on the same rig, as it would for a customer. Use `--fresh-calibration` to make scripts do their first-run setup again.

## Safety

These rules apply to every run, whatever the script does:

- **Hard timeout:** taken from the spec's `timeout_minutes` (default 30), or `--timeout-minutes`.
- **Cycle limit:** the run stops once the spec's `cycle_log` regex has matched `--cycles` times. The default is the spec's `cycles`, and there is a hard ceiling of 1000.
- **Camera lost:** the run aborts if no frame, or an identical frame, arrives for `camera_lost_seconds` (default 10). A live webcam never repeats a frame exactly.
- **Picture frozen:** the run aborts if the picture doesn't change for `max_static_seconds` (default 120). A change means a mean grey difference above `static_threshold`, which defaults to 1.5.
- **Stop:** after setting the stop flag, the runner waits up to `stop_grace_seconds` (default 20) for the script to return. It then always sends `release_all()` and closes the port, including after a crash, Ctrl+C or a script that ignores Stop.
- **Shiny found:** a log line matching `target_log` stops the run, with frames saved at the moment of detection and 2 s later. The console is left on that screen.
  - The default pattern catches lines like "*** SHINY …", "Press Stop when done" and "shiny found".

Any limit can be set in the spec, for example `max_static_seconds: 300` for a script that sits on a static screen.

## Results

Each run gets its own folder, `test_runs/<date-time>_<script>/`, containing:

- **`result.json`**, with these fields:
  - `status` and `reason`;
  - `cycles_completed`, along with when each cycle was logged and how long each took;
  - `errors`, including the traceback if the script crashed;
  - `warnings`, such as checkpoints that couldn't be used;
  - `frames`, each with its name, time, cycle and the log line that triggered it;
  - the settings and calibration answers used;
  - the git revisions and the sha256 of the tested script.
- **`log.txt`**: the script's own log, with timestamps.
- **`frames/`**: one PNG for each of these:
  - start;
  - each checkpoint;
  - the target;
  - errors;
  - the final frame.

`test_runs/` is git-ignored, and frames never leave the PC.

| status | meaning |
|---|---|
| `completed` | reached the cycle count |
| `target-found` | the script reported a find (shiny etc.) |
| `script-ended` | the script returned by itself before the cycle count |
| `error` | the script raised, or failed to load (traceback in `errors`) |
| `timeout` | hit the hard timeout |
| `camera-lost` / `camera-frozen` | the watchdog aborted the run |
| `needs-calibration` | the rig has no answer for a calibration prompt (see below) |
| `needs-input` | the script asked for a D-pad direction the spec doesn't give |
| `interrupted` | Ctrl+C |

## Rigs and calibration

`tests/rigs/<rig>.yaml` holds the port, the camera index and the calibration answers:

- `corners.default` is used by any script that asks for the screen corners.
- `rects.<script>.<n>` answers the n-th rectangle that script asks for. A spec can name a different key with `rig_key`.

When an answer is missing the run stops with `needs-calibration`. Add `--calibrate` and the run instead opens a window showing what the app's video panel would show (including the corner warp). You draw the answer once and it's saved to the rig file.

## Spec fields used

All fields are optional. Without a spec, the run uses the default limits and doesn't count cycles.

```yaml
cycles: 5
timeout_minutes: 30
cycle_log: "Soft reset #(\\d+)"        # one match = one cycle
target_log: "\\*\\*\\* SHINY"          # optional, overrides the default
max_static_seconds: 120
settings: {mode: "Hatch only"}
directions: [left]
calibration:
  - {mode: rect, prompt: "Draw region over Eevee's sprite", rig_key: TODO}
checkpoints:
  - name: encounter
    when: {log: "Wild .* appeared", delay_seconds: 1.0}   # frame 1 s after the log line
  - name: overview
    when: {every_minutes: 5}
```

`when` also accepts the strings `"log: <regex>"` and `"every 5 minutes"`. A checkpoint whose `when` is still `TODO` is skipped with a warning. All 69 drafts are like that until Phase 3.

## Without a robot

`--fake-hw [image or folder]` swaps in a recording controller and a still-image camera. This tests the tool, not the script. Self-tests: `python -m unittest discover -s tests -v`.
