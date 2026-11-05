---
description: Write a mid-phase hand-off before ending a session
---

The session ends before the phase is finished. Do this, in order:

1. Stop at a clean point: finish or revert the change in progress, so the code runs.
2. Run the fast tests and the criteria guard; note the results.
3. In `docs/PROGRESS.md`, under **Hand-offs**, add a section for the current phase with the date:
   - **Done**: what is finished and tested (file names);
   - **Half-done**: what is started, its state, and the file where it is;
   - **Left**: the remaining tasks and criteria not yet passing;
   - **Open questions**: anything waiting for my decision;
   - **Next first step**: exactly what the next session should do first.
4. Log any decisions made this session in `docs/DECISIONS.md`.
5. Propose a commit message like `Phase N work in progress: <summary>`, and remind me to start the
   next session with `/resume <phase id>`.
