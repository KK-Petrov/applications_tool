# 01: Data contract

All user data lives in one folder, `data_dir`, set in `config/project.yaml` → `storage.data_dir`
(an absolute path, typically inside OneDrive). In tests, `data_dir` is a temporary folder filled from
`tests/fixtures/data/`. The repository never contains real user data.

## 1. Folder layout
```
<data_dir>/
  settings.yaml                         # §2
  programmes/<programme_id>.yaml        # §3, one file per programme
  professors/<professor_id>.yaml        # §4, one file per professor
  biography.yaml                        # §5
  courses.yaml                          # §10, courses imported from a transcript (Phase 2c)
  sources/<name>.txt                    # §10, identifier-stripped text of an imported document
  essays/<programme_id>/<essay_id>/
      meta.yaml                         # §6
      v1.md, v2.md, …                   # draft text, immutable once saved
      v2.eval.json                      # §6.3, evaluation of version 2 (Phase 4b)
  cv/base/meta.yaml, v1.md, …           # §7
  cv/<programme_id>/meta.yaml, v1.md, …
  sync/calendar_map.json                # §8
  sync/digest_state.json                # §8
  logs/hub.log                          # rotating, no payloads
  logs/ai_usage.jsonl                   # §11, AI usage log of the app's calls (DECISIONS #60)
```
- IDs are lowercase slugs `[a-z0-9-]{2,60}`, unique within their folder, never reused after deletion.
- All YAML is read and written with ruamel.yaml round-trip mode so the user's comments and key order survive.
  Files are block style (no `{…}`/`[…]` flow collections except empty ones), scalars are never wrapped and a
  file keeps its LF or CRLF line endings; a file still in flow style is converted once on its first save
  (DECISIONS #16, #19). The examples below use flow style only for brevity. Limitation (ruamel): a comment
  written *inside* a multi-line flow collection is lost when that file is converted; comments elsewhere survive.
- Writes are atomic: write `<file>.tmp`, then `os.replace`. A failed validation never overwrites a file.
- Dates are ISO `YYYY-MM-DD`; local date-times are `YYYY-MM-DDTHH:MM` **without** offset, always paired with an IANA zone field.
- Stored timestamps (when something happened: `generated_at`, `saved_at`, `synced_at`, `evaluated_at`) are
  ISO 8601 **with** their UTC offset, e.g. `2026-11-04T14:05:00-05:00`; a value without an offset is invalid (DECISIONS #18).

## 2. settings.yaml
| Field | Type | Notes |
|---|---|---|
| `owner_name` | str | Signs emails ("Kind regards, <owner_name>") |
| `owner_email` | email | The only address the digest may be sent to |
| `display_tz` | IANA zone | One of `config.time.display_zones`; set by the header selector |
| `reminder_tz` | IANA zone | Zone for timed reminder events; default `America/New_York` |
| `calendar_id` | str or null | Google calendar ID, filled by the first sync |
| `google_connected` | bool | Set after `hub connect-google` succeeds |

## 3. programmes/<id>.yaml
```yaml
id: princeton-mfin
name: Princeton Master in Finance
short_name: Princeton MFin
school: Princeton University
unit: Bendheim Center for Finance
aliases: [Princeton, Bendheim, BCF]          # proper nouns used by the wrong-school check (docs/02 §8)
official_domains: [princeton.edu]           # optional; hosts (and their subdomains) that count as official sources (DECISIONS #37)
status: not_started                         # not_started | in_progress | submitted
verification: unverified                    # unverified | verified
verification_note: Last cycle's date        # free text shown on the badge (docs/02 §3); null when verified
verified_on: null                           # date, set only by the Mark verified action
portal_url: https://...
sources: [{url: https://..., title: "...", checked: 2026-10-30}]
deadlines:
  - id: main
    label: Application deadline
    due_local: 2026-12-15T23:59
    tz: America/New_York
    time_known: true                        # false → time assumed 23:59 and shown with "(time to verify)"
    verification: unverified
    source_url: https://...
letters_required: 3                         # int or null (null → config default, shown as "assumed")
letters_note: Without full-time work experience, two must be academic
requirements: [{id: cv, label: Resume / CV, note: null, done: false}]
steps: [{order: 1, label: Create an account on the Graduate School portal, note: null, done: false}]
                                            # a step may carry `requirement_ids: [letters]` (optional; DECISIONS #97)
documents:
  - {id: gmat, label: GMAT official score report, method: GMAC → Princeton, status: not_requested, updated: null, note: null}
interviews:
  - {id: math-test, kind: assessment, label: Math assessment test, start_local: 2026-12-19T10:00,
     tz: America/New_York, estimated: true, status: scheduled, notes: null}
essays: [{id: academic-purpose, title: Statement of academic purpose, prompt: "...", word_limit: null}]
curriculum: {summary: "...", courses: [{name: "...", description: "..."}]}
ai_policy: {text: null, source_url: null}
fee: {amount: null, currency: USD, note: "[amount to verify]"}
```
Enums: document `status` ∈ `not_requested | requested | sent | received | waived`; interview `kind` ∈
`interview | assessment`; interview `status` ∈ `not_invited | invited | scheduled | done`.
The **primary deadline** is the earliest deadline whose date has not passed; if all passed, the last one.
`official_domains` entries are lowercase host names (`columbia.edu`), unique; research accepts a source only on
these domains, their subdomains or `config.research.official_shared_domains` (docs/04 §7).
Validation: every deadline needs `tz` and `source_url`; `due_local` must parse; a programme with
`verification: verified` must have at least one source; a step's `requirement_ids` must name requirements of
the programme. **Step links (DECISIONS #97):** `requirement_ids` (default empty, written only when set) say which
requirements a step is for; "Add standard steps and documents" sets them; Edit programme keeps them, and when a
requirement is removed it drops the link and says so ("<step> is no longer linked to <requirement>."); the key is
removed when no link is left. **Edit programme saves only over the version it showed** (DECISIONS #99): the form
carries the file's sha256, and a save is refused (409, nothing written) when the file changed since, because steps
and documents are rebuilt from the posted rows.

## 4. professors/<id>.yaml
```yaml
id: mulder
name: Prof. Mulder                # display name; surname = last token, override with `surname`
surname: Mulder
title: Professor                  # used in "Dear {title} {surname},"; default "Professor", e.g. "Dr." (2b; DECISIONS #48)
email: m.mulder@example.org       # optional (null → "Email missing"); never shown in the sheet
university: RSM                   # free text; the form offers RSM / UVA McIntire / Other
course_ids: []                    # links to courses.yaml (§10); with links, `course` is not sent; a manual
                                  # course_description fills the linked course it belongs to (docs/04 §2.1, #66)
course: "[course name and code]"
course_description: |             # verbatim paste, any length up to 20,000 characters, never trimmed
  ...
grade: null                       # optional free text, e.g. "9.0 / 10"
term: null                        # optional, e.g. "Fall 2025"
interaction: null                 # optional free text
tone: formal                      # formal | warm
letters:                          # key = programme_id; absent key = not asked for that programme ("—")
  princeton-mfin: {status: draft_ready, requested_on: null, confirmed_on: null,
                   submitted_on: null, thanked_on: null, last_contact: null}
emails:                           # generated drafts, newest last
  - {generated_at: 2026-10-31T10:12:00-04:00, programmes: [princeton-mfin], subject: "...", body: "...",
     warnings: ["Princeton MFin's deadline is unverified — check it before sending"], marked_sent_on: null}
     # warnings: shown with the draft, unverified deadlines first (DECISIONS #48); marked_sent_on: stamped by
     # Mark as requested (on the draft the panel shows) when ANY programme it moves to Requested is one of
     # this draft's programmes; it need not cover them all (US-10; DECISIONS #56). A stamped draft keeps its
     # first date.
notes: null
```
Letter `status` ∈ `not_asked | draft_ready | requested | confirmed | submitted | declined`.
Setting a status stamps the matching date with today (editable afterwards); `last_contact` is the latest stamped date.

## 5. biography.yaml
```yaml
entries:
  - id: gmat
    category: test        # education | test | experience | honour | skill | goal | other
    title: GMAT Focus Edition 735 (99th percentile)
    detail: Quant 93rd, Verbal 96th, Data Insights 99th percentile
    tags: [quantitative]
    state: actual         # actual | anticipated
    dates: null           # free text, e.g. "Jun–Aug 2025"
```
`anticipated` entries may be mentioned only as anticipated ("expected cum laude"), checked in docs/04 §4.

## 6. Essays
### 6.1 meta.yaml
`programme_id`, `essay_id` (matches `programmes/<id>.yaml → essays[].id`), `title`, `prompt`,
`word_limit` (int or null), `current_version` (int), `versions: [{n, saved_at, words}]`.
### 6.2 vN.md
Plain Markdown draft. Saving always creates `v<n+1>.md`; existing versions are never modified.
### 6.3 vN.eval.json
The evaluator output (schema in docs/04 §5.2) plus `exact_checks` (word count, wrong-school hits from
docs/02 §7–8) and `evaluated_at`.

## 7. CVs
Same structure as essays: `cv/base/` holds the base CV, `cv/<programme_id>/` a tailored version.
`meta.yaml` has `kind: base | tailored`, `programme_id` (null for base), `based_on` (base version number).
CVs are evaluated with the same rubric; Programme fit uses the target programme.

## 8. Sync state
- `sync/calendar_map.json`: `{ "<reminder_id>": {"event_id": "...", "hash": "<sha256 of event body>", "synced_at": "..."} }`.
  It can be rebuilt from Google by listing events with the private extended property `hub_reminder_id` (docs/05 §2.4).
- `sync/digest_state.json`: `{ "last_sent_date": "YYYY-MM-DD" }` in `reminder_tz`.

## 9. Models and loader
- `src/hub/models/` holds one Pydantic model per file type above; enums as `StrEnum`.
- `src/hub/store.py` exposes `load_programmes()`, `save_programme()`, `load_professors()`, … Every load
  validates; an invalid file raises `DataError(path, field, message)`, which the UI shows as a banner
  naming the file and field and the CLI prints the same way (C-0-04). Other files still load.
- `hub init-data` copies `seed/programmes/*.yaml` into an empty `data_dir/programmes/` and creates
  `settings.yaml` from prompts (owner name, owner email). It never overwrites existing files.
- Later phases add: `essays/` and `cv/` (4a), `vN.eval.json` (4b), `sync/` (3).

## 10. courses.yaml and sources/ (Phase 2c, DECISIONS #44)
```yaml
courses:
  - id: corporate-finance-fin-101       # slug, unique, never reused
    name: Corporate Finance             # exactly as on the transcript
    code: FIN-101                       # as written, or null
    institution: Example School of Economics
    institution_entered: false          # true when the owner typed the institution in the import preview
                                        # ("your own entry", DECISIONS #93): not from the document
    grade: "8.5"                        # text, exactly as written (e.g. "7,5", "A-", "Pass"), or null
    credits: 6.0                        # number as written (ECTS or US credits), or null
    term: "2024-2025 block 2"           # free text, or null
    lecturers:                          # names as written; the first is the listed-first lecturer
      - Prof. dr. A. van den Berg
      - Dr. J. Smit
    description: |                      # verbatim, up to 20,000 characters, or empty
      ...
    source_document: transcript-2026-11-06.txt   # file in sources/, or null for a course added by hand
    confirmed: true                     # true when accepted in the import preview; false for courses added
                                        # later without checking
    edited: [description, grade]        # optional: fields the owner changed on /courses/{id}/edit, in form
                                        # order; once listed, always listed (DECISIONS #94)
    deleted: false                      # optional: true when the owner deleted the course; kept so its id is
                                        # never reused and it can be brought back (DECISIONS #94)
    merged_into: null                   # optional: the course a deleted copy was merged into (DECISIONS #95)
```
- Courses are written only when the user accepts rows in the import preview (per row or all).
- `sources/<name>.txt` (`<original file stem>-<YYYY-MM-DD>.txt`, or `pasted-text-…`; never overwritten) holds the **identifier-stripped** text of an imported document (docs/02 §10), with a
  header comment giving the original file name and its sha256. The original file (.docx, .pdf) is never stored,
  and no file under data_dir contains a student number, date of birth or national ID number.
- Professors link to courses through `course_ids` (§4); a course can be linked to several professors.
- **Edit, delete, restore (DECISIONS #94):** the owner may change name, code, grade, credits, term, institution,
  lecturers and description; each changed field joins `edited` (values are then the owner's, not the
  transcript's). A course linked by a professor can't be deleted until it is unlinked. A deleted course stays in
  the file with `deleted: true`, is left out of every page, suggestion and email payload, and its id is never
  given to a new course; restoring clears `deleted` and `merged_into`, and is refused while an active course is the same course by the merge rule below (DECISIONS #98). Saves go through the store (shape and comments kept; an
  unchanged save writes nothing).
- **Merge (DECISIONS #95):** courses are the same course when both have a code and the codes match (case, spaces
  and punctuation ignored), or, when not both have one, their names match (case and punctuation ignored); the
  institutions must be equal or one empty. A merge keeps one course (its id); a field filled in one copy only is
  taken from it; where copies differ the owner picks, and a picked field joins `edited`; every professor's
  `course_ids` pointing at a merged-away copy is repointed to the kept course (no duplicates); the other copies
  get `deleted: true` and `merged_into`. At import, a row matching a stored course fills only that course's
  empty fields (from a transcript, so not owner-edited), and the row itself is stored as a retired copy of its
  document (`source_document` that document, `deleted: true`, `merged_into` the stored course, lecturers as the
  document writes them; not added again when the same document is merged again; DECISIONS #96).
- **A document's "Create professors" step (DECISIONS #96)** lists the lecturers of its confirmed active courses
  and of its copies merged into another course (at import or on /courses/merge), each copy's lecturers linked
  to the active course its `merged_into` chain ends at. Courses deleted without a merge are left out.

## 11. logs/ai_usage.jsonl: AI usage log (DECISIONS #60)
One JSON object per line, appended by `run_skill` for every AI call, whether it succeeds or fails. Lines
are never rewritten and the file doesn't rotate (about 250 bytes per call). The app's calls
(`source: app`) go here, at `claude.usage_log` relative to data_dir, so they are backed up with the data.
Eval and live calls go to the run log `claude.run_usage_log` = `reports/ai_usage.jsonl` (relative to the
repository, gitignored), with the same fields.

| Field | Type | Notes |
|---|---|---|
| `at` | str | ISO 8601 with UTC offset, when the call ended |
| `skill` | str | e.g. `rec-email` |
| `backend` | str \| null | `cli`; null on old lines imported without one |
| `action` | enum | `generate_email`, `research_programme`, `reresearch_programme`, `import_courses`, `evaluate_essay`, `evaluate_cv`, `grade_claims`, `live_check`; `unlabelled` only on imported lines |
| `subject` | object | **IDs only**: `professor_id`, `programme_ids` (list), `programme_id`, `essay_id`, `cv_id`; every value matches the ID slug pattern; `{}` when there is no stored item (researching a new programme, a course import, live checks) |
| `source` | enum | `app` (this file), `eval` / `live` (run log); `unknown` only on imported lines |
| `duration_s` | float \| null | wall time of the call; null only on imported lines that had none |
| `total_cost_usd` | float \| null | the CLI's estimate; null when it reported none (failed calls) |
| `ok` | bool | |
| `error` | enum \| null | `not_installed`, `timeout`, `failed`, `invalid_output` |
| `imported` | bool | only on lines copied by `hub import-usage`, always `true` |

- No other field exists. A line never holds payload content, email text, essay text, biography text or a
  research query. Professor IDs are slugs of the name (`jansen`), the same IDs as the file names in data_dir;
  no name is written as text. `run_skill` refuses a subject that isn't IDs only before anything runs
  (whole-string match: a trailing newline is refused too).
- The reader is strict: JSON types without coercion, no extra field, `skill` an ID slug, `backend` `cli` or
  null, finite non-negative numbers, `ok` true exactly when `error` is null, and `unlabelled` / `unknown` /
  `imported` only together. Any other line is unreadable: the page names its file, line number and the field,
  never the field's value.
- A call that fails with anything other than the four `SkillError` kinds (a bug, a broken pipe) is still logged,
  as `failed`, and the error is raised again.
- App lines are written only if data_dir exists; the data folder is never created by logging. A line that
  can't be written is skipped, with a warning in `hub.log`.
- `hub import-usage` copies the lines of the old single log (`reports/ai_usage.jsonl` lines without
  `source`, from before #60) into this file once, as `action: unlabelled`, `subject: {}`, `source: unknown`,
  `imported: true`. The old file is left as it is; the reader ignores its old lines from then on. Running the
  command again does nothing.
- On any failure path the only changes in data_dir are exactly one appended line here and, when the same
  request saved a data file first, that file and exactly one appended `hub.log` line naming it (C-2b-05;
  docs/04 §1.1). Two edge cases outside the tests: if this line can't be written, a `hub.log` warning appears
  instead, and a `hub.log` that rotates on the save line gains `hub.log.1`.
