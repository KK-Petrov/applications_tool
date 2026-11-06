# Progress

Estimated sessions are the kit's recommendation. `/phase` and `/resume` add 1 to *Actual* at the start of each session and recalibrate the remaining estimates when phases run long.

| Phase | Status | Est. sessions | Actual | Effort | Date | Key results | Known issues |
|---|---|---|---|---|---|---|---|
| 0 Scaffold | Done | 1 | 1 | medium | 2026-11-05 | C-0-01 PASS (real-clone sim, 130 tests); C-0-02 PASS (test-fast 8.9 s); C-0-03 PASS (15); C-0-04 PASS (21); You check confirmed by user; follow-up 2026-11-05 (block style, aware timestamps) | – (minor limitations in Phase notes) |
| 1 Programmes and dashboard | Done with known issues | 1–2 | 1 | medium | 2026-11-05 | C-1-01 to C-1-05 PASS (20/20 tz cases, axe 0 serious); C-1-06 confirmed by user; 369 tests, fast 25 s; both reviewers READY | low items in Phase notes |
| 2a Recommender sheet | Done with known issues | 1 | 1 | medium | 2026-11-06 | C-2a-01 PASS (US-6 to US-9, 14 e2e); C-2a-02 PASS (40 cases, priorities 1–8); C-2a-03 PASS (115–290 ms worst warm load, limit 1000); You check confirmed by user; 510 tests, test-fast 26.6 → ~14 s with xdist (8 workers); data-integrity READY, ui READY | minor items in Phase notes |
| 2b Recommendation email generator | Not started | 1–2 | 0 | high | | | |
| 2c Programme research | Not started | 1–2 | 0 | high | | | |
| 3 Reminders, calendar and digest | Not started | 1–2 | 0 | high | | | |
| 4a Biography bank, essays, CVs and exact checks | Not started | 1 | 0 | medium | | | |
| 4b AI essay and CV evaluator | Not started | 1–2 | 0 | high | | | |
| 5 Polish | Not started | 1 | 0 | medium | | | |

Status values: Not started · In progress · Done · Done with known issues

## Calibration
Actual ÷ estimated sessions over finished phases: 0.75 (Phase 0: 1 ÷ 1; Phase 1: 1 ÷ 2; Phase 2a: 1 ÷ 1; total 3 ÷ 4). Below 1.3, so the remaining estimates stand. (updated by /phase and /resume)

## Hand-offs
<!-- /handoff writes a section here: Done / Half-done / Left / Open questions / Next first step -->

## Phase notes
<!-- At the end of each phase: what was built, how to run it, metrics, open issues. -->

