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
`verification: verified` must have at least one source.

## 4. professors/<id>.yaml
```yaml
id: mulder
name: Prof. Mulder                # display name; surname = last token, override with `surname`
surname: Mulder
title: Professor                  # used in "Dear {title} {surname},"; default "Professor", e.g. "Dr."
email: m.mulder@example.org       # optional (null → "Email missing"); never shown in the sheet
university: RSM                   # free text; the form offers RSM / UVA McIntire / Other
course_ids: []                    # links to courses.yaml (§10); with links, course and
                                  # course_description below are not used (they stay for unlinked professors)
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
     marked_sent_on: null}
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
```
- Courses are written only when the user accepts rows in the import preview (per row or all).
- `sources/<name>.txt` holds the **identifier-stripped** text of an imported document (docs/02 §10), with a
  header comment giving the original file name and its sha256. The original file (.docx, .pdf) is never stored,
  and no file under data_dir contains a student number, date of birth or national ID number.
- Professors link to courses through `course_ids` (§4); a course can be linked to several professors.
