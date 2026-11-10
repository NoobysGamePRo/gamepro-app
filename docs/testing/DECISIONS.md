# Testing pipeline: decisions log

Newest first. Each entry: date, decision, why.

## 2026-11-11 (Carl's answers to the Phase 0 questions)
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
