# Acceptance criteria

Rendered from `config/criteria.json` (the source of truth). Frozen at the design freeze and fingerprinted in `config/criteria.lock.json`; see START_HERE.md for how a change is approved.

Methods: **automated** (a test decides) · **human** (you decide, with the phase's "You check" list) · **reported** (measured and shown, not gated).

## Phase 0: Scaffold

| ID | Criterion | Threshold | Method | Gate | Verify | Baseline | Why |
|---|---|---|---|---|---|---|---|
| C-0-01 | From a fresh clone on Windows, setup and the full test suite pass | pass/fail | automated | yes | fresh clone, then `uv sync && uv run playwright install chromium && uv run python tasks.py test` | – | – |
| C-0-02 | Fast tests finish quickly | test_fast_seconds < 60 s | automated | yes | time `uv run python tasks.py test-fast` | – | They run after every Claude turn |
| C-0-03 | No personal data or secrets in the repository; the data folder and token files are outside Git | pass/fail | automated | yes | `uv run pytest tests/test_privacy.py` (scans tracked files for email addresses other than example.com/example.org, tokens, and data/ paths; checks .gitignore) | – | – |
| C-0-04 | Every seed file validates against the schema; an invalid file produces an error naming the file and field | pass/fail | automated | yes | `uv run pytest tests/test_schema.py` | – | – |

## Phase 1: Programmes and dashboard

| ID | Criterion | Threshold | Method | Gate | Verify | Baseline | Why |
|---|---|---|---|---|---|---|---|
| C-1-01 | No deadline is displayed without its time zone and a source link; unverified data is always visibly marked | pass/fail | automated | yes | `uv run pytest tests/e2e/test_programmes.py -k provenance` | – | – |
| C-1-02 | Time-zone conversion is correct on the 20 reference cases, including daylight-saving changeovers | tz_cases_failed == 0 | automated | yes | `uv run pytest tests/test_timezones.py` | – | A deadline shown an hour or a day off is the costliest error the tool can make |
| C-1-03 | Days-left values match docs/02 §2 on boundary cases | pass/fail | automated | yes | `uv run pytest tests/test_countdown.py` | – | – |
| C-1-04 | Programme user stories pass end to end (US-1 to US-5 in docs/03 §2) | pass/fail | automated | yes | `uv run pytest tests/e2e/test_programmes.py` | – | – |
| C-1-05 | No serious or critical accessibility violations on any page | axe_serious_violations == 0 | automated | yes | `uv run pytest tests/e2e/test_a11y.py` | – | WCAG 2.2 AA; keeps the tool usable on a small laptop screen |
| C-1-06 | Dashboard and programme page match the agreed mockup and read clearly | pass/fail | human | yes | phase 1 prompt, You check | – | – |

## Phase 2a: Recommender sheet

| ID | Criterion | Threshold | Method | Gate | Verify | Baseline | Why |
|---|---|---|---|---|---|---|---|
| C-2a-01 | Recommender user stories pass end to end (US-6 to US-9), including a 3,000-character pasted course description saved verbatim | pass/fail | automated | yes | `uv run pytest tests/e2e/test_recommenders.py` | – | – |
| C-2a-02 | Letter coverage counts and next actions match docs/02 §4 on the fixture set | pass/fail | automated | yes | `uv run pytest tests/test_letters.py` | – | – |
| C-2a-03 | The sheet stays fast with many professors | sheet_render_ms_40_professors < 1000 ms | automated | yes | `uv run pytest tests/e2e/test_recommenders.py -k performance` | – | The user will have many professors; the sheet must stay a quick glance |

## Phase 2b: Recommendation email generator

| ID | Criterion | Threshold | Method | Gate | Verify | Baseline | Why |
|---|---|---|---|---|---|---|---|
| C-2b-01 | Every generated email validates against its JSON schema | email_schema_failures == 0 | automated | yes | `uv run python tasks.py eval-email` → reports/eval_email.json: schema_failures | – | The app reads the output by machine |
| C-2b-02 | Every eval email names the right professor and course, lists each selected programme with its exact stored deadline, and mentions no unselected programme | email_fact_failures == 0 | automated | yes | `uv run python tasks.py eval-email` → reports/eval_email.json: fact_failures | – | A wrong deadline or name in a request to a professor is unrecoverable |
| C-2b-03 | Eval emails are 180–280 words, open with 'Dear Professor {surname},' and never state an anticipated achievement as achieved | email_form_failures == 0 | automated | yes | `uv run python tasks.py eval-email` → reports/eval_email.json: form_failures | – | Formal register and honesty about the anticipated cum laude |
| C-2b-04 | No unsupported claims in eval emails, and the claim grader flags all 3 planted calibration emails | unsupported_claims == 0 | automated | yes | `uv run python tasks.py eval-email` → reports/eval_email.json: unsupported_claims, grader_calibration_passed | – | The user requires accuracy over embellishment |
| C-2b-05 | Failure paths (Claude Code missing, timeout, invalid output) show a clear message and write nothing | pass/fail | automated | yes | `uv run pytest tests/test_claude_cli.py` | – | – |
| C-2b-06 | Five generated emails read as something the user would send | pass/fail | human | yes | phase 2b prompt, You check | – | – |

## Phase 2c: Programme research

| ID | Criterion | Threshold | Method | Gate | Verify | Baseline | Why |
|---|---|---|---|---|---|---|---|
| C-5-01 | Researching two programmes yields schema-valid files where every filled field has a source URL and the programme is marked unverified | pass/fail | automated | yes | `uv run python tasks.py live -k research` | – | – |
| C-2c-02 | Re-research writes nothing until the user saves; accepted deadline changes reset those deadlines to unverified; rejected changes leave the file byte-identical | pass/fail | automated | yes | `uv run pytest tests/test_research_merge.py tests/e2e/test_research.py -k us18` | – | – |

## Phase 3: Reminders, calendar and digest

| ID | Criterion | Threshold | Method | Gate | Verify | Baseline | Why |
|---|---|---|---|---|---|---|---|
| C-3-01 | The reminder engine reproduces the worked example and all 30 rows of the golden reminder table | pass/fail | automated | yes | `uv run pytest tests/test_reminders.py` | – | – |
| C-3-02 | Calendar sync is idempotent: two runs create no duplicates; a changed deadline updates events; resolved reminders are removed (against the fake Google API) | pass/fail | automated | yes | `uv run pytest tests/test_calendar_sync.py` | – | – |
| C-3-03 | The digest matches its golden file and can only be sent to the owner address in settings | pass/fail | automated | yes | `uv run pytest tests/test_digest.py` | – | – |
| C-3-04 | Live smoke test creates, updates and deletes one test event in the Applications calendar | pass/fail | automated | yes | `uv run python tasks.py live -k calendar` | – | – |
| C-3-05 | Reminders appear on the iPhone in Apple Calendar with alerts | pass/fail | human | yes | phase 3 prompt, You check | – | – |
| C-3-06 | No code path can send email to any address other than the owner's | pass/fail | automated | yes | `uv run pytest tests/test_no_outbound_email.py` | – | – |

## Phase 4a: Biography bank, essays, CVs and exact checks

| ID | Criterion | Threshold | Method | Gate | Verify | Baseline | Why |
|---|---|---|---|---|---|---|---|
| C-4a-01 | The wrong-school check catches all 20 planted mentions with no false positives on 10 clean essays | wrong_school_misses_plus_false_positives == 0 | automated | yes | `uv run pytest tests/test_wrong_school.py` | – | A competitor's name in an essay is the classic reuse error |
| C-4a-02 | Word count matches the rule in docs/02 §7 on all 15 fixtures | pass/fail | automated | yes | `uv run pytest tests/test_word_count.py` | – | – |
| C-4a-03 | Biography, essay, version and CV user stories pass end to end (US-11 to US-14) | pass/fail | automated | yes | `uv run pytest tests/e2e/test_essays.py tests/e2e/test_biography.py` | – | – |

## Phase 4b: AI essay and CV evaluator

| ID | Criterion | Threshold | Method | Gate | Verify | Baseline | Why |
|---|---|---|---|---|---|---|---|
| C-4b-01 | Every evaluation validates against its schema and rates all four rubric criteria with a reason | evaluation_schema_failures == 0 | automated | yes | `uv run python tasks.py eval-essay` → reports/eval_essay.json: schema_failures | – | The app reads the output by machine |
| C-4b-02 | Planted flaws detected in the essay eval set | planted_flaws_detected >= 8 of 10 | automated | yes | `uv run python tasks.py eval-essay` → reports/eval_essay.json: planted_flaws_detected | the exact checks alone catch only the word-limit and wrong-school flaws (2 of 10) | Feedback must find real problems, not only restate the draft |
| C-4b-03 | The evaluator never rewrites: no comment exceeds 60 words or contains a replacement paragraph | pass/fail | automated | yes | `uv run python tasks.py eval-essay` → reports/eval_essay.json: rewrite_violations | – | – |
| C-4b-04 | False alarms on clean essays (accuracy rated weak on a clean essay) | pass/fail | reported | no | `uv run python tasks.py eval-essay` → reports/eval_essay.json: clean_essay_false_alarms | – | – |
| C-4b-05 | Three evaluations of the user's own drafts are useful and fair | pass/fail | human | yes | phase 4b prompt, You check | – | – |

## Phase 5: Polish

| ID | Criterion | Threshold | Method | Gate | Verify | Baseline | Why |
|---|---|---|---|---|---|---|---|
| C-5-02 | The full end-to-end suite passes (US-1 to US-18) | pass/fail | automated | yes | `uv run python tasks.py test` | – | – |
| C-5-03 | Following README.md alone, the user sets up, syncs and uses the tool | pass/fail | human | yes | phase 5 prompt, You check | – | – |
