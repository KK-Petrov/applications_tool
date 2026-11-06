# Phase 5: Polish

## Goal
The whole tool is documented well enough to set up from the README alone, every page handles its empty and
error states, six accepted known issues of earlier phases are fixed, and the full end-to-end suite passes.

**Recommended:** 1–2 sessions · medium effort. If the phase won't finish within that, stop at a clean point and run `/handoff`.

## Read first
docs/04_ai_features.md §8; docs/03_ui_spec.md §2, §4.7, §5; docs/05_integrations.md §1, §3, §5;
docs/PROGRESS.md (accepted known issues of earlier phases); docs/DECISIONS.md #26, #33–#36, #41, #42, #45.

## Tasks
1. **Usage panel.** Settings shows call count and estimated cost this month (docs/04 §8).
2. **README.md.** Prerequisites, setup on Windows, setting `data_dir` to OneDrive, `init-data`, Google setup
   (docs/05 §1), iPhone setup (docs/05 §3), Task Scheduler, daily use, troubleshooting (Claude Code not found,
   token expired, calendar missing on iPhone, a file locked by OneDrive).
3. **Unreadable programme file.** A clearer error page when a programme file cannot be loaded: name the file and
   the problem, and say how to fix it (accepted Phase 1 item).
4. **Final pass.** Every empty/error state from docs/03 §5 present; no raw IDs or "None" visible (screenshot
   text scan); full suite green, US-1 to US-21 (C-5-02).
5. **Known issues taken on (DECISIONS #45).** Each gets a regression test written first:
   1. The DataError banner for a hand-broken file uses the same plain-word messages as forms: no Pydantic or
      YAML-parser text (accepted Phase 1 item).
   2. The locked / read-only save message names a leftover `<file>.tmp` when that is the cause (Phase 1).
   3. Toast accessibility: after Dismiss, focus returns to the control that triggered the action; the toast is
      not early in the tab order (Phase 1).
   4. Forms report all errors, form checks and model checks, in one round (Phase 1).
   5. Narrow screens: the recommender totals strip doesn't push the table below the fold at 761 px, and a status
      menu in a low row opens fully visible (Phase 2a).
   6. No-JS failed saves use post-redirect-get, so a reload never re-posts (Phases 1 and 2a).
   All other known issues stay accepted as listed in docs/PROGRESS.md.

## Tests first
In your plan, map every automated criterion below to the test or script that checks it and the command
that runs it. After approval, write those tests **before** the implementation and show that they fail
for the right reason. Never edit a test to make it pass without saying so and getting approval.

## Acceptance criteria
Fixed at the design freeze (`config/criteria.json`) and guarded; never lowered without the user's approval. Criteria of earlier phases must keep passing.
- [ ] **C-5-02** The full end-to-end suite passes (US-1 to US-21). *Verify:* `uv run python tasks.py test`. *(automated, gate)*
- [ ] **C-5-03** Following README.md alone, the user sets up, syncs and uses the tool. *Verify:* phase 5 prompt, You check. *(human review: you confirm, gate)*

## Reviewer
Run **ui-reviewer** and **ai-output-reviewer** at the end.

## You check
- Break a programme file by hand: the banner reads in plain words; at 761 px the recommender sheet's first
  rows are visible without scrolling; dismiss a toast with the keyboard and confirm where focus lands.
- C-5-03: follow README.md from the top on your laptop as if new. Anything unclear goes into DECISIONS.md
  and gets fixed in the README.

## Out of scope
Programme research and re-research (Phase 2c); post-decision tracking, costs and scholarships, visa steps,
events log (all deferred by the user).
