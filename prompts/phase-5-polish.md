# Phase 5: Polish

## Goal
The whole tool is documented well enough to set up from the README alone, every page handles its empty and
error states, and the full end-to-end suite passes.

**Recommended:** 1 session · medium effort. If the phase won't finish within that, stop at a clean point and run `/handoff`.

## Read first
docs/04_ai_features.md §8; docs/03_ui_spec.md §2, §4.7, §5; docs/05_integrations.md §1, §3, §5;
docs/PROGRESS.md (accepted known issues of earlier phases).

## Tasks
1. **Usage panel.** Settings shows call count and estimated cost this month (docs/04 §8).
2. **README.md.** Prerequisites, setup on Windows, setting `data_dir` to OneDrive, `init-data`, Google setup
   (docs/05 §1), iPhone setup (docs/05 §3), Task Scheduler, daily use, troubleshooting (Claude Code not found,
   token expired, calendar missing on iPhone, a file locked by OneDrive).
3. **Unreadable programme file.** A clearer error page when a programme file cannot be loaded: name the file and
   the problem, and say how to fix it (accepted Phase 1 item).
4. **Final pass.** Every empty/error state from docs/03 §5 present; no raw IDs or "None" visible (screenshot
   text scan); full suite green, US-1 to US-21 (C-5-02).

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
- C-5-03: follow README.md from the top on your laptop as if new. Anything unclear goes into DECISIONS.md
  and gets fixed in the README.

## Out of scope
Programme research and re-research (Phase 2c); post-decision tracking, costs and scholarships, visa steps,
events log (all deferred by the user).
