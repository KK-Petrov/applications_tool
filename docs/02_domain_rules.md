# 02: Domain rules

All rules here are pure functions in `src/hub/rules/` with no I/O, taking `today`/`now` as arguments
so tests can fix the clock. Parameters come from `config/project.yaml`.

## 1. Time zones
- A deadline instant = `due_local` interpreted in `tz` (zoneinfo). Ambiguous local times (DST fall-back)
  use `fold=0`, the **earlier** instant (conservative). Non-existent local times (spring-forward gap)
  are shifted forward by the gap (02:30 → 03:30) and flagged `adjusted` in the UI.
- Display: convert the instant to `settings.display_tz`. Always print the zone abbreviation
  (`ET`, `CT`, `CET`, `CEST`) next to a time; on programme pages also show the school-zone original.
- Format: `15 Dec 2026, 11:59 pm ET`; converted: `16 Dec, 05:59 Rotterdam` (24-hour clock for Rotterdam,
  12-hour for US zones; configurable in `time.formats`).
- Reference cases for C-1-02 (`tests/test_timezones.py`; expected values derived by hand):

| # | Local time | From | To | Expected |
|---|---|---|---|---|
| 1 | 2026-12-15 23:59 | America/New_York | Europe/Amsterdam | 2026-12-16 05:59 |
| 2 | 2026-12-15 23:59 | America/New_York | UTC | 2026-12-16 04:59 |
| 3 | 2027-01-07 23:59 | America/Chicago | America/New_York | 2027-01-08 00:59 |
| 4 | 2027-01-07 23:59 | America/Chicago | Europe/Amsterdam | 2027-01-08 06:59 |
| 5 | 2027-01-05 17:00 | America/New_York | Europe/Amsterdam | 2027-01-05 23:00 |
| 6 | 2026-10-28 12:00 | America/New_York | Europe/Amsterdam | 2026-10-28 17:00 (US on DST, EU not: 5 h) |
| 7 | 2026-10-24 12:00 | America/New_York | Europe/Amsterdam | 2026-10-24 18:00 |
| 8 | 2026-11-02 12:00 | America/New_York | Europe/Amsterdam | 2026-11-02 18:00 |
| 9 | 2026-11-01 01:30 (ambiguous) | America/New_York | UTC | 2026-11-01 05:30 (fold 0) |
| 10 | 2027-03-14 02:30 (gap) | America/New_York | UTC | 2027-03-14 07:30, flagged adjusted |
| 11 | 2027-03-20 12:00 | America/New_York | Europe/Amsterdam | 2027-03-20 17:00 |
| 12 | 2027-03-29 12:00 | America/New_York | Europe/Amsterdam | 2027-03-29 18:00 |
| 13 | 2026-12-01 09:00 | Europe/Amsterdam | America/New_York | 2026-12-01 03:00 |
| 14 | 2027-04-15 23:59 | America/New_York | Europe/Amsterdam | 2027-04-16 05:59 |
| 15 | 2027-04-15 23:59 | America/New_York | UTC | 2027-04-16 03:59 |
| 16 | 2026-12-31 23:59 | America/Los_Angeles | America/New_York | 2027-01-01 02:59 |
| 17 | 2026-12-31 23:59 | America/Los_Angeles | Europe/Amsterdam | 2027-01-01 08:59 |
| 18 | 2027-01-15 23:59 | America/New_York | America/Chicago | 2027-01-15 22:59 |
| 19 | 2026-10-25 02:30 (ambiguous) | Europe/Amsterdam | UTC | 2026-10-25 00:30 (fold 0) |
| 20 | 2026-11-01 12:00 | UTC | America/New_York | 2026-11-01 07:00 |

## 2. Countdown
`days_left = floor((deadline_utc − now_utc) / 24 h)`. Independent of display zone.
- `days_left ≥ 1` → "46 days" (singular "1 day"). `0 ≤ remaining < 24 h` → "Today, 5 h left".
  Negative → "Passed" (grey). Urgency colour: ≤ `urgency.orange_days` (14) orange; ≤ `urgency.red_days` (2) red.
- Boundary cases for C-1-03 (Princeton deadline 2026-12-15 23:59 New York):
  now 2026-10-30 12:00 NY → 46; now 2026-12-14 23:59 NY → 1; now 2026-12-15 00:00 NY → "Today, 23 h 59 min left";
  now 2026-12-16 00:00 NY → "Passed"; now 2026-12-15 23:58 Amsterdam (= 17:58 NY) → "Today, 6 h 1 min left".

