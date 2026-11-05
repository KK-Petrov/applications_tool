# Supervisor handover: Application Hub

This file briefs a Claude chat (a claude.ai Project) that supervises the Claude Code build of this
project. Upload it to the Project together with `docs/PROGRESS.md`, `docs/DECISIONS.md` and
`docs/00_design_brief.md`, and paste `PROJECT_INSTRUCTIONS.md` into the Project instructions.
Update it when the design or the working style changes; PROGRESS and DECISIONS carry the build state.

## 1. The supervisor's role and rules
1. **Review item by item.** For every plan, question, "Decision needed" or acceptance report from Claude
   Code, take each item in turn, recommend a decision, and give a short reason.
2. **End with one paste-ready reply** for Claude Code, in a single block, containing every decision.
3. **Never let acceptance thresholds be quietly lowered.** A missed threshold gets a diagnosis and a
   fix, not a relaxed check. Changes need the user's explicit agreement and a DECISIONS.md row, and the
   user runs the relock (`python .claude/hooks/criteria_guard.py --relock --decision N`) themselves.
4. **Guard the method:**
   - prefer fixing at the source when a module is reused elsewhere;
   - don't design the result: changes must be defensible as realism or as a requirement, never made
     to produce a demo story; report honest findings;
   - keep any ground truth isolated from the code that must recover it;
   - no in-sample evaluation;
   - baselines stay realistic: not a straw man, not near-optimal;
   - watch for circular definitions and double counting.
5. **Session management.** About one Claude Code session per phase; mid-phase hand-offs go through
   `/handoff` and `/resume`. Recommend stopping at about 70% context. Check that the session estimates
   are recalibrated when phases run long.
6. **Correct yourself openly** when new facts change a recommendation.

**Response style:** Direct, confident and specific; recommendation first; accuracy over embellishment; formal register for anything sent to professors.

## 2. User and environment
- **OS / editor:** Windows (native); vscode.
- **Languages:** python.
- **Key commands:** setup `uv sync && uv run playwright install chromium`, fast tests `uv run python tasks.py test-fast`, full tests `uv run python tasks.py test`.
- **Session practice:** one phase per conversation, plan mode first, `/handoff` + `/resume` mid-phase, the user commits.
- Windows native with VS Code; uv for Python
- Claude Code runs on the user's Claude subscription

## 3. The project
A local web app that tracks US master's applications (deadlines, requirements, documents, interviews), manages recommenders in a sheet and drafts request emails, evaluates essays and CVs, and pushes reminders to a Google Calendar that syncs to the iPhone.

- **Classification:** primary Interactive app; secondary AI-powered app, Service and integration; traits external systems, sensitive data; maturity prototype
- **Design:** see `00_design_brief.md` (core logic, decisions, limitations).
- **Reviewers in the kit:** ui-reviewer, data-integrity-reviewer, ai-output-reviewer

## 4. Phase plan and recommendations
| Phase | Builds | Sessions | Effort | Reviewer |
|---|---|---|---|---|
| 0 Scaffold | Repo layout, tasks.py, data schemas and loader, seed programmes, app shell with sidebar, test harness | 1 | medium | – |
| 1 Programmes and dashboard | Dashboard, programme page (requirements, steps, documents, interviews, curriculum), add/edit programme, time zones, verification | 1–2 | medium | ui-reviewer, data-integrity-reviewer |
| 2a Recommender sheet | Sheet with per-programme status cells, filters, Add professor page, detail panel | 1 | medium | ui-reviewer, data-integrity-reviewer |
| 2b Recommendation email generator | Claude Code wrapper, /rec-email skill, email eval set and graders, Generate button | 1–2 | high | ai-output-reviewer |
| 3 Reminders, calendar and digest | Reminder engine, Google Calendar sync, Gmail digest to yourself, Task Scheduler setup | 1–2 | high | data-integrity-reviewer |
| 4a Biography bank, essays, CVs and exact checks | Biography bank, essay and CV versions, word count, wrong-school check | 1 | medium | ui-reviewer, data-integrity-reviewer |
| 4b AI essay and CV evaluator | /evaluate skill, rubric output, essay eval set, Evaluate button | 1–2 | high | ai-output-reviewer |
| 5 Programme research and polish | /research-programme skill, Research button, README, final end-to-end pass | 1 | medium | ui-reviewer, ai-output-reviewer |

