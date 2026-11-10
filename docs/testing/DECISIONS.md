# Testing pipeline: decisions log

Newest first. Each entry: date, decision, why.

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
