# Testing pipeline: decisions log

Newest first. Each entry: date, decision, why.

## 2026-11-11 (Phase 0)
- **Test tooling lives in gamepro-app, not gamepro-scripts.** This covers `docs/testing/`, `tools/testing/` and `tests/specs/`. Get Scripts copies every `.md`/`.png`/`.py` from gamepro-scripts to customers, so test docs and specs there would ship to them.
- **The inventory is produced by a script** (`tools/testing/inventory.py`), not by hand, so it can be re-run as Beta changes.
- **The "large timing constant" heuristic is dropped from the inventory.** It mostly flagged millisecond thresholds and counters with clear names (`NORMAL_TIME_MS = 1600`). Timing-unit checks move to Phase 1, where the AST and comments are read properly.
- **Proposed, pending approval:** headless mode runs from source (`python main.py test …`), with the GUI and CLI sharing one `core/runner.py`; calibration is answered from rig profiles; promotion needs a `moved.json` that Get Scripts acts on. See discovery.md §3–4.
