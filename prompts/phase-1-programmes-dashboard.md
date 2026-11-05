# Phase 1: Programmes and dashboard

## Goal
The user sees every programme with correct deadlines in his chosen time zone, opens a programme page
with requirements, steps, documents and interviews, and can add, edit and verify programmes.

**Recommended:** 1–2 sessions · medium effort. If the phase won't finish within that, stop at a clean point and run `/handoff`.

## Read first
docs/02_domain_rules.md §1–3, §6; docs/03_ui_spec.md §1, §2 (US-1 to US-5), §4.1, §4.2, §5–7;
docs/01_data_contract.md §3; config/project.yaml → `time`, `urgency`.

## Tasks
1. **Time rules.** `src/hub/rules/timezones.py`: instant from local + zone (fold 0; gap shift + flag),
   conversion, formatting with zone abbreviations. All 20 cases of docs/02 §1 as a parametrised test (C-1-02).
2. **Countdown.** `src/hub/rules/countdown.py` per docs/02 §2, including the boundary cases (C-1-03).
3. **Display zone.** Header selector posts to `/display-tz`, saved in `settings.display_tz`; every time on
   every page renders through one template filter so nothing can bypass it.
4. **Dashboard.** docs/03 §4.1: alert strip (most urgent item; for now from deadlines and letters required —
   Phase 3 replaces it with the reminder engine), stat tiles, programmes table, upcoming list placeholder
   ("Reminders arrive in Phase 3").
5. **Programme page.** docs/03 §4.2 with HTMX toggles for requirements, steps and document statuses;
   interviews add/edit with date, time and zone; source box; verification badge with its note.
6. **Add / edit programme.** Form for every field in docs/01 §3 that a person would type (repeatable rows for
   deadlines, requirements, steps, documents, essays). New programmes are always unverified; editing a
   deadline resets it to unverified (docs/02 §6).
7. **Provenance guard.** A deadline renders only through a component that prints zone and source link; an
   e2e test greps every page for dates without a zone (C-1-01).
8. **E2E and accessibility.** Playwright tests for US-1 to US-5; axe on every page (C-1-05).

## Tests first
In your plan, map every automated criterion below to the test or script that checks it and the command
that runs it. After approval, write those tests **before** the implementation and show that they fail
for the right reason. Never edit a test to make it pass without saying so and getting approval.

## Acceptance criteria
Fixed at the design freeze (`config/criteria.json`) and guarded; never lowered without the user's approval. Criteria of earlier phases must keep passing.
- [ ] **C-1-01** No deadline is displayed without its time zone and a source link; unverified data is always visibly marked. *Verify:* `uv run pytest tests/e2e/test_programmes.py -k provenance`. *(automated, gate)*
- [ ] **C-1-02** Time-zone conversion is correct on the 20 reference cases, including daylight-saving changeovers — `tz_cases_failed == 0`. *Verify:* `uv run pytest tests/test_timezones.py`. *(automated, gate)*
- [ ] **C-1-03** Days-left values match docs/02 §2 on boundary cases. *Verify:* `uv run pytest tests/test_countdown.py`. *(automated, gate)*
- [ ] **C-1-04** Programme user stories pass end to end (US-1 to US-5 in docs/03 §2). *Verify:* `uv run pytest tests/e2e/test_programmes.py`. *(automated, gate)*
- [ ] **C-1-05** No serious or critical accessibility violations on any page — `axe_serious_violations == 0`. *Verify:* `uv run pytest tests/e2e/test_a11y.py`. *(automated, gate)*
- [ ] **C-1-06** Dashboard and programme page match the agreed mockup and read clearly. *Verify:* phase 1 prompt, You check. *(human review: you confirm, gate)*

## Reviewer
Run **ui-reviewer** and **data-integrity-reviewer** at the end.

## You check
- C-1-06: open the dashboard and the Princeton page next to the mockup. Main message clear in 5 seconds?
  Days left 46 on 30 Oct (or the right number for today)? Princeton shown as 16 Dec 05:59 in Rotterdam mode?
- Open each programme's source link and mark any you have checked as verified.

## Out of scope
Recommenders (2a), reminders and calendar (3), essays (4a), the Research button (5).
