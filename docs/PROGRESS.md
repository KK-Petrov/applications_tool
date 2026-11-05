# Progress

Estimated sessions are the kit's recommendation. `/phase` and `/resume` add 1 to *Actual* at the start of each session and recalibrate the remaining estimates when phases run long.

| Phase | Status | Est. sessions | Actual | Effort | Date | Key results | Known issues |
|---|---|---|---|---|---|---|---|
| 0 Scaffold | Done | 1 | 1 | medium | 2026-11-05 | C-0-01 PASS (real-clone sim, 130 tests); C-0-02 PASS (test-fast 8.9 s); C-0-03 PASS (15); C-0-04 PASS (21); You check confirmed by user; follow-up 2026-11-05 (block style, aware timestamps) | – (minor limitations in Phase notes) |
| 1 Programmes and dashboard | Not started | 1–2 | 0 | medium | | | |
| 2a Recommender sheet | Not started | 1 | 0 | medium | | | |
| 2b Recommendation email generator | Not started | 1–2 | 0 | high | | | |
| 3 Reminders, calendar and digest | Not started | 1–2 | 0 | high | | | |
| 4a Biography bank, essays, CVs and exact checks | Not started | 1 | 0 | medium | | | |
| 4b AI essay and CV evaluator | Not started | 1–2 | 0 | high | | | |
| 5 Programme research and polish | Not started | 1 | 0 | medium | | | |

Status values: Not started · In progress · Done · Done with known issues

## Calibration
Actual ÷ estimated sessions over finished phases: 1.0 (Phase 0: 1 ÷ 1) (updated by /phase and /resume)

## Hand-offs
<!-- /handoff writes a section here: Done / Half-done / Left / Open questions / Next first step -->

## Phase notes
<!-- At the end of each phase: what was built, how to run it, metrics, open issues. -->

### Phase 0: Scaffold (2026-11-04)
- **Built:** `pyproject.toml` (Python 3.12, uv), `tasks.py` (all CLAUDE.md targets; eval targets print "not yet implemented"), typed config loader with `HUB_DATA_DIR` override, Pydantic models for every docs/01 §2–8 file, `store.py` (DataError, comment-preserving atomic saves), `hub init-data`, FastAPI shell (sidebar, header, DataError banner, six empty pages incl. `/programmes`), `tokens.css`, vendored htmx 2.0.4, privacy test, pytest + Playwright harness.
- **Run:** `uv run python tasks.py dev` → http://127.0.0.1:8765; `uv run hub init-data`.
- **Metrics:** 95 tests (88 fast + 7 e2e); test-fast 7.6 s; full suite in a real `git clone` (CRLF checkout) 95/95.
- **Reviewers:** ui-reviewer found the sidebar growing to ~452 px (fixed, now tested); data-integrity-reviewer found two data-loss defects in saves and a validation gap (fixed, 14 regression tests; DECISIONS #16).
- **Open:** see Known issues above; time-zone helpers (instant, primary deadline) deliberately left for Phase 1 `rules/`.
- **Phase 0 follow-up (2026-11-05, same session; not counted in any estimate):** block style everywhere (DECISIONS #19: seeds and fixtures converted once, data and comments checked identical; one-field change = one-line diff; CRLF kept; no wrapping) and aware timestamps (DECISIONS #18 applied now: naive values rejected with a DataError naming the field; written as unquoted ISO 8601 with offset; loaded values rebuilt as plain `datetime`). Tests: 130 (123 fast in 8.9 s + 7 e2e); real-clone run 130/130; data-integrity-reviewer: ready, its medium finding (ruamel `TimeStamp` in models) fixed with 4 regression tests.
- **Minor limitations (accepted for now, low severity):** a hand-quoted date or timestamp (`"2026-11-02"`) is rewritten unquoted on the first save, so that save writes although no value changed; a comment inside a multi-line flow collection is lost when an old flow-style file is converted (ruamel limit, documented in docs/01 §1).
- **For later phases:** DECISIONS #13 (min 1 deadline) implies: Phase 1 Add/Edit programme form requires at least one deadline with tz and source_url; Phase 5 research preview must block Save until the user enters a deadline if the skill returned none.
