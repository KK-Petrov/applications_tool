# Design brief: Application Hub

Frozen on 30 October 2026. Every later change is a row in `docs/DECISIONS.md`.
Items marked *(recommended)* were defaults accepted by the user; *(user)* were the user's own choice.

## 1. What and why
- **What is built:** a local web app on the owner's Windows laptop for US master's applications. It tracks
  programmes (deadlines, requirements, application steps, documents, interviews, curriculum), manages
  recommenders in a sheet and drafts formal request emails, evaluates essays and CVs against a fixed
  rubric, and pushes reminders to a Google Calendar that syncs to his iPhone, plus a daily digest email.
- **For whom:** one user, the owner (a finance student applying to quantitative finance master's programmes).
- **What it must do:** never miss or misstate a deadline; make the status of many recommenders readable
  at a glance; produce request emails he would send with light edits; give essay feedback that finds
  real problems without rewriting his voice.
- **Maturity:** personal prototype *(recommended)*: runs locally with one command, no deployment. It
  uses real personal data, so the sensitive-data rules apply.

## 2. Classification
- **Primary type:** Interactive app. **Secondary:** AI-powered app (emails, evaluation, research);
  Service and integration (Google Calendar, Gmail).
- **Traits:** external systems (Google APIs, Claude Code CLI); sensitive data (the owner's biography,
  professors' contact details).

## 3. Environment and way of working
- Windows native, VS Code, Python 3.12 with uv; local Git, no GitHub *(user)*.
- Task runner: `uv run python tasks.py <target>`.
- One phase per Claude Code session; plan mode first; the user commits.
- Deadline pressure: the first programme deadline is 15 December 2026 (Princeton, unverified). The
  owner sends the first recommendation requests by hand; the tool must be useful by mid-November.

## 4. Core logic
### 4.1 Reminder rules (full table in docs/02 §5)
For each programme with deadline D (local date-time + school time zone):
letter requests due D − 42 days; letter follow-up request date + 7 days if not confirmed; submission
check D − 14 days if not all submitted; documents due D − 21 days if any not sent; final essay
evaluation D − 7 days; deadline alerts D − 14, 7, 2, 1 days; the deadline itself; interviews and
assessments, plus one reminder the day before.

### 4.2 Worked example
Princeton MFin, deadline 15 Dec 2026, 23:59 `America/New_York` (EST, UTC−5).
- In UTC: 16 Dec 2026, 04:59. In Rotterdam (`Europe/Amsterdam`, CET): **16 Dec 2026, 05:59**.
- On 30 Oct 2026 at 12:00 New York time, days left = floor(46 d 11 h 59 min) = **46**.
- Letter requests due **3 Nov 2026**; documents due 24 Nov; submission check 1 Dec; final essay check 8 Dec;
  deadline alerts 1, 8, 13 and 14 Dec.
- A professor asked on 2 Nov who has not confirmed by 9 Nov triggers a follow-up reminder on **9 Nov**.

### 4.3 AI division of labour
Exact things (dates, counts, word counts, wrong-school detection, deadlines in emails) are computed in
Python and passed to the model as facts. The model writes emails and judges essays; its output is
checked against those facts.

## 5. Key decisions
| # | Decision | Reason | Source |
|---|---|---|---|
| 1 | Windows native, VS Code, Python 3.12, uv | Matches the user's laptop; no WSL needed | user/recommended |
| 2 | FastAPI + Jinja2 + HTMX, no JS framework | One language; simple single-user tool | recommended |
| 3 | Data as YAML/Markdown in a configurable `data_dir` (OneDrive folder recommended) | Readable by user and Claude Code; OneDrive gives backup | recommended |
| 4 | AI through Claude Code (`claude -p` + project skills) on the user's subscription | Single user; no API key; same skills work interactively | user |
| 5 | Reminders in a dedicated Google Calendar "Applications", added on the iPhone through the Google account | Reliable sync with alerts | recommended |
| 6 | Daily digest by Gmail API to the owner's own address via Task Scheduler at login | Works without a server | user |
| 7 | Request emails are copy-paste only; nothing is sent to professors | Safety; user reviews every email | user |
| 8 | Deadlines stored with the school's time zone; display in ET or Rotterdam | The user moves between the US and the Netherlands | user |
| 9 | Programme data researched by a skill and marked unverified until confirmed | Deadlines are too costly to trust blindly | recommended |
| 10 | Essays and CVs get rubric feedback, never rewrites; word count and wrong-school check in code | Keeps the user's voice; exact checks must be exact | recommended |
| 11 | Recommenders shown as a sheet (rows = professors, columns = programmes), statuses changed in the cell; email addresses stored but not shown in the sheet | Many professors, quick glance | user |
| 12 | Course description pasted verbatim when adding a professor | Context for the email generator | user |
| 13 | In scope: documents tracking, wrong-school check, time zones, interviews/assessments, CV versions | User selection | user |
| 14 | Out of scope: post-decision tracking, costs/scholarships, visa steps, events log, automatic backups, Berkeley Haas (addable later) | User selection | user |
| 15 | Local Git only | User choice | user |

## 6. Acceptance criteria (summary)
Full list in `docs/acceptance_criteria.md`. The ones that define success:
- C-1-01 / C-1-02: no deadline without zone and source; 20/20 time-zone reference cases.
- C-2a-01: the recommender sheet stories pass, pasted descriptions saved verbatim.
- C-2b-02 / C-2b-04: emails carry the exact stored deadlines and no unsupported claims.
- C-3-01 / C-3-02 / C-3-05: reminder engine matches the golden table; sync is idempotent; alerts on the iPhone.
- C-4a-01: wrong-school check 20/20 with no false positives.
- C-4b-02: evaluator finds ≥ 8 of 10 planted flaws.
- C-0-03 / C-3-06: no personal data in the repo; email only ever to the owner.

## 7. Phase plan
| Phase | Builds | Sessions | Effort | Reviewer |
|---|---|---|---|---|
| 0 | Scaffold, schemas, seed programmes, app shell | 1 | medium | – |
| 1 | Dashboard, programme page, add/edit programme, time zones | 1–2 | medium | ui, data-integrity |
| 2a | Recommender sheet, Add professor | 1 | medium | ui, data-integrity |
| 2b | Email generator (`/rec-email`) with eval set | 1–2 | high | ai-output |
| 3 | Reminder engine, Calendar sync, digest, Task Scheduler | 1–2 | high | data-integrity |
| 4a | Biography bank, essay/CV versions, exact checks | 1 | medium | ui, data-integrity |
| 4b | AI evaluator (`/evaluate`) with eval set | 1–2 | high | ai-output |
| 5 | `/research-programme`, polish, README | 1 | medium | ui, ai-output |

Whole build: 8–13 sessions. First visible result: session 2 (Phase 1).

## 8. Known limitations and simplifications
- Sync and digest only run while the laptop is on; events already on the phone keep their alerts.
- Programme data relies on the user's verification; admissions portals are not read.
- AI feedback is diagnostic, not a prediction of admissions outcomes.
- AI calls from buttons use the subscription's Agent SDK credit.
- Essays and CVs are pasted text; file import is out of scope.

## 9. External facts verified
- Booth MiF Round One deadline 7 Jan 2027 — chicagobooth.edu specialised master's announcement, checked 30 Oct 2026.
- MIT Sloan MFin deadline 5 Jan 2027, fee $150, resume + essay + short essay — oge.mit.edu, checked 30 Oct 2026.
- Princeton MFin: Fall 2027 applications opened 15 Sep 2026; last cycle's deadline 15 Dec; three letters (two academic without full-time work experience); math assessment on the first Saturday after the deadline last cycle — bcf.princeton.edu, checked 30 Oct 2026. This cycle's deadline is **unconfirmed**.
- Columbia MSFE deadline 15 Jan 2027, two letters, two essays — third-party site (applybuds.com) only, **unverified**.
- `claude -p` expands user-invoked skills named in the prompt; `--output-format json` with `--json-schema` returns `structured_output`; subscription headless usage draws on a separate Agent SDK credit since 15 Jun 2026 — code.claude.com headless docs, checked 30 Oct 2026.
- Google per-event alerts reaching iOS and the narrow `calendar.app.created` scope are to be verified in Phase 3 (docs/05 §1).
