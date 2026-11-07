# Phase 2c: Programme research and course import

## Goal
A new programme is researched from official pages and added in under five minutes, marked unverified with
sources; a stored programme can be re-researched and the user accepts or rejects each change field by field.
Courses are imported from a transcript (pasted text, .docx or a text-based PDF) into `courses.yaml`, with every
grade checked against the source and no personal identifiers stored; professors are created from the same
document, linked to their courses.

**Recommended:** 2 sessions · high effort. If the phase won't finish within that, stop at a clean point and run `/handoff`.

## Read first
docs/04_ai_features.md §1, §2.1, §7, §9 (all of it carefully); docs/01_data_contract.md §1, §3 (incl.
`official_domains`), §4, §10; docs/02_domain_rules.md §4, §6, §9, §10; docs/03_ui_spec.md §1, §2 (US-16, US-18 to
US-21), §4.2, §4.3, §4.4, §4.8, §5; docs/DECISIONS.md #13, #21, #30, #31, #37, #39–#44; config/project.yaml →
`claude`, `research`, `letters`.

## Tasks
### Programme research (as planned in DECISIONS #37)
1. **Skill.** `.claude/skills/research-programme/SKILL.md` per docs/04 §7 (tools WebSearch, WebFetch). Official
   university or school pages only (DECISIONS #31). A field no official page provides stays null with the note
   "No official source found"; never a third-party source. Every filled field has a `source_url`; the output is
   always `verification: unverified`. The skill proposes `official_domains` for the programme.
2. **Official-source check (Python, rule 13).** A source is official only if its host equals one of the
   programme's `official_domains` or is a subdomain of one (suffix match on a dot boundary: `business.columbia.edu`
   matches `columbia.edu`; `princeton.edu.evil.com` and `evilprinceton.edu` do not), or is on
   `config.research.official_shared_domains` (initially `mba.com`, GMAC). Any other host is refused at preview with a
   message naming the field (e.g. "Deadline 1 · Source URL: example-aggregator.com is not an official source").
