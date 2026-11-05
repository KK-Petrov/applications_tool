---
description: Run all tests, the criteria guard and the relevant reviewers; summarise readiness
---

1. Run the full test suite (`uv run python tasks.py test`). Then run `uv run python tasks.py validate`.
2. Run the criteria guard: `python .claude/hooks/criteria_guard.py`.
3. Decide which reviewers apply. Each file in `.claude/agents/` says in its description when to use it;
   run every reviewer whose area exists in the repo, as subagents (in parallel if possible).
4. Summarise in one table: area | verdict | key numbers | top issue.
5. Finish with what must be fixed before the current phase can be marked Done, by criterion ID.
   Don't fix anything unless I ask.
