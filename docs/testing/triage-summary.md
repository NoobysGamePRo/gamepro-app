# Phase 1: Static triage summary

**Date:** 2026-11-11.
**Scope:** all 69 published Beta scripts. Nothing was run on hardware.
**Per-script detail:** [`triage-report.csv`](triage-report.csv), which merges three sources:
- the automatic checks;
- the manual review in [`triage-notes.csv`](triage-notes.csv);
- the comparison with the C++ originals in [`port-compare.csv`](port-compare.csv).

**Draft test specs:** one per script, in `tests/specs/<folder>/<script>.yaml`. All have `draft: true`.

## How it was checked

1. **`tools/testing/triage.py` (automatic).** For each script it:
   - loads the script exactly as the app does;
   - runs pyflakes to find undefined names;
   - checks every `controller.*` call against `core/controller.py`;
   - checks every import against the modules actually frozen into GamePRo.exe;
   - flags loops Stop can't end, long `time.sleep`s and millisecond values used as seconds;
   - flags SETTINGS the dialog can't show;
   - records calibration prompts and interactive waits;
   - drafts a spec for each script.
2. **`tools/testing/port_compare.py`.** It flattens each port and its C++ original (Dropbox `Windows/Source Code`) into the sequence of Arduino commands each one sends, then scores how similar the two are.
3. **Manual review.** I read every script and compared the low-scoring ones line by line with their C++ originals. The results are in `triage-notes.csv`.

## Result

