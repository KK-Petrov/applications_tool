# 05: Integrations (Google Calendar, Gmail digest, Task Scheduler)

## 1. Google OAuth (one-time setup by the user, guided by README and `hub connect-google`)
1. In Google Cloud Console create a project "Application Hub"; enable **Google Calendar API** and **Gmail API**.
2. OAuth consent screen: External, add the user's own address as a test user, then set publishing status
   to **In production**. Reason: refresh tokens of apps left in "Testing" expire after 7 days. The user
   will see an "unverified app" warning on first sign-in, which is expected for a personal app.
   *Verified 2026-11-08 (DECISIONS #106): an External app in Testing gets refresh tokens that expire in 7 days
   unless it asks only for name/email/profile scopes (https://developers.google.com/identity/protocols/oauth2),
   so "In production" stays.*
3. Create an OAuth client of type **Desktop app**; save the JSON as `.secrets/client_secret.json`.
4. `hub connect-google` runs the installed-app flow (`google_auth_oauthlib.flow.InstalledAppFlow.run_local_server`),
   stores `.secrets/token.json`, sets `settings.google_connected: true`.
- Scopes (from `config.google.scopes`): `https://www.googleapis.com/auth/calendar.app.created` (create and
  manage only calendars the app created) and `https://www.googleapis.com/auth/gmail.send`.
  *Verified 2026-11-08 (DECISIONS #106): `calendar.app.created` = "Make secondary Google calendars, and see,
  create, change, and delete events on them" (https://developers.google.com/workspace/calendar/api/auth), so
  the scopes stay.* `hub connect-google` without `.secrets/client_secret.json` says what to create; a refused
  refresh asks to run it again.
- `.secrets/` is gitignored and never read into a Claude conversation; tests never touch it.

## 2. Calendar sync (`src/hub/google/calendar_sync.py`, CLI `hub sync [--dry-run]`)
### 2.1 Calendar
If `settings.calendar_id` is empty: create a secondary calendar `config.google.calendar_name`
("Applications") with `timeZone = settings.reminder_tz`; store its ID. Never write to any other calendar.
### 2.2 Event body (from a Reminder, docs/02 §5)
`summary`, `description`, `start`/`end` (`dateTime` + `timeZone`), `reminders: {useDefault: false,
overrides: [{method: popup, minutes: m}, …]}`, `extendedProperties.private.hub_reminder_id = <id>`.
`hash` = sha256 of the canonical JSON of the body.
### 2.3 Algorithm
1. Desired = reminder engine output for today.
2. Map = `sync/calendar_map.json` (rebuild per §2.4 if missing or if a mapped event 404s).
3. For each desired reminder: not in map → insert; in map with a different hash → patch; same hash → skip.
4. For each map entry not desired → delete the event, drop the entry.
5. Save the map atomically after each successful call (a crash mid-run must not cause duplicates next run).
6. Return counts `{created, updated, deleted, unchanged, errors}`; `--dry-run` computes them without calling Google.
Refinements (DECISIONS #106): every run lists the calendar's events once (paged, with deleted ones) and
reconciles the map with them first, so a mapped event that is gone (404), deleted (410) or `cancelled`
(deleted on the phone) is inserted again, never patched in place; a patch answered 404/410 deletes the old
id (ignoring "already gone") and inserts. Events carry `hub_hash` so a rebuilt map knows their contents.
A programme, professor or settings file that can't be read stops the run before any call (its events would
look resolved and be deleted). The calendar is recreated only when `calendars.get` on the stored id returns
404, at most once per run, logged (Q8). 401/403 or a refused refresh stops the run ("Reconnect Google").
Backoff waits are `google.backoff_s`. A run's counts go to `sync/last_sync.json`.
### 2.4 Rebuild
List events in the Applications calendar with `privateExtendedProperty=hub_reminder_id=*` style filtering
(list all events, keep those with the property); rebuild the map from them; delete duplicates sharing an ID.
### 2.5 Robustness
Timeout 30 s per call; retry 429/5xx with exponential backoff (1, 2, 4 s, max 3 tries); log counts and
IDs, never descriptions. Tests use `tests/fakes/fake_google.py`, an in-memory Calendar + Gmail service with
the same method chain (`service.events().insert(...).execute()`), including 404 and 500 injection.

## 3. iPhone setup (user steps, in README)
1. iPhone Settings → Calendar → Accounts → Add Account → Google → sign in; turn Calendars on.
2. If "Applications" is missing in the Calendar app's calendar list, open Google's calendar sync-select page
   in a browser, tick "Applications", save, and wait a few minutes.
3. Check that a test event shows its alert. If iOS ignores the per-event alerts, set a default alert in the
   Calendar app for that account and record it in DECISIONS.md (C-3-05).

## 4. Daily digest (`src/hub/google/digest.py`, CLI `hub digest [--dry-run]`)
- Sent at most once per day (`sync/digest_state.json`, date in `reminder_tz`); skipped if nothing to report
  and `digest.send_when_empty` is false.
- To = From = `settings.owner_email`. The send function takes no recipient argument; it reads the owner
  address itself and refuses if it is empty (C-3-06). `tests/test_no_outbound_email.py` scans `src/` and
  fails if any module other than `digest.py` calls the Gmail API or builds a MIME message, or if a
  recipient other than `owner_email` can reach the send call.
- Subject: `Application Hub · Fri 30 Oct: 3 items in the next 14 days`.
- Body (plain text + simple HTML, golden file `tests/fixtures/digest/golden.txt`): Overdue; Next 14 days
  (reminders, dated, in display zone); Waiting on professors (> 7 days since request); Unverified
  programmes with deadlines in < 60 days; link `http://127.0.0.1:8765/`.
- `--dry-run` prints the message instead of sending.
- Format (DECISIONS #106): header "Application Hub · <weekday day month year>" and "N items in the next 14
  days. Times are in <display zone>."; sections Overdue, Next 14 days (reminders dated today to 13 days
  ahead), Waiting on professors, Unverified programmes (deadline instant within 60 days; submitted ones left
  out), each "- …" lines or "- Nothing overdue." / "- Nothing due." / "- Nobody." / "- None."; deadlines
  with their display-zone time, the school time when the zones differ, and the source. A data file that
  can't be read is named in the header. The "items" count is the Next 14 days list. HTML: the same lines,
  escaped; checked structurally (the golden file is the plain text).

## 5. Windows Task Scheduler (`hub install-schedule` / `hub uninstall-schedule`)
Creates two tasks with `schtasks`:
- "Application Hub sync": at logon (2-minute delay) and daily at 12:00; runs `uv run --project <repo> hub sync`.
- "Application Hub digest": at logon (3-minute delay); runs `uv run --project <repo> hub digest`.
Both log to `<data_dir>/logs/hub.log`. Tests mock `subprocess.run` and assert the exact argv.
Limitation: nothing runs while the laptop is off; events already synced stay on the phone with their alerts.

## 6. Live tests (`uv run python tasks.py live -k calendar`, C-3-04)
Creates a temporary calendar "Application Hub test", inserts one event, patches it, deletes it, deletes the
calendar, asserts each step. Requires `.secrets/token.json`; skipped with a clear message otherwise.
