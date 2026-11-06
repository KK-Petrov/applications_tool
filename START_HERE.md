# Application Hub: Start Here

A local web app that tracks US master's applications (deadlines, requirements, documents, interviews), manages recommenders in a sheet and drafts request emails, evaluates essays and CVs, and pushes reminders to a Google Calendar that syncs to the iPhone.

This folder is a build kit for Claude Code. It contains the project memory (`CLAUDE.md`), the design
brief and specifications (`docs/`), the acceptance criteria (`config/criteria.json`, readable in
`docs/acceptance_criteria.md`), one prompt per build phase (`prompts/`) and the Claude Code setup
(`.claude/`: reviewer subagents, commands, hooks and permissions). Nothing is built yet: Claude Code
builds it phase by phase, and you approve every plan.

## Prerequisites
- Claude Code, installed and signed in with your Claude subscription (`claude` works in a terminal)
- Python 3.12 and uv (`uv --version` works)
- Git for Windows
- Python 3 on PATH as `python` (for the hooks)
- A Google account that is also added to the iPhone (Settings → Calendar → Accounts); needed from Phase 3

## Environment: Windows (native)
The kit runs on native Windows. Install **Git for Windows** (Claude Code uses its Git Bash to run commands and hooks) and make sure `python` is on your PATH. The hooks are Python scripts and run with `python`.
Project commands: setup `uv sync && uv run playwright install chromium`, fast tests `uv run python tasks.py test-fast` (full list in CLAUDE.md).

## Using VS Code
The Claude Code extension and the CLI share the same project files (`CLAUDE.md`, `.claude/settings.json` with hooks and permissions, `.claude/agents/`, `.claude/commands/`).
- **Plan mode:** pick **Plan** in the mode indicator under the prompt box. VS Code opens the plan as a document you can comment on before approving.
- **One phase per conversation:** start a new conversation for each phase instead of `/clear`.
- **Commands:** type `/` to check that `phase`, `resume`, `handoff`, `validate` and `review` appear.
- **Undo a wrong turn:** use checkpoints (rewind) and the inline diffs.

## Step by step
1. **Unpack** the kit into an empty folder and commit it: `git init && git add -A && git commit -m "Build kit"`.
2. **Check the parameters** in `config/project.yaml`. Defaults are sensible; change them before Phase 1
   if the result should mirror your real situation.
3. **Start Claude Code** in the folder (`claude` in a terminal, or the Claude Code panel in VS Code).
4. **Switch to plan mode** (Shift+Tab in the terminal, or the mode indicator in VS Code) and type
   `/phase 0`. Claude reads the prompt and the specs and presents a plan. Answer its open questions,
   then approve.
5. **At the end of each phase:** check the acceptance results, do the "You check" items in the phase
   prompt, run `/validate` if you want a second opinion, then commit with the proposed message.
6. **Start a new conversation for the next phase** and type `/phase <next id>`.

**Stopping mid-phase:** type `/handoff`, commit, and in the next session type `/resume <phase id>`
(not `/phase`, which starts a fresh plan).

## Commands
| Command | What it does |
|---|---|
| `/phase <id>` | Starts a phase: plan → your approval → tests first → build → acceptance |
| `/resume <id>` | Continues a phase from the hand-off in `docs/PROGRESS.md` |
| `/handoff` | Writes a mid-phase hand-off (done / half-done / left) before you stop |
| `/validate` | Runs all tests, the criteria guard and the relevant reviewers; summarises readiness |
| `/review <name>` | Runs one reviewer subagent |

If a command doesn't appear when you type `/` (the VS Code extension supports a subset), type
"Follow `.claude/commands/phase.md` for phase 0" instead, or turn on the extension's Use Terminal setting.

