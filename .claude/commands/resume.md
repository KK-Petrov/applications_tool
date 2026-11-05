---
description: Resume a phase from the hand-off in docs/PROGRESS.md (plans only the remaining work)
argument-hint: <phase id>
---

We are resuming **Phase $ARGUMENTS** in a new session.

1. Read `CLAUDE.md`, `prompts/phase-$ARGUMENTS-*.md`, `docs/PROGRESS.md` (especially the latest
   hand-off for this phase), `docs/DECISIONS.md` and `docs/acceptance_criteria.md`.
2. **Verify the hand-off against the code**; don't trust it blindly. Check `git status` and `git log -5`,
   run the fast tests, and confirm which items marked done really are. Report any difference.
3. Add 1 to the phase's actual sessions in `docs/PROGRESS.md`, then recalibrate as `/phase` step 4 does.
4. Present a plan for the **remaining work only** (same contents as `/phase` step 5) and wait for approval.
5. After approval, continue. Finish with the end-of-phase routine from `.claude/commands/phase.md`
   step 7, or with `/handoff` if the phase still won't finish in this session.