## 3. Programme progress
- Requirements progress = done ÷ total. Documents progress = (received + waived) ÷ total, shown as "0 / 4 received".
- Dashboard sort: by primary-deadline instant ascending; passed deadlines last.
- "Unverified" badge if `programme.verification == unverified` or any deadline is unverified; the badge
  carries a reason: "Last cycle's date", "Third-party source", "Researched, not checked" (field `verification_note`).

## 4. Letters, coverage and next actions
- `required(p)` = `programme.letters_required`, else `letters.default_required` (2) shown as "assumed".
- `confirmed(p)` = letters with status `confirmed` or `submitted`. Coverage cell: "confirmed / required".
- `in_play(p)` = `requested + confirmed + submitted`. Over-ask warning when `in_play(p) > required(p) + letters.max_extra` (1).
- `request_due(p)` = primary deadline date − `reminders.letters_request_days` (42).
- **Next action** for a professor: evaluate every letter, take the highest-priority result, ties by earliest date:

| Priority | Condition (per letter to programme p) | Text | Urgent |
|---|---|---|---|
| 1 | course or course_description empty and any letter exists | Add course details | no |
| 2 | status `not_asked`/`draft_ready` and today > request_due(p) | Send request (overdue since {date}) | yes |
| 3 | status `requested` and today ≥ requested_on + 7 d | Follow up (asked {date}) | yes |
| 4 | status `not_asked`/`draft_ready` | Send request by {request_due} | if ≤ 7 days away |
| 5 | status `confirmed` and today ≥ deadline date − 14 d | Check submission for {short_name} | yes |
| 6 | status `submitted` and `thanked_on` empty | Send thank-you note | no |
| 7 | status `requested` | Waiting for reply | no |
| 8 | status `confirmed` | Waiting for submission | no |
| – | only `declined` or no letters | None | no |

- Sheet filters: **Needs action** = priorities 1–6; **Waiting on professor** = 7–8; **Done** = every letter
  `submitted` (thanked) or `declined`.
- Fixture for C-2a-02 (`tests/fixtures/letters/`): today 2026-10-30; professors covering every row above,
  including one with Princeton `requested_on: 2026-10-20` (→ priority 3) and one `not_asked` for Booth
  (request_due 2026-11-26 → priority 4, not urgent).

## 5. Reminder rules
### 5.1 Rules
Dates are computed on the deadline's **local date in the school zone** (D). Rule IDs and conditions:

| Rule ID | Date | Fires when |
|---|---|---|
| `letters_request_due` | D − 42 | confirmed(p) + requested(p) < required(p) |
| `documents_due` | D − 21 | any document status ∈ not_requested, requested |
| `letters_check` | D − 14 | any letter to p not submitted (and not declined) or fewer letters than required |
| `essay_final` | D − 7 | the programme has at least one essay |
| `deadline_alert_14/7/2/1` | D − 14 / 7 / 2 / 1 | programme status ≠ submitted |
| `deadline` | the deadline instant | programme status ≠ submitted |
| `letter_follow_up` | requested_on + 7 | letter status still `requested` |
| `interview` | the interview start | status `scheduled` |
| `interview_eve` | interview date − 1 | status `scheduled` |

Offsets come from `config.reminders`. Reminders dated before `today` are not produced. When a
programme becomes `submitted`, all its reminders disappear except interviews.

### 5.2 Event shape
- Rule reminders: timed events at `reminders.time_of_day` (09:00) in `settings.reminder_tz`, 15 min, one popup at 0 min.
- `deadline`: at the deadline instant in the school zone, 30 min, popups 1,440 and 120 min before.
- `interview`: at the start in its zone, 60 min, popups 1,440 and 60 min before. Estimated dates get "(estimated)" in the title.

### 5.3 IDs and titles
ID = `<rule>:<programme_id>` plus `:<subject_id>` for `letter_follow_up` (professor id) and interviews
(interview id). Title pattern: `[Hub] Princeton MFin: request letters (3 needed)`; description lists the
facts and the local app link `http://127.0.0.1:8765/programmes/<id>`.

### 5.4 Worked example and golden table (C-3-01)
Fixture `tests/fixtures/reminders/`: today 2026-10-30; `reminder_tz` America/New_York.
Princeton (D 2026-12-15 23:59 New York, 3 letters, no letters confirmed, documents not sent, one essay,
math test scheduled 2026-12-19 10:00 New York, estimated); Booth MiF (D 2027-01-07 23:59 Chicago,
letters_required null → 2 assumed); Columbia MSFE (D 2027-01-15 23:59 New York, 2 letters); professor
`example-a` with Princeton `requested` on 2026-10-29. Expected output, exactly these 30 rows (`tests/fixtures/reminders/golden.csv`):

