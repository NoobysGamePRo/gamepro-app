# Phase 0: Discovery

Covers the Beta → Release testing pipeline from `gamepro-beta-to-release-brief.md`. Read-only so far: no app or script code has changed.

Files produced:
- `docs/testing/inventory.csv`: one row per Beta script.
- `tools/testing/inventory.py`: regenerates the inventory.

## 1. How the app works today

### Repos
- **gamepro-app:** the tkinter app, built into `GamePRo.exe` with PyInstaller.
- **gamepro-scripts:** the scripts. The app never bundles them.

### Where scripts live
- **Customer install:** `scripts/` next to `GamePRo.exe`.
- **Dev run (`python main.py`):** the `gamepro-scripts` checkout next to `gamepro-app`.

### How a script is loaded
`gui/app.py` `_build_tree` walks the scripts folder:
- Each folder becomes a tree node.
- Folders starting with `_` or `.` are hidden, and so are folders with no scripts in them.
- Every `.py` file is executed with `importlib` (`_load_script`). The first `BaseScript` subclass found is listed under its `NAME`.
- A script that fails to import is logged ("Failed to load …") and left out of the list.

### The script API (`scripts/base_script.py`)
A script is a class with `NAME`, plus optional `DESCRIPTION` and `SETTINGS`, and a `run()` method:

```
run(controller, frame_grabber, stop_event, log, request_calibration)
```

- **Buttons:** method calls on `GameProController` (`core/controller.py`), for example `press_a()` or `hold_left()`.
  - Each call sends one byte over serial at 9600 baud and blocks until the Arduino ACKs with `'K'` when the servo finishes.
  - There are no button names or durations in a data format. Scripts are plain Python, and "timing" is `self.wait(seconds, stop_event)` or `time.sleep()`.
- **Camera:** `frame_grabber.get_latest_frame()` returns the latest 640×480 BGR frame (DirectShow webcam, autofocus).
- **Light sensor:** `controller.read_light_value()` returns 0–1020.
- **Calibration:** `request_calibration(prompt, mode)` blocks the script until a person either:
  - drags a rectangle on the video (`mode='rect'`, the default; 64 Beta scripts use it), or
  - clicks the 4 corners of the game picture (`mode='corners'`, a perspective warp; used by the newer FRLG scripts).
  - 52 Beta scripts save their rectangle to `Beta/calibration/<script>.json` and skip calibrating on later runs.

### How a script runs
`_run_script` does the following, in order:
1. Shows the settings dialog if the script has `SETTINGS`.
2. Starts `script.run()` on a daemon thread.
3. Catches any exception and logs it as "Script error".
4. `_on_script_done` re-enables the UI.

Stop only sets `stop_event`. A script that is inside a long `time.sleep()` doesn't notice until the sleep ends.

### Beta and Release status
Status is **just the top-level folder** in gamepro-scripts: `Beta/<game folder>/…` or `Release/<game folder>/…`. There's no flag, manifest or database.

**Publishing** means pushing to `main`. The app's **Get Scripts** button downloads `main.zip` and copies every `.py/.md/.png/.jpg/.jpeg/.gif` file into the local `scripts/` folder.

## 2. What's in Beta

Release holds only 3 scripts: FRLG Shiny Mewtwo, FRLG Shiny Snorlax and ORAS horde encounters. Beta holds **71** scripts, about 16,300 lines.

| Folder | Scripts | Console |
|---|---|---|
| gen_2_vc (Gold/Silver/Crystal VC) | 9 | 3DS |
| gen_3_frlg | 5 | Switch (NSO GBA) |
| gen_4_hgss_dpp | 16 | 3DS (DS mode) |
| gen_5_bw_b2w2 | 3 | 3DS (DS mode) |
| gen_6_oras_xy | 10 | 3DS |
| gen_7_sun_moon | 12 | 3DS |
| gen_8_bdsp | 6 | Switch |
| gen_8_sword_shield | 4 | Switch |
| gen_9_scarlet_violet | 3 | Switch |
| legends_za | 1 | Switch / Switch 2 |
| lets_go | 2 | Switch |

That's **50 for 3DS** (19 of them DS games) and **21 for Switch**.

- **Origin:** 65 of the 71 were last changed in March 2026. That was the migration. 44 say "Ported from …cpp" in their header.
- **Old-system leftovers:** there's no C++ syntax and no hard-coded COM ports. The port is a faithful translation: the C++ delays became seconds-valued constants, and LDR timing mostly became `avg_rgb` checks on a drawn rectangle.
- **How they detect things:**
  - camera: 61;
  - camera + light sensor: 3;
  - light sensor only: 1;
  - **blind timing only: 6.** These are the Wonder Trade / Surprise Trade loops, Battle Royal BP, Megamart Premier Balls and the Legends Z-A bench. A test can only judge them from frames.

### Already found: real bugs, without running anything
- `crystal_game_corner` calls `controller.press_start()`, and `lets_go_shiny_legendary` / `lets_go_shiny_mewtwo` call `controller.press_plus()`. **Neither method exists**, so these scripts crash at that line.
- `sword_shield_auto_breeding` calls the private `controller._send()` directly. It works, but it gets around the API.
- 61 scripts use `time.sleep()`. Mostly these are short, but Stop is ignored while a sleep runs.
- `usum_wonder_trade` and `oras_wonder_trade` use `wonder_trade()` (`'W'`). On the Switch firmware `'W'` is the + button. That's fine for 3DS but worth confirming.

