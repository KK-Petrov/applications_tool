---
description: Run one reviewer subagent (or list them)
argument-hint: <reviewer name, or empty to list>
---

If no name is given (`$ARGUMENTS` is empty), list the reviewers in `.claude/agents/` with one line each
on when to use them, and stop.

Otherwise run the **$ARGUMENTS** subagent. If it reviews a user interface and a screenshot script exists
(see the reviewer's instructions), run that first. Report its findings ordered by impact, with the
criterion IDs they affect. Don't change code until I approve the fixes.
