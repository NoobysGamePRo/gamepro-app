# Testing pipeline: decisions log

Newest first. Each entry: date, decision, why.

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
