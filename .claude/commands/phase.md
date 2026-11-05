---
description: Start a build phase (plan first, tests first, build only after approval)
argument-hint: <phase id, e.g. 0 or 2a>
---

We are starting **Phase $ARGUMENTS**.

1. Read `CLAUDE.md`, `docs/PROGRESS.md`, `docs/DECISIONS.md` and `docs/acceptance_criteria.md`.
2. Read `prompts/phase-$ARGUMENTS-*.md` and every document it lists under "Read first".
3. Check `docs/PROGRESS.md`:
   - every earlier phase must be **Done** or **Done with known issues**; if not, stop and say what is missing;
   - if this phase is already **In progress**, stop and tell me to use `/resume $ARGUMENTS` instead.
4. **Recalibrate.** For finished phases, compute actual ÷ estimated sessions (upper bound of the range).
   If the ratio is above 1.3, tell me the updated estimate for this phase and the remaining ones.
5. Present a **plan** and wait for my approval. Don't edit any file before I approve. The plan contains:
   - the ordered implementation steps and the files you will create or change;
   - a **criterion → test map**: every criterion ID of this phase, the test or script that checks it, and the command;
   - risks, and numbered open questions, each with your recommended answer and a one-line reason;
   - whether the work fits the recommended sessions; if not, where you propose to hand off.
6. After approval:
   - set the phase to **In progress** in `docs/PROGRESS.md` and add 1 to its actual sessions;
   - write the tests for the automated criteria **first** and run them: show they fail for the right reason;
   - implement in small steps, running the fast tests as you go.
7. At the end of the phase:
   - run every acceptance check and show a table: criterion ID | result | numbers | PASS / FAIL / REPORTED;
   - run `python .claude/hooks/criteria_guard.py`;
   - run the reviewer subagent(s) named in the prompt;
   - list the "You check" items for me and wait for my confirmation before marking the phase Done;
   - update `docs/PROGRESS.md` (status, actual sessions, key results, known issues) and `docs/DECISIONS.md`;
   - propose a commit message.

   Don't start the next phase.
