# Phase 2a: Recommender sheet

## Goal
Many professors readable at a glance: one row per professor, one status cell per programme, changed with
one click, plus the Add professor page with a pasted course description.

**Recommended:** 1 session · medium effort. If the phase won't finish within that, stop at a clean point and run `/handoff`.

## Read first
docs/01_data_contract.md §4; docs/02_domain_rules.md §4; docs/03_ui_spec.md §2 (US-6 to US-9), §4.3, §4.4, §7;
config/project.yaml → `letters`, `performance`.

## Tasks
1. **Letter rules.** `src/hub/rules/letters.py`: required (with assumed default), confirmed, in-play,
   over-ask warning, request_due, next action with the priority table of docs/02 §4. Fixture set covering
   every priority row (C-2a-02).
2. **Sheet.** `/recommenders` per docs/03 §4.3: totals strip, toolbar (search, filter, sort, count), sticky
   table with one column per programme ordered by deadline, legend. Email never rendered in the sheet.
3. **Status cells.** Button + menu; POST `/recommenders/{id}/letters/{programme_id}` swaps the cell and the row's
   next action; stamps the matching date; a cell with no letter shows "—" and offers "Ask for this programme".
4. **Add professor.** `/recommenders/new` per docs/03 §4.4; the description textarea accepts up to 20,000
   characters and is stored verbatim (block scalar in YAML; test round-trips a 3,000-character text with
   line breaks, quotes and non-ASCII characters exactly). "Save and generate email" is disabled with a
   tooltip until Phase 2b.
5. **Detail panel.** `/recommenders/{id}/panel`: details (email shown here), Edit, Mark as requested; email area
   placeholder for 2b.
6. **CSV import.** Columns from docs/03 §4.3; preview before saving; rows with errors listed, valid rows imported.
7. **Performance.** Fixture with 40 professors × 4 programmes; measure server render + Playwright load (C-2a-03).
8. **E2E.** US-6 to US-9 (C-2a-01), axe on both pages.

## Tests first
In your plan, map every automated criterion below to the test or script that checks it and the command
that runs it. After approval, write those tests **before** the implementation and show that they fail
for the right reason. Never edit a test to make it pass without saying so and getting approval.

## Acceptance criteria
Fixed at the design freeze (`config/criteria.json`) and guarded; never lowered without the user's approval. Criteria of earlier phases must keep passing.
- [ ] **C-2a-01** Recommender user stories pass end to end (US-6 to US-9), including a 3,000-character pasted course description saved verbatim. *Verify:* `uv run pytest tests/e2e/test_recommenders.py`. *(automated, gate)*
- [ ] **C-2a-02** Letter coverage counts and next actions match docs/02 §4 on the fixture set. *Verify:* `uv run pytest tests/test_letters.py`. *(automated, gate)*
- [ ] **C-2a-03** The sheet stays fast with many professors — `sheet_render_ms_40_professors < 1000 ms`. *Verify:* `uv run pytest tests/e2e/test_recommenders.py -k performance`. *(automated, gate)*

## Reviewer
Run **ui-reviewer** and **data-integrity-reviewer** at the end.

## You check
- Add your real professors with their course descriptions (in the app only; never into the repository).
  Can you see who needs what in under 10 seconds? Does "Next action" say the right thing?

## Out of scope
Generating emails (2b), reminders (3).
