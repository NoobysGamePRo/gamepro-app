# Testing pipeline: decisions log

Newest first. Each entry: date, decision, why.

## 2026-11-13 (Lapras live demo, dark screens)
- **First live headless demo: FRLG Shiny Lapras, Switch 2 rig.** 5 attempts ran cleanly (reset → gift → summary). But the shiny check would have missed: the Script Builder had stored the star area in raw camera pixels, so it only lined up with the camera position at recording time. A star painted into a real frame scored 0 px. Fixed in gamepro-scripts PR #6 (the area is now a fraction of the calibrated screen; the painted star scores 134–154 px against a threshold of 10). A second 5-attempt run had no false shinies. **Lesson:** a pass needs a detection proof, not only clean cycles. A painted-reference check on a real frame is cheap and caught this.
- **Promoted on Carl's approval:** FRLG Shiny Starter (PR #5, already used by a customer to get a shiny Squirtle) and FRLG Shiny Lapras (PR #7). The five FRLG Script Builder scripts got real descriptions in place of "Auto-generated script for …".
- **Dark screens (Carl's call: both options).** Auto-detection can't find a mostly black screen, such as the Silph Co. room, and correctly rejects it instead of guessing. The video panel now:
  - remembers the last confirmed corners for each camera (`calibration/screen_corners.json` next to the exe) and offers them with "✓ Use last corners" when the screen is too dark;
  - keeps trying detection in the background, so a picture that brightens still gets found;
  - shows a tip to open a bright screen (e.g. the party menu) before pressing Run when there are no saved corners.

  Most soft-reset scripts restart the game first, so what's on screen at Run doesn't matter to them. The tip lives in the app rather than in readmes because those scripts don't have readmes.
- **Test specs:** the Lapras spec gained `target_log: "DETECTED!"`, because the default pattern didn't match the Script Builder's wording. The rig file gained a backup `corners.default`.

## 2026-11-13 (automatic screen corners)
- **Carl's call: one-click confirm, with manual setup as the backup.** When a script asks for the screen corners, the app now looks for the screen for about 2 s. If it finds it, it draws a green outline and a "✓ Use detected corners" button. Clicking 4 corners yourself still works and overrides it. If nothing is found, the prompt is exactly the old 4-click one. Only the 8 corner-calibration scripts are affected; the 52 that ask for a region are unchanged.
- **Detection (`core/screen_detect.py`).** It thresholds the lit area against the bezel brightness and fits a straight line to each side with RANSAC, taking the corners from where the lines cross. Dark scenery touching an edge therefore only removes part of a side. It rejects anything that isn't solid, inside the picture, square-on (opposite sides within 5 %) and a known screen shape (GBC, DS/3DS lower, GBA, 3DS upper, Switch). A result needs at least 3 of 8 frames over 2 s to agree within 4 px.
- **Checked on about 870 saved frames from the Switch rig.** 683 were accepted; all but two were within 12 px of the reference corners. The two were a mosaic image and a frame from a day when the screen sat differently, as seen in manual checks of the fitted edges. 187 were rejected and would fall back to manual: HOME menu off the picture, boot screens, broken edges.
- **The screen moves a few pixels between sessions,** probably nudged by the servos. So test mode detects the corners on every run, and the rig file's `corners.default` is only the backup.
- **3DS:** the camera only sees the top screen (the bottom screen has the light sensor), so there is one lit area to find. This hasn't been tried on a 3DS rig yet.

## 2026-11-11 (Phase 2: headless test mode)
- **The test mode is a separate entry point, `tools/testing/gamepro_test.py`, not `main.py test`.** `main.py` and the exe stay byte-for-byte as they were, and PyInstaller never picks up the test code. This follows Carl's rule that test tools are never customer-facing.
- **No duplicated logic.** Loading a script (`load_script_class`), running it (`run_script`), the scripts folder and the corner-warp maths moved from `gui/app.py` and `gui/video_panel.py` into `core/runner.py`. The GUI and the test mode both call it. The GUI's behaviour is unchanged.
- **Only the human parts are substituted:**
  - calibration comes from the rig profile (`tests/rigs/<rig>.yaml`);
  - SETTINGS come from the spec, then `--set`;
  - `wait_for_direction` comes from the spec's `directions`.

  Calibration answers are keyed `corners.default` and `rects.<script>.<n>`, and are captured once per rig with `--calibrate`. They are stored in panel coordinates, exactly what the GUI would return, including the warped view.
- **Runs use a per-rig sandbox copy of the scripts.** Scripts write calibration and other data next to themselves. A first fake run wrote `Beta/calibration/crystal_shiny_eevee.json` into the real scripts folder (since deleted), so runs now execute from `test_runs/_sandbox/<rig>/`. Calibration still persists per rig, as it does for a customer.
- **Safety, enforced by the runner:**
  - a hard timeout (spec, default 30 min);
  - a cycle limit (counted by the `cycle_log` regex, ceiling 1000);
  - the camera watchdog: an identical or missing frame for 10 s is `camera-lost`, and no visible change for `max_static_seconds` is `camera-frozen`;
  - stop: the stop flag, up to 20 s for the script to return, then **always** `release_all()` and close the port.

  The self-tests (`tests/test_headless.py`, 11 cases, fake hardware) cover each rule, including a script that ignores Stop. Writing that test found a real bug, now fixed: the runner waited forever on such a script.
- **A "shiny found" log line (`target_log`) ends the run as `target-found`.** Frames are saved at the line and 2 s later, and the console is left on the shiny.
- **Checkpoint `when`** is `{log: regex, delay_seconds}` or `{every_minutes}`. Draft checkpoints still marked TODO are skipped with a warning. Filling them in is Phase 3.
- **`test_runs/` is git-ignored.** Frames and logs stay on the test PC.

## 2026-11-11 (Carl's answers to the Phase 1 questions)
- **Crystal Game Corner:** keep the shiny prize hunter. The coin farmer is not wanted.
- **ORAS Horde:** retire it. XY Horde (Release) covers ORAS, but first needs to handle ability pop-ups during the encounter.
- **Re-port order:** as proposed: wild detection (9 scripts), then single-step fixes, then breeding.
- **Unported C++ programs:** port all 14 (Gen VI Horde to be checked against XY Horde first).
- **Go-ahead for Phase 2.** gamepro-app PR #4 and gamepro-scripts PRs #3 and #4 are still open for review.

## 2026-11-11 (Phase 1: static triage)
- **Ports are checked against their C++ originals, not just for errors.** Every Beta script loads and passes the code checks, yet about a third don't do what the original program did. `port_compare.py` scores each port against its C++ original by the commands they send, and the low scorers were read by hand.
- **Judgement lives in `triage-notes.csv`, which takes precedence over the automatic status.** `triage.py` stays repeatable: re-running it regenerates `triage-report.csv` and never overwrites a spec.
- **Statuses:** likely-ok / fix-in-pr / needs-fix / unclear / in-development (the last for Carl's own work in progress, e.g. FRLG Breeding).
- **Draft specs get their pass rule from the script's kind** (soft-reset hunt, wild, egg, farm, trade), following the Phase 0 answer on shiny hunts. Cycle counts: 5 for soft-reset hunts, 10 for wild, 3 for eggs and trades, 5 for farms.
- **Specs are YAML** (PyYAML, dev only). The generator writes JSON-style quoted strings, which are valid YAML.
- **Phase 1 fixes only small, certain bugs** (gamepro-scripts PRs #3 and #4). Re-ports that change detection or flow wait for Carl's priority order and hardware tests.
- **Imports are checked against the real exe contents** (PyInstaller `PYZ-00.toc` / `Analysis-00.toc` plus built-in modules). A script importing something the exe doesn't freeze would work from source but fail for customers.

## 2026-11-11 (Carl's answers to the Phase 0 questions)
- **Judge = Option B: no Claude API key.** The runner saves checkpoint frames and results overnight, and Claude Code judges them in a morning session on Carl's subscription. This avoids separate API billing while the specs are still being tuned. Option A (API key, fully unattended judging) can be added later without changing the runner.
- **Promotion clean-up: `retired.json` + app v1.10** (gamepro-app PR #3). The list is generated from the scripts repo's git history: every path no longer published, with the sha256 of every published version. Get Scripts deletes a local file only if it matches one of those hashes. Content hashes were chosen over a plain old→new list because a customer's own edits must never be deleted. `moved_data` carries user data folders across.
- **Not changed: `press_plus` already existed as hardware.** + is the HOME servo in the other direction, sent as `'W'` (`wonder_trade()`). The controller gained `press_plus()` / `press_home()` aliases.
- **No Start button.** Start and Select are always pressed together (the soft-reset servo). `crystal_game_corner`'s `press_start()` is a Phase 1 fix.
- **Rigs:** 2 or more possible, to be tested. Start states should be reached unattended where possible, with Carl stepping in only when needed.
- **Pass for shiny hunts:** enough clean cycles, plus a screenshot (or similar) proving the detection works.
- **First promotions:** FRLG Pickup Farm and FRLG Shiny Deoxys (gamepro-scripts PR #1). Mewtwo and Snorlax were already in Release.

## 2026-11-11 (Phase 0)
- **Test tooling lives in gamepro-app, not gamepro-scripts.** This covers `docs/testing/`, `tools/testing/` and `tests/specs/`. Get Scripts copies every `.md`/`.png`/`.py` from gamepro-scripts to customers, so test docs and specs there would ship to them.
- **The inventory is produced by a script** (`tools/testing/inventory.py`), not by hand, so it can be re-run as Beta changes.
- **The "large timing constant" heuristic is dropped from the inventory.** It mostly flagged millisecond thresholds and counters with clear names (`NORMAL_TIME_MS = 1600`). Timing-unit checks move to Phase 1, where the AST and comments are read properly.
- **Proposed, pending approval:** headless mode runs from source (`python main.py test …`), with the GUI and CLI sharing one `core/runner.py`; calibration is answered from rig profiles; promotion needs a `moved.json` that Get Scripts acts on. See discovery.md §3–4.
