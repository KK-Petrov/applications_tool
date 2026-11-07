# Phase 4a: Biography bank, essays, CVs and exact checks

## Goal
The user keeps his background in one biography bank, stores essays and CVs by version, and sees an exact
word count and any wrong-school mention as he works. Request emails to professors draw on the biography bank
and tie each professor's course to a programme through its stored curriculum (DECISIONS #59).

**Recommended:** 1–2 sessions · high effort (DECISIONS #59). If the phase won't finish within that, stop at a clean point and run `/handoff`.

## Read first
docs/01_data_contract.md §5–7, §10; docs/02_domain_rules.md §7–8; docs/03_ui_spec.md §2 (US-11 to US-14), §4.5, §4.6;
docs/04_ai_features.md §2–4 (the /rec-email upgrade); config/project.yaml → `wrong_school`, `email`.

## Tasks
1. **Biography bank.** Page and CRUD per docs/03 §4.6; categories with counts; anticipated flag; "used in n
   essays" (count from the latest evaluation's `used` list once 4b exists; 0 until then).
2. **Word count.** `src/hub/rules/word_count.py` per docs/02 §7; 15 fixtures with expected counts (C-4a-02).
3. **Wrong-school check.** `src/hub/rules/wrong_school.py` per docs/02 §8; planted and clean fixture sets
   (20 mentions, 10 clean essays) (C-4a-01); "intended" marking stored per version.
4. **Essay store.** Essays per programme from `programmes/<id>.yaml → essays`; versions per docs/01 §6
   (immutable vN.md, meta.yaml); live word count and wrong-school hits via HTMX on input (debounced 500 ms).
5. **Compare.** Side-by-side diff of two versions (difflib, rendered server-side) (US-13).
6. **CVs.** Base CV and one tailored CV per programme per docs/01 §7; same editor and checks (US-14).
7. **E2E.** US-11 to US-14 (C-4a-03) and axe on the new pages.
8. **Grounded /rec-email** (DECISIONS #59; docs/04 §2–4; C-4a-04).
   - `payloads.py`: biography selection (3–5 entries, deterministic, docs/04 §2.4), `selected_biography_ids`,
     each selected programme's curriculum (bracketed notes stripped); the parameters go into
     `config/project.yaml → email`.
   - `rec_email.json` gains required `programme_links`; SKILL.md gains the link and selected-background rules,
     a shorter offer and thanks, and an opening and closing drawn from the link.
   - `email_checks.py`: used-entry and link checks, shared by the app (warnings, never blocking, including
     the unverified-curriculum warning) and the graders.
   - `/grade-claims`: its facts include the curriculum.
   - Eval: invented curriculum courses for the frozen eval programmes (Booth keeps only a summary), biography
     entries matching some cases' courses, and a 4th calibration email (an invented programme course, not
     declared in `programme_links`) reported as `undeclared_course_caught`; `grader_calibration_passed` stays
     on emails 1–3.
   - Tune SKILL.md on cases 1–7 only; C-2b-01 to C-2b-04 must pass again on the changed eval set.

## Tests first
In your plan, map every automated criterion below to the test or script that checks it and the command
that runs it. After approval, write those tests **before** the implementation and show that they fail
for the right reason. Never edit a test to make it pass without saying so and getting approval.
For C-4a-04 the fast payload and grader tests come first (selection, used entries, valid and invented
links, summary-only programme); the live eval comes after.

## Acceptance criteria
Fixed at the design freeze (`config/criteria.json`) and guarded; never lowered without the user's approval. Criteria of earlier phases must keep passing.
- [ ] **C-4a-01** The wrong-school check catches all 20 planted mentions with no false positives on 10 clean essays — `wrong_school_misses_plus_false_positives == 0`. *Verify:* `uv run pytest tests/test_wrong_school.py`. *(automated, gate)*
- [ ] **C-4a-02** Word count matches the rule in docs/02 §7 on all 15 fixtures. *Verify:* `uv run pytest tests/test_word_count.py`. *(automated, gate)*
- [ ] **C-4a-03** Biography, essay, version and CV user stories pass end to end (US-11 to US-14). *Verify:* `uv run pytest tests/e2e/test_essays.py tests/e2e/test_biography.py`. *(automated, gate)*
- [ ] **C-4a-04** Every eval email uses at least 2 of its selected biography entries (verified in the text), links the professor's course to at least one selected programme through a programme element found verbatim in the stored curriculum or summary, and contains 0 invented programme elements, while staying within 180–280 words — `email_grounding_failures == 0`. *Verify:* `uv run python tasks.py eval-email` → reports/eval_email.json: grounding_failures (tuning cases 1–7 and held-out cases 8–10 reported separately). *(automated, gate)*
- Reported, not gated: `undeclared_course_caught` (4th calibration email), `biography_id_mismatches`.
- C-2b-01 to C-2b-04 are re-run on the changed eval set and must keep passing.

## Reviewer
Run **ui-reviewer**, **data-integrity-reviewer** and **ai-output-reviewer** (skill, schema, eval set and
graders change) at the end.

## You check
- Enter your real biography entries (education, test scores, experience, anticipated honours, goals) in the app.
- Paste a real draft; check the word count against your own count and plant a competitor's name to see it flagged.
- Generate request emails for two real professors: the course-to-programme link and the background entries
  are true and read naturally, and the openings differ.

## Out of scope
AI evaluation (4b).
