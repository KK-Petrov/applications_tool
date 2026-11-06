# CLAUDE.md: Application Hub

## Purpose
Application Hub is a single-user web app that runs locally on the owner's Windows laptop. It helps him
apply to US quantitative finance master's programmes (Princeton MFin, MIT Sloan MFin, Chicago Booth MiF,
Columbia MS Financial Economics, and any he adds). It tracks deadlines, requirements, documents and
interviews; manages recommenders in a sheet and drafts formal request emails; evaluates essays and CVs
against a fixed rubric; and pushes reminders to a Google Calendar that syncs to his iPhone.
The owner's real data lives in a data folder outside the repository. Tests and fixtures use invented data only.

## Sources of truth (read before working in an area)
- `docs/00_design_brief.md`: the frozen design and its decisions.
- `docs/01_data_contract.md`: data folder layout, every YAML/Markdown file and field, enums, IDs.
- `docs/02_domain_rules.md`: time zones, countdown, letter coverage, next actions, reminder rules (worked example), word count, wrong-school check.
- `docs/03_ui_spec.md`: pages, routes, user stories US-1 to US-14, components, states, design tokens.
- `docs/04_ai_features.md`: the Claude Code wrapper, the skills, their JSON schemas, eval sets and graders.
- `docs/05_integrations.md`: Google Calendar sync, Gmail digest, OAuth, Task Scheduler, iPhone setup.
- `config/project.yaml`: every tunable parameter. Never hard-code a parameter that belongs here.
- `seed/programmes/*.yaml`: the four starting programmes, copied into the data folder by `init-data`.
- `docs/PROGRESS.md` and `docs/DECISIONS.md`: build state and decisions.

If code and spec disagree, stop and ask.