| Status | Scripts | Meaning |
|---|---|---|
| likely-ok | 22 | Follows the original, or is new and sound. Ready for hardware testing. |
| fix-in-pr | 8 | A real bug, now fixed in an open gamepro-scripts PR (#3, #4). |
| needs-fix | 23 | Won't work as written. Needs a re-port from the C++ original, or a design fix. |
| unclear | 15 | Nothing definitely broken, but it differs from the original. Hardware testing decides. |
| in-development | 1 | FRLG Breeding: your current project, kept out of the backlog. |

The automatic checks alone passed 60 of the 69 scripts. Every one of the 23 needs-fix scripts loads and runs without an error; the problems only show up when the port is compared with what the original program did.

## What's wrong, by pattern

1. **Different species read as shiny (9 scripts).** These are the wild-encounter scripts:
   - Gen 2 Random Encounter (as part of the timing bug in item 4);
   - HGSS Random and Headbutt;
   - Platinum Random;
   - BW Random;
   - BDSP Wild;
   - SwSh Chain Fishing;
   - SV Route 1;
   - Crystal Gift Egg (7 possible hatchlings).

   They save the first Pokémon's colours as "normal", so the next different species is reported as shiny. The C++ originals timed the shiny sparkle, or looked for the shiny star, and neither depends on the species. **Fix:** re-port the original detection.
2. **Switch "soft reset" never restarted the game (6 scripts).** On Switch, `'S'` is just HOME and `'Z'` is HOME+A+B.
   - Affected: the four BDSP legendary scripts, BDSP Starter and SwSh Stationary.
   - **Fixed in PR #4** with HOME → X → A → relaunch, the same way Regi, Let's Go and FRLG already restart.
3. **Light-sensor timing switched to sprite colour (about 20 scripts).** For Gen 2–5 2D sprites this is fine. For animated 3D models (USUM, ORAS, BDSP, SwSh) the average colour moves as the Pokémon animates, so false shinies are likely. These are marked unclear; the hardware test decides.
4. **The port does something different from the original.**
   - **Celebi:** skips the 8 GS Ball dialogue presses.
   - **Crystal Sudowoodo:** opens POKéMON instead of PACK.
   - **DP Starter:** about 6 A presses where the event needs about 27.
   - **ORAS Starter:** invents a summary-screen path.
   - **Crabrawler:** no soft reset, so it keeps pressing A on an empty berry pile.
   - **Friend Safari:** can't flee.
   - **Megamart:** never sells back, so it runs out of money.
   - **ORAS/XY Breeding:** simplified to one egg per reset.
   - **Crystal and Gold/Silver starters:** check 3 regions where only one starter is received.
   - **Gen 2 Random Encounter:** its timing is always ~0 ms, and it can't flee.
5. **Crashes and smaller bugs (fixed in PR #3):**
   - Crystal Game Corner called `press_start()`, which doesn't exist.
   - SV Egg Breeding read the camera frame as RGB instead of BGR, so it never saw an egg.
   - A duplicate *XY - Horde Encounter* sat in Beta alongside Release.
   - FRLG Starter said "3DS".
6. **Settings that can only be changed by editing code.** Starter choice, prize slot, portal direction and trade count are class constants. Moving them into `SETTINGS` (the dialog) would let customers choose them. That's a separate improvement, not part of this triage.

## Needs your decision

- **Crystal Game Corner.** The C++ original is a *slot-machine coin farmer*. The Beta script is a different program: a prize-Pokémon shiny hunter. Do you want either, or both?
- **ORAS Horde vs XY Horde.** *XY - Horde Encounter* (Release) says it also works in ORAS. Should the separate *ORAS - Horde Encounter* be retired instead of fixed?
- **Re-port order for the 23 needs-fix scripts.** I suggest:
  1. the wild-encounter detection (one shared approach, 9 scripts);
  2. the single-step fixes (Crabrawler reset, Friend Safari flee, Sudowoodo, Celebi, DP Starter);
  3. the breeding rewrites last.
- **C++ programs that were never ported (16).** These are:
  - Ditto Box Mash A;
  - Generic Shiny Static Encounter;
  - ORAS Starter (Record Encounter);
  - ORAS Story-mode Lati@s;
  - SOS Chaining (Ditto track);
  - Shiny Dojo Hitmonchan;
  - USUM Blacephalon/Stakataka, Partner Cap Pikachu, Poipole, Surf Pikachu, Type: Null (USUM version), UB First Time Encounter and Wonder Trade;
  - Gen VI Horde Encounters;
  - Colour Detection and Light Sensor Test (both just removed, so ignore).

  Should any of them be ported?

## What Phase 2 (headless test mode) must handle

From the inventory of interactive points in `triage-report.csv`:
- **Calibration.**
  - 52 scripts save their calibration to `<exe folder>/calibration/<name>.json`, or `Beta/calibration/` when run from source. This path doesn't depend on Beta or Release, so promoting a script keeps it.
  - The first run needs a person to draw the region. A headless run should pre-seed the JSON or answer `request_calibration` from a rig profile.
- **Waits for a person.** Chain Fishing waits for a D-pad press (`wait_for_direction`), and Chain Fishing and XY Horde have a "press Stop within 10 s" baseline window. The runner must answer or skip these.
- **"Shiny found" leaves the script parked.** On a shiny, 61 scripts park in `stop_event.wait()`. The runner treats the log line as the end of the run, saves the frame and presses Stop itself.
- **Switch firmware.** The test rigs need **Switch firmware v1.5**. Older versions don't ACK most commands, so `_send()` would wait out its timeout on every press.

## Suggested hardware test order (Phase 4)

Start with the 22 likely-ok scripts, grouped so cartridges change as rarely as possible:
1. **Switch:**
   - FRLG Lapras and Starter;
   - Let's Go Legendary and Mewtwo;
   - SwSh Regi and Auto Breeding;
   - Legends Z-A Bench.
2. **3DS VC:** Crystal Eevee and Electrode.
3. **3DS DS-mode:**
   - HGSS Eevee, Legendary and Starter;
   - DPPt Dialga/Palkia, Legendary and Darkrai;
   - Platinum Starter;
   - BW and B2W2 Starters.
4. **3DS:**
   - Chain Fishing (6th gen);
   - Static Type: Null, USUM Starter and SM Starter.

Then the 8 fix-in-pr scripts once their PRs are merged, then the unclear ones.

## Re-running

```
pip install pyflakes pyyaml          # dev only
python tools/testing/port_compare.py # needs the Dropbox C++ folder
python tools/testing/triage.py --specs
```

- `triage.py` overwrites `triage-report.csv`. It never overwrites a spec that already exists; delete one to regenerate it.
- Manual judgement goes in `triage-notes.csv`, which takes precedence over the automatic status.
