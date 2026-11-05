# Phase 4a: Biography bank, essays, CVs and exact checks

## Goal
The user keeps his background in one biography bank, stores essays and CVs by version, and sees an exact
word count and any wrong-school mention as he works.

**Recommended:** 1 session · medium effort. If the phase won't finish within that, stop at a clean point and run `/handoff`.

## Read first
docs/01_data_contract.md §5–7; docs/02_domain_rules.md §7–8; docs/03_ui_spec.md §2 (US-11 to US-14), §4.5, §4.6;
config/project.yaml → `wrong_school`.

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

## Tests first
In your plan, map every automated criterion below to the test or script that checks it and the command
that runs it. After approval, write those tests **before** the implementation and show that they fail
for the right reason. Never edit a test to make it pass without saying so and getting approval.

## Acceptance criteria
Fixed at the design freeze (`config/criteria.json`) and guarded; never lowered without the user's approval. Criteria of earlier phases must keep passing.
- [ ] **C-4a-01** The wrong-school check catches all 20 planted mentions with no false positives on 10 clean essays — `wrong_school_misses_plus_false_positives == 0`. *Verify:* `uv run pytest tests/test_wrong_school.py`. *(automated, gate)*
- [ ] **C-4a-02** Word count matches the rule in docs/02 §7 on all 15 fixtures. *Verify:* `uv run pytest tests/test_word_count.py`. *(automated, gate)*
- [ ] **C-4a-03** Biography, essay, version and CV user stories pass end to end (US-11 to US-14). *Verify:* `uv run pytest tests/e2e/test_essays.py tests/e2e/test_biography.py`. *(automated, gate)*

## Reviewer
Run **ui-reviewer** and **data-integrity-reviewer** at the end.

## You check
- Enter your real biography entries (education, test scores, experience, anticipated honours, goals) in the app.
- Paste a real draft; check the word count against your own count and plant a competitor's name to see it flagged.

## Out of scope
AI evaluation (4b).