## Stack & layout
- Python 3.12, managed with uv. FastAPI + Jinja2 templates + HTMX (vendored `static/htmx.min.js`, no CDN at runtime).
- Pydantic v2 models for every data file; ruamel.yaml for round-trip YAML (keeps the user's comments).
- Google: `google-api-python-client`, `google-auth-oauthlib`. Time zones: `zoneinfo` + `tzdata`.
- Tests: pytest, pytest-playwright, axe-playwright-python. Markers: `e2e`, `live` (real Google or Claude calls; never in fast tests).
- Layout: `src/hub/` (package: `app.py`, `models/`, `store.py`, `rules/`, `ai/`, `google/`, `cli.py`, `templates/`, `static/`),
  `tests/` (`fixtures/`, `fakes/`, `e2e/`, `evals/`), `.claude/skills/` (the AI skills), `seed/`, `config/`, `docs/`.
- Generated output goes to `reports/` (gitignored). Secrets and OAuth tokens go to `.secrets/` (gitignored).
- Data folder: `storage.data_dir` in `config/project.yaml`; the env var `HUB_DATA_DIR` overrides it (tests always set it to a temp folder; handy for a scratch copy). There is no `.env.example` (DECISIONS #17).

## Commands (tasks.py is the single entry point: `uv run python tasks.py <target>`)
`dev` (app at http://127.0.0.1:8765) · `init-data` · `test-fast` (< 60 s, no e2e, no live; parallel, warns above 45 s) · `test` (all but live; parallel, then `serial`-marked tests alone) ·
`e2e` · `live` (real Google/Claude; pass `-k`) · `eval-email` · `eval-essay` · `lint` (ruff) · `validate` (lint + test + criteria guard)
App CLI (installed by uv as `hub`): `hub serve` · `hub sync [--dry-run]` · `hub digest [--dry-run]` · `hub connect-google` ·
`hub email-payload <professor_id> [--programme <id>]` (prints the /rec-email input; used by "Refine in Claude Code").
Add new targets to tasks.py and keep this list up to date.

## Glossary
- **Programme**: one degree programme at one school (e.g. `princeton-mfin`). **Deadline**: local date-time + IANA zone + source.
- **Verified / unverified**: whether the user has confirmed a programme's data against its source page.
- **Display zone**: the time zone the UI shows times in (`America/New_York` or `Europe/Amsterdam`, chosen in the header).
- **Letter**: one professor × one programme, with a status (`not_asked` … `submitted`, `declined`). **Coverage**: confirmed or submitted letters ÷ letters required.
- **Next action**: the single most urgent thing to do for a professor, derived by the rules in docs/02 §4.
- **Reminder**: a computed calendar entry (rule × programme × subject); **sync map**: reminder ID → Google event ID.
- **Biography bank**: the owner's background entries; the only source of facts for emails and evaluations. **Anticipated**: an entry not yet achieved (e.g. cum laude).
- **Rubric**: Structure, Content fit, Programme fit, Accuracy; ratings strong / adequate / weak.
- **Skill**: a Claude Code skill in `.claude/skills/<name>/SKILL.md`, run by the app through `claude -p`.

## Non-negotiable rules
1. **Thresholds are never quietly lowered.** The acceptance criteria in `config/criteria.json` were fixed at the design freeze and are fingerprinted in `config/criteria.lock.json`. If a criterion is missed, report it with a diagnosis and a proposed fix. You may propose a change as a **Decision needed**; only the user can approve it, by logging it in `docs/DECISIONS.md` and relocking. Never edit `config/criteria.lock.json`.
2. **Tests aren't weakened to pass.** Never edit, skip or loosen a test to make it pass without saying so. If a test is wrong, explain why and get approval first.
3. **Spec and code must agree.** If they disagree, stop and ask. Don't silently pick one.
4. **Honest findings.** Report surprising or unflattering results plainly. Never tune data, parameters or config to make an expected result or demo story appear; every change must be defensible as realism or as a requirement.
5. **Parameters live in config.** Every tunable value is in `config/project.yaml`. Never hard-code one.
6. **Decisions are logged.** Every deviation from or refinement of the specs gets a row in `docs/DECISIONS.md` (date, phase, decision, reason, spec affected), including fixes to earlier phases.
7. **No secrets in code.** Configuration comes from `.env` (never committed; `.env.example` is). Never read `.env` into the conversation.
8. **Fix at the source.** When shared code causes a problem, fix it there instead of working around it locally.
9. **Never send email to anyone but the owner.** Request emails are copy-paste only; the only outbound email is the digest to `settings.owner_email`.
10. **Deadlines carry their time zone and source.** Every deadline is stored as a local time plus an IANA time zone plus a source URL; no code path drops either.
11. **Unverified stays visible.** Researched or third-party data is `unverified` until the user clicks Mark verified; never set `verified` in code or skills.
12. **Real data lives outside the repository.** Read and write user data only through the configured `data_dir`; fixtures and tests use invented people at example.com.
13. **Exact checks in code, judgement in the AI.** Word counts, wrong-school detection, deadlines and dates are computed in Python, never asked of the model.
14. **The AI comments, never rewrites.** Essay and CV feedback points at problems; it never produces replacement text.
15. **No invented facts in generated text.** Emails and feedback may only use facts present in the biography bank, the professor record or the programme data.
16. **Secrets stay unread.** Never open, print or copy `.secrets/` (OAuth client and token files) or paste their contents into the conversation; code reads them only at runtime through `config.google` paths.

## Working agreements
- **Plan first.** Every session starts with a plan (`/phase` or `/resume`): steps, files, how each
  acceptance criterion will be tested, risks, and numbered open questions, each with your recommended
  answer and a one-line reason. Wait for approval before editing anything.
- **Tests first.** After approval, write the tests for this phase's automated criteria before the
  implementation, and show that they fail for the right reason. Then implement in small steps,
  running `uv run python tasks.py test-fast` often. Hooks lint edited files and, at the end of each turn, run the
  criteria guard and the fast tests; fix what they report.
- **One phase per session.** Don't start the next phase unless asked. If the phase won't finish in
  this session (context around 70%), say so and run `/handoff`.
- **Decision needed.** When a spec gap, a contradiction or a failing criterion needs the user, stop and
  write a block headed **Decision needed** with: the problem in two sentences, the evidence (numbers),
  the options in order of preference with their consequences, and your recommendation.
- **End of phase.** Run every acceptance check and show the result per criterion ID; run the criteria
  guard and the reviewer(s) named in the phase prompt; list the "You check" items for the user; update
  `docs/PROGRESS.md` (status, actual sessions, key results, known issues) and `docs/DECISIONS.md`;
  propose a commit message. Don't commit or push.
- **Reviewers.** Run `ui-reviewer` after changing pages, templates or styles; run `data-integrity-reviewer` after changing schemas, deadlines, time zones, reminders, sync, statuses or privacy handling; run `ai-output-reviewer` after changing a skill, prompt, schema, eval set or grader.
- **Code style.** Prefer boring, readable code. Type hints/types everywhere. Docstrings explain the
  domain meaning, not just the mechanics.