## 5. Acceptance criteria
| ID | Phase | Criterion | Threshold | Gate |
|---|---|---|---|---|
| C-0-01 | 0 | From a fresh clone on Windows, setup and the full test suite pass | pass/fail | yes |
| C-0-02 | 0 | Fast tests finish quickly | test_fast_seconds < 60 s | yes |
| C-0-03 | 0 | No personal data or secrets in the repository; the data folder and token files are outside Git | pass/fail | yes |
| C-0-04 | 0 | Every seed file validates against the schema; an invalid file produces an error naming the file and field | pass/fail | yes |
| C-1-01 | 1 | No deadline is displayed without its time zone and a source link; unverified data is always visibly marked | pass/fail | yes |
| C-1-02 | 1 | Time-zone conversion is correct on the 20 reference cases, including daylight-saving changeovers | tz_cases_failed == 0 | yes |
| C-1-03 | 1 | Days-left values match docs/02 §2 on boundary cases | pass/fail | yes |
| C-1-04 | 1 | Programme user stories pass end to end (US-1 to US-5 in docs/03 §2) | pass/fail | yes |
| C-1-05 | 1 | No serious or critical accessibility violations on any page | axe_serious_violations == 0 | yes |
| C-1-06 | 1 | Dashboard and programme page match the agreed mockup and read clearly | pass/fail | yes |
| C-2a-01 | 2a | Recommender user stories pass end to end (US-6 to US-9), including a 3,000-character pasted course description saved verbatim | pass/fail | yes |
| C-2a-02 | 2a | Letter coverage counts and next actions match docs/02 §4 on the fixture set | pass/fail | yes |
| C-2a-03 | 2a | The sheet stays fast with many professors | sheet_render_ms_40_professors < 1000 ms | yes |
| C-2b-01 | 2b | Every generated email validates against its JSON schema | email_schema_failures == 0 | yes |
| C-2b-02 | 2b | Every eval email names the right professor and course, lists each selected programme with its exact stored deadline, and mentions no unselected programme | email_fact_failures == 0 | yes |
| C-2b-03 | 2b | Eval emails are 180–280 words, open with 'Dear Professor {surname},' and never state an anticipated achievement as achieved | email_form_failures == 0 | yes |
| C-2b-04 | 2b | No unsupported claims in eval emails, and the claim grader flags all 3 planted calibration emails | unsupported_claims == 0 | yes |
| C-2b-05 | 2b | Failure paths (Claude Code missing, timeout, invalid output) show a clear message and write nothing | pass/fail | yes |
| C-2b-06 | 2b | Five generated emails read as something the user would send | pass/fail | yes |
| C-3-01 | 3 | The reminder engine reproduces the worked example and all 30 rows of the golden reminder table | pass/fail | yes |
| C-3-02 | 3 | Calendar sync is idempotent: two runs create no duplicates; a changed deadline updates events; resolved reminders are removed (against the fake Google API) | pass/fail | yes |
| C-3-03 | 3 | The digest matches its golden file and can only be sent to the owner address in settings | pass/fail | yes |
| C-3-04 | 3 | Live smoke test creates, updates and deletes one test event in the Applications calendar | pass/fail | yes |
| C-3-05 | 3 | Reminders appear on the iPhone in Apple Calendar with alerts | pass/fail | yes |
| C-3-06 | 3 | No code path can send email to any address other than the owner's | pass/fail | yes |
| C-4a-01 | 4a | The wrong-school check catches all 20 planted mentions with no false positives on 10 clean essays | wrong_school_misses_plus_false_positives == 0 | yes |
| C-4a-02 | 4a | Word count matches the rule in docs/02 §7 on all 15 fixtures | pass/fail | yes |
| C-4a-03 | 4a | Biography, essay, version and CV user stories pass end to end (US-11 to US-14) | pass/fail | yes |
| C-4b-01 | 4b | Every evaluation validates against its schema and rates all four rubric criteria with a reason | evaluation_schema_failures == 0 | yes |
| C-4b-02 | 4b | Planted flaws detected in the essay eval set | planted_flaws_detected >= 8 of 10 | yes |
| C-4b-03 | 4b | The evaluator never rewrites: no comment exceeds 60 words or contains a replacement paragraph | pass/fail | yes |
| C-4b-04 | 4b | False alarms on clean essays (accuracy rated weak on a clean essay) | pass/fail | no |
| C-4b-05 | 4b | Three evaluations of the user's own drafts are useful and fair | pass/fail | yes |
| C-5-01 | 5 | Researching two programmes yields schema-valid files where every filled field has a source URL and the programme is marked unverified | pass/fail | yes |
| C-5-02 | 5 | The full end-to-end suite passes (US-1 to US-17) | pass/fail | yes |
| C-5-03 | 5 | Following README.md alone, the user sets up, syncs and uses the tool | pass/fail | yes |

## 6. Decisions at the freeze
See `DECISIONS.md`; the build adds rows from #9 onwards.

## 7. What to ask the user for
When the user brings Claude Code output, it should include the plan or report text (or a screenshot),
and the current PROGRESS.md and DECISIONS.md if they changed. If something needed is missing, ask for it.
