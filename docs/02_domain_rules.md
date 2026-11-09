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
| 1 | no course details (no linked course with a name and a description, and course or course_description empty) and any letter exists | Add course details | no |
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

## 9. Names (Phase 2c, DECISIONS #44)
Lecturer names are parsed in Python from the name as written (`config.names`):
1. **Title tokens** (`names.title_tokens`, case-insensitive, with or without a full stop: prof., dr., ir., drs.,
   mr., …) are stripped. The professor's `title` is pre-filled from the highest-ranked stripped token
   (`names.title_precedence`; "prof." → "Professor", "dr." → "Dr."); with no token it defaults to "Professor".
   The user can edit it.
2. **Initials** are tokens of one letter followed by a full stop, possibly joined ("J.A." = J, A).
3. **Particles** (`names.particles`: van, van der, van den, de, den, der, ter, te, ten, 't, in 't, op de, von, …)
   belong to the surname: "Prof. dr. A.B. van den Berg" → surname "van den Berg", initials "A B", title
   "Professor".
4. **Salutation:** the surname keeps its particle in lower case after a first name or initials ("A. van den
   Berg"); standing alone, as in "Dear Professor Van den Berg,", the first particle is capitalised.
5. **Matching** (lecturer ↔ existing professor, Add professor suggestions): surname plus initials, compared
   case-insensitively and with diacritics removed ("Müller" = "Muller"). A lecturer without initials matches on
   surname only and is offered for linking, never merged silently.
Worked examples (tests/test_names.py): "Prof. dr. A. van den Berg" → Professor · van den Berg · "Dear Professor
Van den Berg,"; "Dr. J. de Vries" → Dr. · de Vries · "Dear Dr. De Vries,"; "S. Whitfield" → Professor ·
Whitfield.

## 10. Identifiers and grades in imported documents (Phase 2c, DECISIONS #44)
Rule 13: both checks run in Python, never in the model.
0. **Invisible characters first** (DECISIONS #69): before any label or value matching, every Unicode default-ignorable code point (#74: also the combining grapheme joiner, variation selectors, Hangul fillers) and format character (category Cf: zero-width space, zero-width (non-)joiner, byte-order mark, soft hyphen, direction marks …) is removed and every space character (category Zs: no-break, narrow no-break, thin, figure …) becomes a plain space, so neither can hide a label ("Student\u200Bnumber") or split a value ("51\u200B2345"). The normalised text is what is shown, sent and stored; the original file name is normalised the same way.
1. **Identifier stripping is label-anchored, known layouts only, and fails closed** (`src/hub/courses/identifiers.py`;
   DECISIONS #64, #65, #84). **Known layouts only (user decision, #84):** a label's value is removed only in one
   of four layouts; any identifier label outside them stops the import with "The document mentions "<label>" on
   line <n>, but not in a layout the app can read safely, so it can't remove its value whole. Nothing was sent
   to Claude. Paste the text, or put the identifier as "Label: value" on its own line." The four layouts:
   (1) **"Label: value"**, on a line or in a table cell: each label directly followed (only separators: spaces,
   `: ; , . - – # = (`) by its own value, taken whole; several pairs may share a line ("Diploma register
   2023-004512 - Document number 123456789"); an ID-shaped value before the first label is outside;
   (2) **label, value on the next line**: a line of one or more labels with no value on it (other words allowed),
   then the next non-empty line, without tabs and without a label, starting with their values in order
   ("Student ID" / "S2034567"; a PDF heading row "Studentnummer Geboortedatum Opleiding" / "598431 12-03-2001
   BSc …", the tabbed layout 4 with its tabs lost); (3) **label cell, value cell**: a cell holding one label and
   nothing else, the next cell holding the value alone (words after it allowed, none before);
   (4) **heading row, value row**: a tabbed row of headings, at most one label per cell and no value anywhere on
   it, then the next non-empty row, also tabbed and without a label, each label's value alone in its column, not
   going on in the next cell; a cell no heading heads holds nothing ID-shaped. Words between a label and its value
   ("Student identification number or code (if available): 512345"), two labels in one cell, a heading cell
   beside a "Label: value" cell, a label line followed by a table row or another label line, and a value split
   across cells are all outside. Edges inside the layouts (#87): after a layout 2 or 4 value row, the rows that
   follow are read on (layout 4: tabbed rows with digits and no label, past blank lines, until a heading row;
   layout 2: the lines right after it with no blank line, tab or label), and an ID-shaped value in a label's
   column, or leading such a line, stops (a second enrolment, a table of earlier diplomas); in a layout 1 or 3
   row, an ID-shaped cell must be a taken value or the value cell of another (non-label) key cell
   ("Ref<TAB>598431"), else it stops; layout 2's label line holds no value at all ("4521 Student number"
   stops); a bracket holding a digit or the marker is the label's value, never its abbreviation, so
   "Student number (512345)" reads the same at Send. Session 4 (#88, #89, user decisions): a label followed by a
   colon and then empty cells ("Student number:<TAB><TAB>598431", Word tab alignment) is layout 1, the value
   being the next non-empty cell (without the colon it stays outside), and a non-label key with a colon is found across empty cells too ("Ref:<TAB><TAB>598431" keeps its value, #90); a .docx soft line break inside a table
   cell reads as a space, so "Student⏎number" in a cell is one label. The rules below describe how a value is found and taken whole inside these
   layouts. A number or date is removed only when an identifier label (`courses.identifier_labels`, English and Dutch:
   student number / nr. / ID / identification number, matriculation or registration number, date of birth, DOB,
   SSN, BSN, diploma register (number), document number / nr. / no. / nummer, passport no., ID card number, matrikelnummer, born on / in, studentnummer, stud.nr., geboortedatum, geboren op / in, burgerservicenummer, diplomaregisternummer, documentnummer, …) anchors it; "born" and "geboren" alone (`courses.date_labels`) are labels by default and none only right after a title token, an initial or a particle (config `names`: "Prof. M. Born", "Dr. K. Geboren", "M. van Born") or when the first thing after them, past an optional "(…)" and separators, is a word that is not a month ("the Born rule", "Born Rule Seminar"); a colon directly after them always makes them labels, also after a title, an initial or a particle ("Dr. Born: 12-03-2001", #100) (#89, #90, #92); "born at" and "geboren te" are labels, so "Geboren te Rotterdam" stops as before #89, except when rule (d) below removes a date on that line (#100):
   - **same line:** the first number of 2+ digits (a letter prefix and digit groups such as "123 456 782" allowed),
     date (numeric either way round; with a month name in English or Dutch, also abbreviated and with a 2-digit or
     'YY year: 12-MAR-01, 2001-Mar-12, 12 maart '01; with an ordinal "12th"/"3rd of"; a Roman month 12.III.2001;
     month and year "March 2001") or US SSN after the label, before the next label or, in a table row, the next
     tab; in a **key/value row** ("Student number<TAB>512345") the value is the next cell (DECISIONS #67);
   - **label on one line, value on the next:** a label with no number on its line takes the value that **leads**
     the next non-empty line ("Student ID" / "S2034567", or a short "4123"); anything else first stops the import;
   - **table label row, then value row:** a tab-separated pair is matched column by column ("Studentnummer
     Opleiding  Geboortedatum" / "598431  56966 BSc …  12-03-2001": the middle column's programme code is kept);
     without tabs (a PDF row) the k values must lead the next line, in order. Each such value must be taken whole as on the label's line (no month word, Roman month or 'YY right after it, and no digit after the last one), and nothing ID-shaped (5+ digits, or a date) may remain on the line after the labels' values; otherwise the import stops: the app can't tell which is the identifier (DECISIONS #73, #74). A bracket after a label belongs to it only without digits ("(BSN)"). In fields Claude returns, a value not taken whole is removed with every other candidate after its label (#74). At Read the marker "[removed]" is never a value; at Send it may fill a label's slot but never hides a number after it (#73). A tabbed value row stops the import if a cell no label heads (past the label row's last cell, or under a blank heading cell) holds an ID-shaped token (5+ digits or a date; at Send also the marker): no heading says what it is (DECISIONS #77, #78). In a key/value row the next cell is the value cell only if it holds the value alone (spaces and punctuation around it) and doesn't start with another label; otherwise the row is a label row and the value is on the next row (#78, #81, #82). Two labels in one cell stop the import, in a key/value row and in a label row (a merged Word header "Student number / Date of birth") (#82, #83). The value cell may have words after the value (no digit, no label) but none before it (#83). A short value (under 5 digits, no date) whose next cell goes on with a digit, month word, Roman month or 'YY ("12<TAB>03<TAB>2001") stops the import (#83). A label row without a heading cell followed by a row with words in its other cells (a course row) is not its value row: the import stops (#82). The Save step re-strips the text as Send does and refuses (409, "Nothing was saved: the text changed after it was checked. Read the document again.") a text that no longer strips (#82, #83); a label row may keep nothing ID-shaped beside its labels' removed values, otherwise the import stops (#81). A heading is a cell with a letter; a column headed by an identifier label is an identifier column (#81). In fields Claude returns, a value row, a label row or a key/value cell that can't be resolved loses every candidate (#78, #81). A year-serial register number (4 digits, a hyphen, 5+ digits: "2023-004512") is one value; "2021-2022" and "5123-45" are not (DECISIONS #76).
   **Fail closed:** the value must be taken whole: a digit just before it; just after it, past separators, a digit,
   a month word, a Roman month or 'YY (an unknown format such as "12/3 2001"); another number later in its segment
   ("512345, previously 498765"); or a label whose value can't be found stops the import with the message above
   (#84); nothing is sent to the model or written. A standalone 9-digit number is treated as a BSN, and
   removed, only if it passes the 11-check; a US SSN shape (123-45-6789) is removed even without a label.
   **Beyond labels (DECISIONS #93; real transcript: an unlabelled PDF footer "J.A. Example (512345)   1/9" on
   every page was sent):** (a) on a line naming the owner (`settings.owner_name`: the surname with its particle,
   as whole words, any case, which covers the full name and "J.A. Example"), (b) on a page header or footer (a
   line holding a page number such as "1/9", "Page 1 of 9", "pagina 1 van 9" whose text apart from it stands
   on 2+ lines), and (c) inside a bracket holding nothing else, every ID-shaped token goes. ID-shaped here: a
   run of 5+ digits (digit groups allowed), at most one letter in front, up to 3 letters after and then an
   optional hyphen with 1–3 digits, the 5+ digits counted before that suffix (512345, 598 431, S2034567,
   512345ab, 512345-01; #100), or a date; never a course code ("FEB11001", "FIN 3010", "3010-01"), a year range,
   credits ("( 20.0 )") or a page number. Known consequences (fail closed): a 5-digit programme code alone in
   brackets ("(50952)") is removed; on a line naming the owner a 5+ digit number of any kind goes.
   **(d) A date after "born"/"geboren" (DECISIONS #100, user decision on data-integrity r11 F3):** on any line, the
   first date with at most `courses.born_date_max_words` (4) words between it and "born"/"geboren" goes,
   whatever stands before the word or between ("Born Rotterdam, 12-03-2001", "geboren te Sofia op 12-03-2001",
   "Prof. M. Born<TAB>12-01-2022"). A "born …"/"geboren …" label outside the four layouts no longer stops the
   import when (d) removes such a date and every label on the line is a date label and nothing else ID-shaped
   stands on it; otherwise it stops as before. Known consequences: a lecturer named Born followed by a
   completion date loses that date (the row is flagged; accepted, safe side); a date more than 4 words after
   "born" on a line where "born" is no label (e.g. "Born to a family of farmers near the sea, 12-03-2001") is
   not removed. Course codes, grades, credits and years are kept. Each removed value
   becomes `courses.removed_marker` ("[removed]"). Stripping runs on the text when it is read, again (strictly) when the user presses Send to Claude after reviewing it (DECISIONS #70; a label followed by the marker counts as already removed, so stripping stripped text changes nothing), and on every returned field (there, an unresolved label is left alone). The original
   file's name (stored in the source header) loses every date pattern and every run of 6 or more digits, and is
   kept on one line; the header's sha256 is written only if it is 64 hex digits.
2. **Grades and credits are facts, anchored to their row** (DECISIONS #64, #65; stricter than "anywhere in the
   text"). A row of the stripped text is the course's row when it holds the course's code **and** name (or, when
   the code is on no line, its name on word boundaries, so "Mathematics I" doesn't match "Mathematics II"). The
   grade is accepted only if its exact string stands on that row as a whole value **outside the row's own name,
   code, term and one credits value** ("8" doesn't match "8,5"; "A" doesn't match "A-", "A+" or an initial "A.";
   a grade equal to the credits "4", the year "2021" or "block 1" is not found (after the row's own name, code and term, years, year ranges whose second year follows the first, and "<term word> N" from `courses.term_words` are taken out of the row even when the model returned no term, DECISIONS #80, #81; dates on the row are taken out first; after a qualifier from `courses.term_qualifiers` ("1e", "Fall"), and after a one-letter term word ("Q 1", "S 2"), the number is the grade unless another grade-shaped value comes between it and the credits ("Fall semester 2 7 4": 2 is the term number; #82, #83); with no credits returned the row's last value stands in for them, so "Fall semester 8 4 A" (an ECTS letter after the credits) is then read as a term number (known limit); a term word after an apostrophe ("Master's 9") is no term word (#83); word grades (Pass, V, VR) are not counted as values (known limit, #83); known limits: a bare "semester 8" is read as the term, so a grade 8 right after it is flagged, and with no credits returned a wrong grade equal to the printed credits is found); a real grade "6" with credits "6"
   still is, since the row then holds 6 twice). The credits must stand on the row too, compared as numbers ("3",
   "3.0", "3.00", "7,5" all equal 3 or 7.5). With a term, only rows that also hold it count ("Term not found on this
   course's row" otherwise: a course taken twice has two rows). In a tab table under a heading row naming a grade or
   credits column (`courses.grade_headings`, `credit_headings`), the grade must be that column's cell exactly and the
   credits that column's number (DECISIONS #67). Otherwise
   the row is flagged ("Grade not found in the document", "Credits not found on this course's row", "Course not
   found in the document") and can't be accepted until the user corrects it. The same check is used by the app's
   preview and the live grader. Known limit: a wrong grade that equals another value printed on that row (an
   extra column) passes; the live eval also compares every grade with `expected.json`. Whole values (#91): a
   course name cut short ("Mathematics" where the row says "Mathematics II") and a lecturer that is not one
   whole name are flagged (docs/04 §9). **A name wrapped in its column** ("Professional Development &" /
   "Mentoring I", #93) counts as written when the joined text is the name exactly: in a table row a cell holds
   the first part and the same cell of the next line the rest, alone; on a line without tabs the first part is
   followed only by the row's values and the next line is the rest, alone (a matching refinement, not a
   looser check). Known limit: when a PDF puts the row's values after the wrapped part, the grade is not on
   the name's first line and is flagged (safe side). Known limits: a description fragment passes; on an untabbed row an
   institution's name right after the course name reads as part of it (flagged, safe side).
3. **Scanned PDFs:** a PDF whose average non-space text per page is below `courses.min_pdf_text_per_page` is
   refused: "This PDF has no text layer; OCR is not supported. Export it as text or Word, or paste the text."