## Phase map and recommendations
| Phase | Builds | You see at the end | Sessions | Effort | Reviewer |
|---|---|---|---|---|---|
| 0 Scaffold | Repo layout, tasks.py, data schemas and loader, seed programmes, app shell with sidebar, test harness | `uv run python tasks.py dev` opens an empty Application Hub with the sidebar | 1 | medium | – |
| 1 Programmes and dashboard | Dashboard, programme page (requirements, steps, documents, interviews, curriculum), add/edit programme, time zones, verification | Your four programmes with deadlines in ET or Rotterdam time | 1–2 | medium | ui-reviewer, data-integrity-reviewer |
| 2a Recommender sheet | Sheet with per-programme status cells, filters, Add professor page, detail panel | All professors at a glance; statuses change with one click | 1 | medium | ui-reviewer, data-integrity-reviewer |
| 2b Recommendation email generator | Claude Code wrapper, /rec-email skill, email eval set and graders, Generate button | A tailored, formal request email per professor | 1–2 | high | ai-output-reviewer |
| 2c Programme research and course import | /research-programme skill (official sources only), Research a programme, Re-research with field-by-field accept/reject; /extract-courses (transcript → courses.yaml, grades checked, identifiers stripped), professors created from the transcript, course suggestions on Add professor | A new programme added in under five minutes; your courses imported from your transcript; professors created with their courses; stored programmes refreshed safely | 2 | high | ai-output-reviewer, ui-reviewer, data-integrity-reviewer |
| 3 Reminders, calendar and digest | Reminder engine, Google Calendar sync, Gmail digest to yourself, Task Scheduler setup | Reminders on your iPhone; a daily digest email | 1–2 | high | data-integrity-reviewer |
| 4a Biography bank, essays, CVs and exact checks | Biography bank, essay and CV versions, word count, wrong-school check | Drafts stored by version with exact checks | 1 | medium | ui-reviewer, data-integrity-reviewer |
| 4b AI essay and CV evaluator | /evaluate skill, rubric output, essay eval set, Evaluate button | Rubric feedback on a draft | 1–2 | high | ai-output-reviewer |
| 5 Polish | Usage panel, README, final end-to-end pass, clearer unreadable-file page | Set up and use the tool from the README alone | 1 | medium | ui-reviewer, ai-output-reviewer |

**Whole build:** about 8–12 sessions. You first see something visual or usable at the end of Phase 1 (Programmes and dashboard), around session 2–3.

Session estimates are recalibrated during the build: `/phase` and `/resume` compare actual with
estimated sessions in `docs/PROGRESS.md` and update the remaining estimates when phases run long.

## Model and effort
Checked 30 Oct 2026: Claude Code offers effort levels from low to max. Use high effort for Phases 2b, 3 and 4b and for every planning and acceptance step; medium for the rest. Switch to a stronger model only if a phase keeps failing for reasoning reasons.

## Session practice
- Start every session in **plan mode**; read the plan before approving. Most corrections are cheapest there.
- **One phase per conversation.** Stop at about **70% context** or when the phase is done.
- **You commit.** Claude proposes commit messages; it can't push (blocked in `.claude/settings.json`).
- **Don't fix an earlier phase silently.** If Claude finds a problem from an earlier phase, it logs the fix in `docs/DECISIONS.md`.
- **Keep the machine awake** during long runs; a sleeping laptop pauses everything.

## Acceptance thresholds are fixed
The criteria in `config/criteria.json` were agreed at the design freeze and are fingerprinted in
`config/criteria.lock.json`. The criteria guard runs at the end of every Claude turn: if a criterion
is changed, added or removed, it fails. Claude can't edit the lock file. To approve a change:
1. Make sure `docs/DECISIONS.md` has a row (say #12) that names every changed criterion ID and the reason.
2. Run it yourself: `python .claude/hooks/criteria_guard.py --relock --decision 12`.

## How to steer Claude well
- **If Claude drifts, point it at the spec**: "Re-read docs/01 section 3; ..."
- **Record real decisions**: say "log this in DECISIONS.md" so later phases inherit it.
- **Ask for the recommendation** if a question comes without one.

## What this proves, and what it doesn't
- Calendar sync and the digest only run while the laptop is on; events already on the phone keep their alerts.
- Programme data is only as reliable as the user's verification against the source; admissions portals are not read.
- AI feedback is diagnostic and not a prediction of what an admissions committee will think.
- Button-triggered AI use draws on the subscription's separate Agent SDK credit.
- CVs and essays are handled as pasted text; .docx and PDF import is out of scope.

## Supervisor pack (optional)
`supervisor/` lets a claude.ai Project review Claude Code's plans and reports. Create a Project, upload `supervisor/SUPERVISOR_HANDOVER.md`, `docs/00_design_brief.md`, `docs/PROGRESS.md` and `docs/DECISIONS.md` to its knowledge, and paste `supervisor/PROJECT_INSTRUCTIONS.md` into its instructions. Bring it every "Decision needed", every plan of a large phase and every acceptance report; re-upload PROGRESS and DECISIONS when they change.

