# Phase 3: Reminders, calendar and digest

## Goal
Every deadline, letter and document reminder reaches the user's iPhone through the Applications calendar,
and a daily digest email summarises what is due, without duplicates and without ever emailing anyone else.

**Recommended:** 1–2 sessions · high effort. If the phase won't finish within that, stop at a clean point and run `/handoff`.

## Read first
docs/02_domain_rules.md §5 (the golden table); docs/05_integrations.md (all); docs/03_ui_spec.md §2 (US-17), §4.1, §4.7;
config/project.yaml → `reminders`, `google`, `digest`, `schedule`.

## Tasks
1. **Reminder engine.** `src/hub/rules/reminders.py`: pure function `(programmes, professors, settings, today)
   → list[Reminder]` per docs/02 §5; golden CSV with the 30 rows and the extra cases listed there (C-3-01).
   Replace the dashboard alert strip and "Upcoming reminders" with engine output.
2. **Fake Google.** `tests/fakes/fake_google.py` per docs/05 §2.5 (Calendar + Gmail, error injection).
3. **Calendar sync.** `src/hub/google/calendar_sync.py` per docs/05 §2.1–2.5: create calendar, insert/patch/delete
   by hash, map saved after each call, rebuild from extended properties, dry-run (C-3-02).
4. **OAuth.** `hub connect-google` per docs/05 §1. First verify against Google's current docs: the
   7-day refresh-token expiry in Testing mode and whether `calendar.app.created` covers creating the calendar
   and its events. Record both findings in DECISIONS.md; adjust scopes in config if needed.
5. **Digest.** `src/hub/google/digest.py` per docs/05 §4 with golden text (C-3-03); recipient fixed to
   `owner_email`; `tests/test_no_outbound_email.py` (C-3-06).
6. **Settings page.** Connect status, Sync now (+ dry-run), last sync counts, digest preview (US-17).
7. **Task Scheduler.** `hub install-schedule` / `hub uninstall-schedule` per docs/05 §5; argv tested with a mock.
8. **Live smoke test.** `tasks.py live -k calendar` per docs/05 §6 (C-3-04), run once the user has connected Google.

## Tests first
In your plan, map every automated criterion below to the test or script that checks it and the command
that runs it. After approval, write those tests **before** the implementation and show that they fail
for the right reason. Never edit a test to make it pass without saying so and getting approval.

## Acceptance criteria
Fixed at the design freeze (`config/criteria.json`) and guarded; never lowered without the user's approval. Criteria of earlier phases must keep passing.
- [ ] **C-3-01** The reminder engine reproduces the worked example and all 30 rows of the golden reminder table. *Verify:* `uv run pytest tests/test_reminders.py`. *(automated, gate)*
- [ ] **C-3-02** Calendar sync is idempotent: two runs create no duplicates; a changed deadline updates events; resolved reminders are removed (against the fake Google API). *Verify:* `uv run pytest tests/test_calendar_sync.py`. *(automated, gate)*
- [ ] **C-3-03** The digest matches its golden file and can only be sent to the owner address in settings. *Verify:* `uv run pytest tests/test_digest.py`. *(automated, gate)*
- [ ] **C-3-04** Live smoke test creates, updates and deletes one test event in the Applications calendar. *Verify:* `uv run python tasks.py live -k calendar`. *(automated, gate)*
- [ ] **C-3-05** Reminders appear on the iPhone in Apple Calendar with alerts. *Verify:* phase 3 prompt, You check. *(human review: you confirm, gate)*
- [ ] **C-3-06** No code path can send email to any address other than the owner's. *Verify:* `uv run pytest tests/test_no_outbound_email.py`. *(automated, gate)*

## Reviewer
Run **data-integrity-reviewer** at the end.

## You check
- Follow README/docs/05 §1 to connect Google, then run `uv run hub sync`.
- C-3-05: on your iPhone, the Applications calendar shows the Princeton reminders (3 Nov request letters,
  15 Dec deadline) with alerts. Change a deadline in the app, sync again, and see the event move, not duplicate.
- Run `uv run hub digest --dry-run` and read it; then send one for real and check your inbox.

## Out of scope
Essays (4a/4b); reminders for decisions, scholarships or visas (out of scope for this build).
