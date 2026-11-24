# GamePRo: where things stand

**Read this first when picking work back up.** It is the handoff. If a chat is lost, this file plus
[`testing/DECISIONS.md`](testing/DECISIONS.md) is everything needed to carry on.

**Rule:** update this file at the end of every piece of work (PR merged, release, customer
contacted, test run), in the same PR where possible. Newest-first log at the bottom.

_Last updated: 2026-11-24._

## Repos

| Repo | What | Notes |
|---|---|---|
| `C:\Dev\gamepro-app` | The app (exe), test tooling, this file | Test tooling and docs live here, never in gamepro-scripts |
| `C:\Dev\gamepro-scripts` | Scripts customers get via Get Scripts | Every `.md`/`.png`/`.py` ships to customers, so no internal docs there |
| `C:\Dev\gamepro-firmware-switch` / `-3ds` | Arduino firmware, Releases with a `.hex` | Flash Firmware pulls the latest Release |
| `C:\Dev\gp_agent` | Claude's helpers for driving the rig directly | FRLG Breeding is generated from here (`build_package.py`) |

C++ originals: `Dropbox\GamePRo Software\Windows\GamePRo Programs\Source Code\`.

## Released versions

- **App:** v1.13 (2026-11-18). Nothing merged and waiting for a release.
- **Switch firmware:** v1.6. **3DS firmware:** v4.6.
- **Scripts `main`:** `ed729ab` (PR #11). Open: scripts PR #12 (Crystal → Release).
- **In Release:** FRLG Pickup Farm, Deoxys, Mewtwo, Snorlax, Shiny Starter, Lapras; XY Horde; VC Crystal Shiny Starter once #12 merges.

## Waiting on someone else

| Since | What | Waiting for |
|---|---|---|
| 2026-11-24 | Scripts PR #12: promote VC Crystal Shiny Starter to Release | Carl to review and merge, together with app PR #11 (this file + the spec's new path) |
| 2026-11-07 | FRLG Breeding (Beta) | Carl's own testing from the user install |

## Not yet proven on hardware

- Scripts PR #10: Deoxys-style random restart in FRLG Lapras, Starter, Mewtwo and Snorlax (fake hardware only).
- VC Gold/Silver Shiny Starter (scripts PR #11; fake hardware only). Crystal is proven (customer, shiny Cyndaquil).
- If a customer's reset rod moves the wrong way (up, like `W`): it's their **SR Max** calibration on the wrong side of SR Centre (the servo sits the other way round on XL models), not the script. Every firmware sends SR Max + A + B for `Z`. Check with the SR2 button in Manual Controls.
- Automatic screen corners on a 3DS rig (only the Switch rig so far).

## Testing pipeline (Beta → Release)

Detail: [`testing/triage-summary.md`](testing/triage-summary.md), decisions in
[`testing/DECISIONS.md`](testing/DECISIONS.md), spec format in [`testing/spec-format.md`](testing/spec-format.md).

| Phase | Status |
|---|---|
| 0 Discovery | Done 2026-11-11 |
| 1 Static triage | Done 2026-11-11: 22 likely-ok, 8 fixed, 23 needs-fix, 15 unclear, 1 in development |
| 2 Headless test mode | Done 2026-11-11 (`tools/testing/gamepro_test.py`) |
| 3 Spec format + checker | Done 2026-11-17 (`tools/testing/spec_check.py`) |
| 4 Hardware runs + judging | **Next.** Only FRLG Shiny Lapras done so far |
| 5 Promotion command | Not started |

## Next up (in Carl's order)

1. Phase 4 hardware runs, starting with the 22 likely-ok scripts, grouped by console (order in triage-summary.md).
2. Re-ports:
   1. the 9 wild-encounter scripts that report a new species as shiny, using one shared detection approach;
   2. the single-step fixes: Crabrawler, Friend Safari, Crystal Sudowoodo, Celebi and DP Starter;
   3. the breeding rewrites.

   Crystal / GS starters are done (PR #11).
3. XY Horde: handle ability pop-ups, then retire ORAS Horde.
4. Port the 14 C++ programs that were never ported (list in triage-summary.md).

## Standing rules

- Branches + PRs only. Never push to `main`; Carl reviews every script behaviour change and every promotion.
- Batch app (exe) releases. Script and readme changes can go out on their own.
- FRLG soft-reset scripts randomise the restart timing (FRLG Shiny Deoxys is the model).
- Prove shiny detection on a **real** shiny screenshot (`gamepro-scripts/Shiny_Images/`), never a painted-in star.
- Present options and wait for Carl's explicit yes before acting on them.

## Loose ends

- gamepro-scripts has untracked files: `Shiny_Images/FRLG Lugia.png` (commit it?) and Pickup Farm `stuck_*.png` test screenshots (never commit those).

## Log (newest first)

- **2026-11-24:** A customer got VC Crystal Shiny Starter working on a new 3DS and found a **shiny Cyndaquil**. Promotion to Release is in scripts PR #12; its test spec path is updated but still `draft: true` (no rig run or detection-proof screenshot yet).
- **2026-11-19:** The Crystal customer's reset rod moved up. Traced to calibration (SR Max), not the script or firmware.

- **2026-11-18:**
  - App v1.13 released.
  - Scripts PR #10 (FRLG random restart) and PR #11 (Crystal / GS starter re-port) merged; the customer has been advised to test Crystal.
  - This STATUS file was added after a lost chat showed the history wasn't recoverable from one place.
- **2026-11-17:** Phase 3 done (app PR #9).
- **2026-11-16:** Lapras star area moved onto the real star (scripts PR #8).
- **2026-11-13:** Lapras live demo; Lapras and FRLG Starter promoted; automatic screen corners; app v1.11 and v1.12; Switch firmware v1.6.
- **2026-11-11:** Phases 0–2; Pickup Farm and Deoxys promoted; app v1.10.
