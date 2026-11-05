# Phase 5: Programme research and polish

## Goal
A new programme is researched and added in under five minutes, marked unverified with sources, and the
whole tool is documented well enough to set up from the README alone.

**Recommended:** 1 session · medium effort. If the phase won't finish within that, stop at a clean point and run `/handoff`.

## Read first
docs/04_ai_features.md §7–8; docs/01_data_contract.md §3; docs/03_ui_spec.md §2 (US-16), §4.7, §5;
docs/05_integrations.md §1, §3, §5.

## Tasks
1. **Skill.** `.claude/skills/research-programme/SKILL.md` per docs/04 §7 (tools WebSearch, WebFetch; school
   pages first; every field with a source; always unverified).
2. **Research flow.** "Research a programme" on the dashboard and Add programme page: query field → run →
   preview with sources and nulls highlighted → Save writes `programmes/<id>.yaml` (US-16).
3. **Live test.** `tasks.py live -k research` on two programmes (e.g. "Berkeley Haas MFE", "Carnegie Mellon MSCF")
   validating schema, sources and `unverified` (C-5-01). Do not save these into the user's data folder.
4. **Usage panel.** Settings shows call count and estimated cost this month (docs/04 §8).
5. **README.md.** Prerequisites, setup on Windows, setting `data_dir` to OneDrive, `init-data`, Google setup
   (docs/05 §1), iPhone setup (docs/05 §3), Task Scheduler, daily use, troubleshooting (Claude Code not found,
   token expired, calendar missing on iPhone).
6. **Final pass.** Every empty/error state from docs/03 §5 present; no raw IDs or "None" visible (screenshot
   text scan); full suite green (C-5-02).

## Tests first
In your plan, map every automated criterion below to the test or script that checks it and the command
that runs it. After approval, write those tests **before** the implementation and show that they fail
for the right reason. Never edit a test to make it pass without saying so and getting approval.

## Acceptance criteria
Fixed at the design freeze (`config/criteria.json`) and guarded; never lowered without the user's approval. Criteria of earlier phases must keep passing.
- [ ] **C-5-01** Researching two programmes yields schema-valid files where every filled field has a source URL and the programme is marked unverified. *Verify:* `uv run python tasks.py live -k research`. *(automated, gate)*
- [ ] **C-5-02** The full end-to-end suite passes (US-1 to US-17). *Verify:* `uv run python tasks.py test`. *(automated, gate)*
- [ ] **C-5-03** Following README.md alone, the user sets up, syncs and uses the tool. *Verify:* phase 5 prompt, You check. *(human review: you confirm, gate)*

## Reviewer
Run **ui-reviewer** and **ai-output-reviewer** at the end.

## You check
- C-5-03: follow README.md from the top on your laptop as if new. Anything unclear goes into DECISIONS.md
  and gets fixed in the README.
- Research one programme you are considering and check its sources yourself before marking it verified.

## Out of scope
Post-decision tracking, costs and scholarships, visa steps, events log (all deferred by the user).