### Phase 2a: Recommender sheet (2026-11-06, session 1)
- **Task 0 (tests):** pytest-xdist, 8 workers (fastest of auto/4/8, median of 3: 11.7/11.7/10.2 s); `serial` marker for timing gates (second, non-parallel pass); warning above `performance.test_fast_warn_seconds` (45 s). test-fast 26.6 s → 11.1 s at the time (348 tests); full `test` 78 s → ~21 s. Isolation: audit clean; e2e server now receives its bound socket (port race fixed); security tests share one app per module whose teardown proves no write (DECISIONS #38).
- **Built:** `rules/letters.py` (in_play, over_ask, next_action, bucket, set_status), `sheet.py` (columns by deadline, rows, filter/sort), `professor_form.py` (form ↔ Professor, CSV), `routes/recommenders.py` (sheet, status cells, add/edit, panel, mark as requested, CSV preview/save), `Store.create_professor` (exclusive, case-insensitive), templates, CSS, app.js (menus, auto-submit, selection).
- **Results:** C-2a-01 12/12 recommender e2e; C-2a-02 39/39; C-2a-03 worst of 5 warm loads 115–290 ms over runs (cold 407–913 ms; server render median 24–64 ms); C-1-05 axe 0 serious on 4 new pages + open menu + panel. Suite 508 passed (507 parallel + 1 serial); test-fast median 14.1 s.
- **Reviewers:** data-integrity NOT READY (overwrite on a case-only name clash, browser maxlength trimming, letter with history deleted on Edit, unchanged save rewrote files, partial import message) → fixed → **READY** (its 4 low findings fixed after). ui-reviewer NOT READY (unverified badge missing in totals, Next action off-screen at 1280 px, selection/panel stale) → fixed → re-review NOT READY (selection lost after clicking a name) → fixed with an e2e regression test → final review **READY**; its one WARN (Mark as requested lost search/filter/sort after a click) fixed with an e2e test. DECISIONS #38–#43. You check confirmed by the user (one real professor added; UI works; Next action correct).
- **Tests changed with disclosure (all new this phase, before passing):** e2e cell checks made stricter (data-status + trigger text: the open menu's buttons made `to_contain_text` pass early); US-8 Done expectation corrected (Quinn and Santos are Done); `aria-selected` → `aria-current` (aria-selected is invalid on a table row); maxlength assertion replaced by "no maxlength" (review H2); "University is required" → "Other institution is required"; a too-broad `".."` check narrowed. Phase 0/1 tests unchanged except test_security.py (shared app, DECISIONS #38).
- **Decided (DECISIONS #43):** re-entering Requested keeps the original requested_on (L1); no date-order check (L2, accepted); deleted professor IDs can be reused (L5, accepted for now).
- **For Phase 3:** confirm that a reused professor ID cannot leave a stale or wrong calendar event (reminder IDs embed the professor ID; DECISIONS #43, L5).
- **Open / minor (accepted):** the create lock covers one process; totals strip pushes the table below the fold at 761 px; a status menu in a low row may need scrolling inside the sheet box; no-JS failure page stays at the POST address.
- **Run:** `uv run python tasks.py dev` → http://127.0.0.1:8765/recommenders.

### Phase 1: Programmes and dashboard (2026-11-05, session 1)
- **Built:** `rules/` (timezones, countdown, programmes, letters counts, interim alert), `views.py`, `web.py` (one `when`/`school_time` filter set + `_time.html` provenance macros), `routes/programmes.py` (list, page, add/edit form with repeatable rows, verify, requirement/step/document toggles, interviews), `programme_form.py`, `security.py` (Host/Origin/Referer checks), header zone selector (`POST /display-tz`), dashboard (alert, 4 tiles, table, reminders placeholder), CSS from tokens only, store load cache.
- **Results:** C-1-01 6/6 provenance tests in both zones; C-1-02 20/20 reference cases (`tz_cases_failed = 0`); C-1-03 all 5 boundary cases plus edges; C-1-04 US-1 to US-5 pass (18 e2e tests in test_programmes.py); C-1-05 `axe_serious_violations = 0` on 13 pages + Rotterdam mode + open forms; C-1-06 confirmed by the user (with the document-date fix, DECISIONS #32). Suite 369 passed (test-fast 25.1 s, limit 60 s; machine was running ~2× slower than earlier in the session); criteria guard OK.
- **Reviewers:** ui-reviewer NOT READY → its FAIL (silent failed HTMX toggle) and warnings fixed and tested; data-integrity-reviewer NOT READY → findings fixed with regression tests (hand-written file round-trip, step order, aliases, verify shape, inf fee); its other two blockers (Phase 0 test, missing DECISIONS rows) were already resolved when it reported. Re-reviews: data-integrity NOT READY (locked verify showed Verified) → fixed → **READY**; ui NOT READY twice (failure shown off-screen; error copy and toast) → fixed → **READY**, its last finding (toast reset wiped a half-typed interview) fixed after the READY verdict and tested (#36). DECISIONS #20–#36. Columbia seed source moved to Columbia Business School's own page (#31).
- **Tests changed with disclosure:** Phase 0 `test_static_assets_are_served_locally` narrowed (user-approved, #28); shell test client moved to `base_url=127.0.0.1:8765` because of the Host check (fixture, intent unchanged); e2e `live_server` uses a fixed clock.
- **Open / minor (accepted):** an unreadable programme file shows "Page not found" with the banner naming the file; the alert's "today" is the display-zone date, so "overdue" can show up to 6 h early in Rotterdam mode; toggling a deadline's "time not confirmed" flag alone does not reset its verification (a changed source URL does, DECISIONS #30); single-line form fields are trimmed of outer spaces; a `\r\n` stored inside a multi-line field comes back as `\n` after a form save (browsers normalise textarea line breaks); a no-JS validation error renders the page at the POST URL (a reload re-posts); the DataError banner for a hand-broken file can show Pydantic wording (e.g. "Input should be a finite number"; form errors are in words); a hand-quoted step `order: "2"` loses its YAML comment on save (data correct, same class as quoted dates); a leftover read-only `<file>.tmp` blocks saves of that file with a "locked or read-only" message that doesn't name the `.tmp`; the form validates in two rounds (form checks first, model checks on the next submit); after Dismiss on the toast, focus goes to the page body, and the toast sits early in the tab order; toggles have no `hx-sync` (45 concurrent toggles lost 0 updates in a probe).
- **Run:** `uv run python tasks.py dev` → http://127.0.0.1:8765.

### Phase 0: Scaffold (2026-11-04)
- **Built:** `pyproject.toml` (Python 3.12, uv), `tasks.py` (all CLAUDE.md targets; eval targets print "not yet implemented"), typed config loader with `HUB_DATA_DIR` override, Pydantic models for every docs/01 §2–8 file, `store.py` (DataError, comment-preserving atomic saves), `hub init-data`, FastAPI shell (sidebar, header, DataError banner, six empty pages incl. `/programmes`), `tokens.css`, vendored htmx 2.0.4, privacy test, pytest + Playwright harness.
- **Run:** `uv run python tasks.py dev` → http://127.0.0.1:8765; `uv run hub init-data`.
- **Metrics:** 95 tests (88 fast + 7 e2e); test-fast 7.6 s; full suite in a real `git clone` (CRLF checkout) 95/95.
- **Reviewers:** ui-reviewer found the sidebar growing to ~452 px (fixed, now tested); data-integrity-reviewer found two data-loss defects in saves and a validation gap (fixed, 14 regression tests; DECISIONS #16).
- **Open:** see Known issues above; time-zone helpers (instant, primary deadline) deliberately left for Phase 1 `rules/`.
- **Phase 0 follow-up (2026-11-05, same session; not counted in any estimate):** block style everywhere (DECISIONS #19: seeds and fixtures converted once, data and comments checked identical; one-field change = one-line diff; CRLF kept; no wrapping) and aware timestamps (DECISIONS #18 applied now: naive values rejected with a DataError naming the field; written as unquoted ISO 8601 with offset; loaded values rebuilt as plain `datetime`). Tests: 130 (123 fast in 8.9 s + 7 e2e); real-clone run 130/130; data-integrity-reviewer: ready, its medium finding (ruamel `TimeStamp` in models) fixed with 4 regression tests.
- **Minor limitations (accepted for now, low severity):** a hand-quoted date or timestamp (`"2026-11-02"`) is rewritten unquoted on the first save, so that save writes although no value changed; a comment inside a multi-line flow collection is lost when an old flow-style file is converted (ruamel limit, documented in docs/01 §1).
- **For later phases:** DECISIONS #13 (min 1 deadline) implies: Phase 1 Add/Edit programme form requires at least one deadline with tz and source_url; Phase 2c research preview must block Save until the user enters a deadline if the skill returned none.