### Already found: the pipeline must deal with these
1. **Get Scripts never deletes.** It adds and updates, nothing else. If a script moves from `Beta/` to `Release/`, every customer keeps the old Beta copy and **sees the script twice**. Promotion therefore needs app support (see Phase 5).
2. **Calibration needs a human.** Scripts block in `request_calibration` until someone drags a box or clicks corners. A headless run must answer those calls automatically.
3. **Calibration files are tied to the folder.** `Beta/calibration/*.json` lives under `Beta`, so a promoted script looks in `Release/calibration` and asks the customer to calibrate again. Pickup Farm's learned item pictures (`pickup_items/`) move the same way.
4. **No servo release on finish or crash.** Neither the app nor most scripts call `release_all()` when a script finishes or crashes. If one crashes during a `hold_*`, the servo stays down. The safety rule needs this in the runner.
5. **Scripts don't report "cycles".** Each one logs progress in its own words ("Soft reset #12", "Battle 40…"). The runner has to count cycles from log lines; a regex per spec does this without changing any script.

## 3. Proposed design for Phases 1–5

### Phase 1: static triage (`tools/testing/triage.py`)
- Builds on the inventory parser.
- Adds AST checks:
  - calls to controller methods that don't exist;
  - `time.sleep` inside loops;
  - loops whose body has no stop check;
  - timing constants whose names or comments say milliseconds;
  - very short presses or gaps, which the old C++ often had and which drop presses on Switch;
  - `request_calibration` modes;
  - JSON calibration paths.
- Claude reads each script's header and constants, then drafts its start state and a spec (`draft: true`). It also gives a status of `likely-ok`, `needs-fix` or `unclear`, with reasons.
- Output: `triage-report.csv`.
- Fixes go out as one PR per game folder.

### Phase 2: headless test mode
- `python main.py test --script <path> --spec <spec.yaml> --cycles N --out <dir>`. This runs from source on the test PC; the customer exe is unchanged and the mode is off by default.
- **No duplicated logic:** the body of `_run_script.thread_target` moves into a small `core/runner.py` that the GUI and the CLI both call. The GUI's behaviour stays the same.
- **Headless calibration:** `request_calibration` is answered from a **rig profile**, e.g. `tests/rigs/switch1.json` holding the clicked corners for each camera, plus the rectangles the spec names for each script.
- **Safety, all enforced by the runner:**
  - a hard timeout;
  - a cycle limit, counted from the spec's log regex;
  - a frozen or missing camera watchdog: the run aborts if frames stop arriving or don't change for longer than the spec allows;
  - always `release_all()` in a `finally`;
  - a crash is recorded as `status: error` with the frame at the time.
- **Frames** are saved at checkpoints (log regex or every N minutes), at the end and on error.
- Results go to `result.json`. Everything stays local.

### Phase 3: spec format
As in the brief, plus three fields:
- `cycle_log`: a regex that marks one cycle;
- `calibration`: named rectangles or corners from the rig profile;
- `max_static_seconds`: for the frozen-feed watchdog.

Specs live in `gamepro-app/tests/specs/<game folder>/<script>.yaml`, not in gamepro-scripts, so customers never download them. (Get Scripts copies `.md` and image files.)

### Phase 4: runner + judge
- The runner groups scripts by console and game so cartridges are swapped as rarely as possible, and prints the setup needed before each batch.
- The judge checks in order and stops at the first decisive answer:
  1. OpenCV: is the frame black or frozen, and does it match the reference image?
  2. Claude API with the checkpoint frames and the `expect` text, giving `pass`, `fail` or `needs-human` and a one-line reason.
- Results are added to `results.csv`, and a morning summary is written in Markdown.

### Phase 5: promotion
- `python tools/testing/promote.py <script>` runs only after your approval. It:
  - moves the script, its `.md`, readme images and template folders with `git mv` on a branch and opens a PR;
  - records the evidence (run IDs, pass rate) in `docs/testing/promotions.md`;
  - drafts a release note.
- **Needs an app change first:** gamepro-scripts gets a `moved.json` (old path → new path). Get Scripts uses it to:
  - delete the old Beta copy, but only if it's identical to the version that was published (so a customer's own edits are never lost);
  - move calibration JSON and learned templates across.

  This means a new exe before the first promotion goes out.

## 4. Questions for you
1. **Duplicates after promotion:** OK to add `moved.json` handling to Get Scripts (new exe), as above? Until that's out, any promoted script appears twice for customers.
2. **Rigs:** how many robot + camera setups can run at once, and for which consoles? I know of one Switch rig (COM6, camera 1). Is there a 3DS rig for the 50 3DS/DS scripts?
3. **Start states:** are they reached from saves you keep per game, one cartridge or save per game? Several scripts need a specific spot, such as Spear Pillar or in front of a legendary. Can we keep a "test save" per game, or do you set each one up by hand?
4. **What a pass means for shiny hunts:** a shiny can take thousands of resets, so a test can't wait for one. I suggest a pass is N clean cycles (reset → encounter → correct "not shiny" decision) plus one check on a known shiny reference frame where one exists. Is that enough to promote?
5. **Claude API:** for the judge, is a Claude API key on the test PC OK (frames go only to the API, as the brief says)? Do you want a monthly budget cap?
6. **Fixing real bugs:** `press_start` / `press_plus` don't exist. Should the controller gain those, or does a separate servo map to them on your hardware? Which Arduino command is Start on 3DS?
7. **Repo naming:** the brief says scripts live in the `noobysgamepro` repo. I'm treating that as `NoobysGamePRo/gamepro-scripts` and keeping the test tooling in `gamepro-app`. Correct?
8. **FRLG Pickup Farm promotion:** you've approved it. Should it wait for (1), or go now and accept the duplicate until the new exe is out?