3. **Research a new programme (US-16).** "Research a programme" on the dashboard and Add programme page: query
   field → run → the preview first shows the proposed `official_domains` for the user to confirm or edit; only then
   do the source checks run. Preview shows sources and nulls ("No official source found") highlighted → Save writes
   `programmes/<id>.yaml`. Save is blocked until a deadline with `tz` and `source_url` exists (DECISIONS #13).
4. **Re-research (US-18).** A "Re-research" button on each programme page runs `/research-programme` for that
   programme and shows a field-by-field comparison with the stored data: new / changed / unchanged, each with its
   source URL. The user accepts or rejects each change; nothing is written until Save. Accepted changes to a
   deadline's date, time, zone or source reset that deadline to unverified (DECISIONS #21, #30); verification is
   never set by research. A programme without `official_domains` (e.g. the owner's existing files) gets them
   prefilled from the hosts of its stored source URLs, and the user confirms before the comparison runs. Saving
   keeps the file's shape and comments (DECISIONS #29b).
5. **Live test.** `tasks.py live -k research` on two programmes (e.g. "Berkeley Haas MFE", "Carnegie Mellon MSCF")
   validating schema, official sources and `unverified` (C-5-01). Live tests never write to the user's `data_dir`
   (they run with `HUB_DATA_DIR` set to a temporary folder).

### Course import and professors from a document (DECISIONS #44)
6. **Models and config.** `courses.yaml` model (docs/01 §10); Professor gains `title` (default "Professor"),
   optional `email` and `course_ids` (docs/01 §4). New config sections `names` (particles, title tokens → title,
   title precedence) and `courses` (identifier labels in English and Dutch, allowed file types, upload size limit,
   minimum text per PDF page) with their config models, so no parameter is hard-coded (rule 5). Update the Phase 2a
   code that these touch: Add/Edit (title, optional email, linked courses), the sheet and panel ("Email missing"),
   the duplicate-email check (skips empty emails), next-action priority 1 (docs/02 §4), and the salutation surname
   (docs/02 §9). Phase 2a tests that change (e.g. "Email is required") are changed with disclosure.
7. **Names (docs/02 §9).** Parse initials, title tokens and Dutch particles; the surname keeps its particle;
   a standalone salutation capitalises the first particle; a stripped title token pre-fills `title` through
   `names.title_tokens` (else "Professor"). Matching key: surname + initials, case- and diacritic-insensitive.
   Tests in `tests/test_names.py`.
8. **Text and identifiers (docs/02 §10, rule 13).** Extract text from pasted text, .docx and text-based PDFs; a PDF
   with too little text per page is refused as scanned ("This PDF has no text layer; OCR is not supported. Export
   it as text or Word, or paste the text."). Strip identifiers in Python, label-anchored (English and Dutch labels in
   config); a standalone 9-digit number is removed only if it passes the BSN 11-check. Strip again after
   extraction. Store only the stripped text as `sources/<name>.txt` plus the original's file name and sha256
   (never the original file).
9. **Skill `/extract-courses`** per docs/04 §9: rows with name, code, institution, grade, credits, term,
   lecturers, description as written. The preview (`/courses/import`) shows every row and field; a grade the code
   cannot find verbatim in the stripped source text is flagged; nothing is written until the user accepts rows
   (per row or all), which writes `courses.yaml` with `confirmed: true`. Reuse the research preview/accept pattern.
   A minimal Courses list (`/courses`); Phase 4a shows courses in full on the biography bank page.
10. **Create professors from this document (US-20).** After courses are confirmed: every lecturer listed once
    (name as written, their courses with the owner's grade, a listed-first marker), sorted by best grade, nothing
    ticked by default. A lecturer matching an existing professor is offered for linking, not duplicated. Ticked
    lecturers are created in one batch through `Store.create_professor`, with `course_ids` linked, email empty and
    `title` from the name. The email payload (docs/04 §2.1) uses every linked course.
11. **Add professor suggestions (US-21).** Typing a name suggests courses whose lecturers match the surname
    (case- and diacritic-insensitive); choosing one links the course and fills course, description, grade and term;
    everything stays editable and nothing auto-saves.
12. **Eval set and live test.** `tests/evals/courses/`: an invented 28-course transcript (invented student,
    institutions and lecturers) as .docx, text PDF and pasted text, with expected rows; identifiers in several
    formats in English and Dutch, plus course codes and other numbers that resemble IDs. `tasks.py live -k courses`
    runs `/extract-courses` on it and grades the output (C-2c-03).

## Tests first
In your plan, map every automated criterion below to the test or script that checks it and the command
that runs it. After approval, write those tests **before** the implementation and show that they fail
for the right reason. Never edit a test to make it pass without saying so and getting approval.
Required supporting tests:
- `tests/test_research_preview.py` (fast, fake Claude): a non-official host is refused at preview and the message
  names the field; a subdomain of an official domain is accepted; look-alike hosts (`princeton.edu.evil.com`,
  `evilprinceton.edu`) are refused; an `mba.com` source is accepted; re-research on a programme without
  `official_domains` asks for confirmation before comparing.
- `tests/test_names.py`: initials, title tokens (title pre-filled; "Professor" otherwise), Dutch particles
  (surname "van den Berg"; standalone salutation "Van den Berg"), diacritic- and case-insensitive matching.
- `tests/test_course_import.py` (fast, fake Claude): every identifier in the fixture transcript is removed and
  appears nowhere in data_dir (including `sources/`); every course code, grade and credit value survives; a BSN-like
  number failing the 11-check is kept; a scanned PDF is refused; an unfound grade is flagged; nothing (courses or
  professors) is written before the user confirms; each lecturer is listed once with all their courses; a lecturer
  matching an existing professor is offered for linking; batch creation never overwrites a file.

## Acceptance criteria
Fixed at the design freeze (`config/criteria.json`) and guarded; never lowered without the user's approval. Criteria of earlier phases must keep passing.
- [ ] **C-5-01** Researching two programmes yields schema-valid files where every filled field has a source URL and the programme is marked unverified. *Verify:* `uv run python tasks.py live -k research`. *(automated, gate)*
- [ ] **C-2c-02** Re-research writes nothing until the user saves; accepted deadline changes reset those deadlines to unverified; rejected changes leave the file byte-identical. *Verify:* `uv run pytest tests/test_research_merge.py tests/e2e/test_research.py -k us18`. *(automated, gate)*
- [ ] **C-2c-03** On the invented 28-course fixture transcript, course import extracts every course with exact name, grade and credits, flags 0 false grades, stores no identifiers, lists every lecturer once with all their courses, and writes nothing (courses or professors) until the user confirms. *Verify:* `uv run python tasks.py live -k courses` (tests/evals/courses/) and `uv run pytest tests/test_course_import.py`. *(automated, gate)*

## Reviewer
Run **ai-output-reviewer**, **ui-reviewer** and **data-integrity-reviewer** at the end.

## You check
- Research one programme you are considering; open every cited source and check it is the school's own page.
- Re-research one of your programmes, reject one change and accept another; confirm the file shows only the accepted change.
- Import your own transcript (in the app only); check every course name, grade and credit value against the
  original, and that no student number or date of birth appears in the data folder.
- Create professors from it; check names, titles, particles and the linked courses.

## Out of scope
The Settings usage total, README and the final end-to-end pass (Phase 5); reminders (3); the full course view on the biography
bank page (4a); OCR of scanned documents.
