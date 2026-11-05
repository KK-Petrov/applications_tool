# Decision log

Record every decision that deviates from or refines the specs. Newest first. A change to an acceptance criterion must name the criterion ID(s) in its row; the user then relocks with `criteria_guard.py --relock --decision <#>`.

| # | Date | Phase | Decision | Reason | Spec affected |
|---|---|---|---|---|---|
| 8 | 2026-10-30 | freeze | Design frozen; 36 acceptance criteria locked in config/criteria.lock.json | Thresholds change only with the user's approval and a logged decision | config/criteria.json |
| 7 | 2026-10-30 | design | Berkeley Haas out of scope for now; addable through Add programme | User choice | 00 §5 |
| 6 | 2026-10-30 | design | Local Git only; no GitHub | User choice; Git gives per-phase checkpoints | 00 §5 |
| 5 | 2026-10-30 | design | Daily digest by Gmail API to the owner's own address, run by Windows Task Scheduler at login | User choice; works without a server | 05 §4 |
| 4 | 2026-10-30 | design | Reminders go to a dedicated Google Calendar 'Applications', reached on the iPhone through the Google account in iOS settings | Reliable sync with alerts; subscriptions refresh slowly and may drop alerts | 05 §2 |
| 3 | 2026-10-30 | design | AI features call Claude Code (`claude -p`) with project skills on the user's subscription, not the API | Single user; no API key; the same skills work interactively | 04 §1 |
| 2 | 2026-10-30 | design | Data stored as YAML and Markdown files in a configurable data folder (OneDrive recommended) | Readable and editable by the user and by Claude Code; OneDrive gives backup | 01 §1 |
| 1 | 2026-10-30 | design | Python FastAPI with server-rendered Jinja templates and HTMX; no JavaScript framework | One language, simple on Windows, enough for a single-user tool | 03 §1 |