| # | Rule | Programme | Date |
|---|---|---|---|
| 1 | letters_request_due | princeton-mfin | 2026-11-03 |
| 2 | documents_due | princeton-mfin | 2026-11-24 |
| 3 | letters_check | princeton-mfin | 2026-12-01 |
| 4 | deadline_alert_14 | princeton-mfin | 2026-12-01 |
| 5 | essay_final | princeton-mfin | 2026-12-08 |
| 6 | deadline_alert_7 | princeton-mfin | 2026-12-08 |
| 7 | deadline_alert_2 | princeton-mfin | 2026-12-13 |
| 8 | deadline_alert_1 | princeton-mfin | 2026-12-14 |
| 9 | deadline | princeton-mfin | 2026-12-15 23:59 America/New_York |
| 10 | interview_eve | princeton-mfin (math-test) | 2026-12-18 |
| 11 | interview | princeton-mfin (math-test) | 2026-12-19 10:00 America/New_York |
| 12 | letters_request_due | booth-mif | 2026-11-26 |
| 13 | documents_due | booth-mif | 2026-12-17 |
| 14 | letters_check | booth-mif | 2026-12-24 |
| 15 | deadline_alert_14 | booth-mif | 2026-12-24 |
| 16 | essay_final | booth-mif | 2026-12-31 |
| 17 | deadline_alert_7 | booth-mif | 2026-12-31 |
| 18 | deadline_alert_2 | booth-mif | 2027-01-05 |
| 19 | deadline_alert_1 | booth-mif | 2027-01-06 |
| 20 | deadline | booth-mif | 2027-01-07 23:59 America/Chicago |
| 21 | letters_request_due | columbia-msfe | 2026-12-04 |
| 22 | documents_due | columbia-msfe | 2026-12-25 |
| 23 | letters_check | columbia-msfe | 2027-01-01 |
| 24 | deadline_alert_14 | columbia-msfe | 2027-01-01 |
| 25 | essay_final | columbia-msfe | 2027-01-08 |
| 26 | deadline_alert_7 | columbia-msfe | 2027-01-08 |
| 27 | deadline_alert_2 | columbia-msfe | 2027-01-13 |
| 28 | deadline_alert_1 | columbia-msfe | 2027-01-14 |
| 29 | deadline | columbia-msfe | 2027-01-15 23:59 America/New_York |
| 30 | letter_follow_up | princeton-mfin (example-a) | 2026-11-05 |

Note: rule 1 for Princeton still fires because 1 requested + 0 confirmed < 3 required. Additional tests:
mark all Princeton letters submitted → rows 1 and 3 disappear; set Princeton `submitted` → rows 1–9 disappear,
10–11 stay; move "today" to 2026-11-04 → row 1 disappears (past).

## 6. Verification
- Only the POST `/programmes/{id}/verify` handler (user click) sets `verification: verified`, `verified_on`,
  and every deadline's `verification: verified`. Skills and imports always write `unverified`.
- Editing a deadline's date, time or zone resets that deadline to `unverified`.

## 7. Word count (C-4a-02)
A word is a maximal run of non-whitespace characters containing at least one letter or digit.
So `long-term` = 1, `U.S.` = 1, `2027` = 1, a free-standing `—` or `–` = 0, `well—known` = 1, a URL = 1.
Markdown syntax characters alone (`#`, `*`, `-` list bullets) are not words. Show "≈ portal count"
because portals differ. Fixtures: 15 short texts in `tests/fixtures/word_count/` with the expected count
in their file name.

## 8. Wrong-school check (C-4a-01)
For an essay or tailored CV targeting programme P:
1. **Flag terms** = for every other programme in `data_dir`: `school`, `unit`, `short_name`, `aliases`;
   plus `config.wrong_school.extra_terms` (other well-known schools).
2. Remove any term that equals or is contained in P's own `school`, `unit`, `short_name` or `aliases`, and
   every term in `config.wrong_school.own_background_terms` (RSM, Erasmus, Rotterdam, UVA, Virginia, McIntire).
3. Match **case-sensitively** on word boundaries (proper nouns are capitalised; this keeps "phone booth"
   clean), allowing a trailing possessive (`Booth's`, `Booth’s`). Multi-word terms match with any single
   whitespace between words.
4. Skip matches inside any phrase in `config.wrong_school.allow_phrases` (e.g. "British Columbia").
5. Report each hit as `{term, line, column, excerpt}`. The user can mark a hit "intended"; that is stored
   in `meta.yaml → intended_mentions` and suppressed on later checks of the same version only.
Fixtures: `tests/fixtures/essays/planted/` (essays with 20 planted mentions in total, including possessives,
multi-word names and aliases such as "Sloan") and `tests/fixtures/essays/clean/` (10 essays mentioning the
own programme, own background, "British Columbia", "phone booth").
