# 04: AI features

## 1. How the app calls Claude
The app does not use the Anthropic API. It runs Claude Code in headless mode on the user's subscription.
Each feature is a project skill in `.claude/skills/<name>/SKILL.md`, so the same skill also works in an
interactive session (`claude` → `/rec-email mulder`).

### 1.1 Wrapper: `src/hub/ai/claude_cli.py`
```python
def run_skill(skill: str, payload: dict, schema: dict, *, tools: list[str] | None = None,
              timeout_s: int | None = None) -> SkillResult: ...
# SkillResult: data: dict, cost_usd: float | None, duration_s: float, session_id: str
# Raises SkillError(kind, message) with kind in {not_installed, timeout, failed, invalid_output}
```
The app calls `hub.ai.skills.run_skill` (same signature plus `config=`), which hands the call to the
backend named by `claude.backend` (§1.2); `src/hub/ai/claude_cli.py` is the `cli` backend.
Command (argv list, never a shell string), locked down (DECISIONS #47):
```
{claude.path} -p "The input is the JSON document on stdin." \
  --append-system-prompt-file .claude/skills/{skill}/SKILL.md --setting-sources "" \
  --output-format json --json-schema '{schema JSON}' \
  --permission-mode dontAsk --permission-prompts none \
  --tools "{the skill's tools, e.g. WebSearch,WebFetch; empty for rec-email}" \
  --disallowedTools "{every other tool: Bash, PowerShell, Edit, Write, Read, …}" \
  --strict-mcp-config --settings '{"disableAllHooks": true}' --no-session-persistence \
  --max-turns {claude.max_turns}
```
No settings file applies to these calls (not the project's `.claude/settings.json`), so project skills
don't load either: the skill reaches the model as its SKILL.md (`--append-system-prompt-file`). The Phase 2b
live check proved that a call can't run a command or write a file, and that a control run without these
flags can (`uv run python tasks.py live -k "skill_expansion or lockdown"`).
- `cwd` = repository root. No settings source is loaded, so project skills don't load; the skill's
  SKILL.md is passed with `--append-system-prompt-file` (DECISIONS #47). Do **not** use `--bare`: bare mode
  ignores the subscription login.
- stdin = `json.dumps(payload)` (well under the 10 MB stdin cap).
- `claude.path` from config; default resolves `claude` via `shutil.which` (on Windows this may be `claude.cmd`).
- Parse stdout JSON; the result is `structured_output`. Missing, `is_error: true`, or failing local
  jsonschema validation → `SkillError("invalid_output")`. Non-zero exit → `failed` with stderr's first line.
  Timeout (`claude.timeout_s`, 120; research 300) → kill the process tree, `timeout`.
- Log one line per call to `reports/ai_usage.jsonl`: skill, duration, `total_cost_usd`, ok/error. Never log payloads.
- Nothing is written to `data_dir` unless `run_skill` returns successfully (C-2b-05).
- Tests replace the binary with `tests/fakes/fake_claude.py` (via `claude.path`), which replays recorded
  outputs from `tests/fakes/recordings/` and can simulate each failure. Live calls only under the `live`
  marker and in `tasks.py eval-*`.
- Verified in Phase 2b: a skill named at the start of `-p` expands only when project settings load; the
  lockdown loads none, so the fallback `--append-system-prompt-file` is used (DECISIONS #47).

### 1.2 Backends (DECISIONS #50)
`run_skill` (`src/hub/ai/skills.py`) is the only AI entry point the app imports, with `SkillResult` and
`SkillError`. It checks the skill's tool allow-list (`SKILL_TOOLS`), calls the backend named by
`claude.backend`, validates the reply against the schema, words the four errors and writes the usage log.
A backend implements `call(skill, payload, schema, tools, timeout_s, config) -> BackendReply` (the
structured output, cost, session id, its raw result) and raises `SkillError` through the shared helpers
(`not_installed`, `timed_out`, `failed`, `invalid_output`). Only `cli` exists. An `api` backend would plug in
like this, without changing callers: SKILL.md as the system prompt; the payload (JSON) as the user message;
the schema as the structured-output format; tools only from `SKILL_TOOLS` (WebSearch/WebFetch as server
tools for research-programme, nothing for the others); a timeout mapped to `timed_out`; API errors mapped to
`failed`; a missing key or package to `not_installed`. It must pass `tests/test_ai_backend_contract.py`
(payload + schema → validated dict; only the four error kinds, each ending "Nothing was saved."; a usage line
without the payload; nothing written to data_dir on failure) after adding a harness there.

## 2. Skill: `/rec-email`
### 2.1 Input payload (built in Python by `src/hub/ai/payloads.py`)
```json
{"owner": {"name": "…"},
 "professor": {"name": "Prof. Mulder", "title": "Professor", "surname": "Mulder", "university": "RSM",
               "courses": [{"name": "…", "code": null, "description": "(verbatim text)", "grade": null,
                            "term": null, "credits": null}],
               "interaction": null, "tone": "formal"},
 "programmes": [{"short_name": "Princeton MFin", "full_name": "Princeton Master in Finance",
                 "deadline_text": "15 December 2026, 11:59 pm ET", "submission": "via the Graduate School portal",
                 "letters_note": "…"}],
 "biography": [{"id": "gmat", "category": "test", "title": "…", "detail": "…", "state": "actual"}],
 "constraints": {"min_words": 180, "max_words": 280,
                 "subject": "Request for a letter of recommendation: master's programmes in finance",
                 "sign_off": "Kind regards,"}}
```
`deadline_text` is formatted in Python in the **school's** zone (`email.deadline_format`); the model must copy
it exactly. A deadline whose time is not confirmed is given as its date only ("7 January 2027"), with a
warning (DECISIONS #48). `submission` is null (no stored field says how letters are submitted). The subject
is built in Python from `email.subject_template` and `email.programme_type` and copied by the model.
Only programmes ticked for this letter are included. `courses` holds every course linked through
`course_ids` (docs/01 §10), or the professor's manual course and description when none is linked
(DECISIONS #44). `surname` is the salutation form (docs/02 §9: "Van den Berg" standing alone).

### 2.2 Rules in SKILL.md
1. Formal register; open with exactly `Dear {title} {surname},` (e.g. `Dear Dr. De Vries,`); close with `Kind regards,` then the owner's name.
2. Structure: (a) who the owner is and the course taken with the professor (term, grade if given);
   (b) what he did or learned in the course, drawn from the course description and the interaction;
   (c) why these programmes, from goal/experience entries; (d) the programmes and deadlines as a short
   list, copied exactly; (e) offer of CV, transcript and a short summary of points the professor could
   speak to; ask whether the professor feels able to write a strong letter; thanks.
3. 180–280 words in the body. Subject: `constraints.subject`, i.e. `Request for a letter of recommendation:
   master's programmes in finance` (DECISIONS #48).
4. Use only facts in the payload. Never invent grades, projects, conversations, rankings or praise.
   Anticipated entries are written as anticipated ("I expect to graduate cum laude").
5. If the payload lacks a grade or interaction, write around it and add a warning such as
   "No specific interaction given; paragraph (b) relies on the course description only."
6. Never mention programmes not in the payload.
7. Word the opening and closing sentences anew for each professor; no fixed stock sentence; structure and
   facts unchanged. Months are written in full ("from January to June 2026"), never changing the date
   (DECISIONS #49).

### 2.3 Output schema (`src/hub/ai/schemas/rec_email.json`)
```json
{"type": "object", "additionalProperties": false,
 "required": ["subject", "body", "used_biography_ids", "warnings"],
 "properties": {"subject": {"type": "string", "maxLength": 140},
                "body": {"type": "string"},
                "used_biography_ids": {"type": "array", "items": {"type": "string"}},
                "warnings": {"type": "array", "items": {"type": "string"}}}}
```

## 3. Skill: `/grade-claims` (eval only)
Input: `{"text": "(email body)", "facts": (the rec-email payload)}`. Output: `{"unsupported_claims":
[{"quote": "…", "reason": "…"}]}`. A claim is unsupported if no payload field states or directly implies
it. Courtesy phrases ("I greatly enjoyed your course") are not claims; factual statements about
performance, projects, contact or achievements are.

## 4. Email eval set and graders (C-2b-01 to C-2b-04)
`tests/evals/email/cases/` — 10 invented professors at example.org with an invented owner biography:
1. rich record (grade, term, interaction), one programme; 2. no grade, no interaction; 3. all four
programmes; 4. warm tone; 5. 3,000-character pasted description; 6. biography with an **anticipated**
cum laude; 7. very short description (sparse, tempts invention); 8. UVA professor, term "Fall 2026";
9. two programmes with different zones (ET and CT); 10. professor name with a particle ("Prof. van der Berg", surname "van der Berg"; salutation
"Dear Professor Van der Berg,"). Every case sets `title` explicitly; case 8 uses `"Dr."`.
`tests/evals/email/calibration/` — 3 hand-written emails each containing one invented claim; the grader must flag all 3.

`tasks.py eval-email` runs `/rec-email` once per case (live), then graders in `tests/evals/email/graders.py`:
- `schema_failures`: output fails the schema.
- `fact_failures`: body lacks the professor's surname in the salutation, the course name, or any selected
  programme's `short_name` **and** its exact `deadline_text`; or contains any unselected programme's
  short name, school or alias.
- `form_failures`: body word count (docs/02 §7) outside 180–280; salutation not exactly `Dear {title} {surname},`;
  an anticipated entry stated as achieved (regex for its title without "expect"/"anticipat"/"on track");
  an abbreviated month name (Jan, Feb, …, Sept, Dec) other than a word of the professor's or owner's name
  (DECISIONS #49).
- `subject_mismatches` (reported, not gated): the subject differs from `constraints.subject`.
- Payloads are built by the app's builder from the cases and frozen programme copies in
  `tests/fixtures/email_eval/` (DECISIONS #48); cases 8–10 are held out from tuning and reported separately.
- `unsupported_claims`: total from `/grade-claims` over the 10 emails; `grader_calibration_passed`: all 3 calibration emails flagged.
Writes `reports/eval_email.json` and a readable `reports/eval_email.md` with each email for the human check.

## 5. Skill: `/evaluate` (essays and CVs)
### 5.1 Input payload
`{"kind": "essay|cv", "programme": {short_name, full_name, school, curriculum, essay prompt, word_limit, ai_policy},
"text": "<draft with paragraph numbers [P1] … prefixed>", "biography": [...], "exact_checks":
{"word_count": n, "word_limit": n|null, "wrong_school_hits": [...]}}`.
### 5.2 Output schema (`src/hub/ai/schemas/evaluate.json`)
```json
{"type": "object", "additionalProperties": false, "required": ["criteria", "unused_biography", "summary"],
 "properties": {
  "criteria": {"type": "array", "minItems": 4, "maxItems": 4, "items": {"type": "object",
    "additionalProperties": false, "required": ["name", "rating", "reason", "comments"],
    "properties": {"name": {"enum": ["structure", "content_fit", "programme_fit", "accuracy"]},
      "rating": {"enum": ["strong", "adequate", "weak"]}, "reason": {"type": "string", "maxLength": 400},
      "comments": {"type": "array", "items": {"type": "object", "additionalProperties": false,
        "required": ["paragraph", "comment"], "properties": {"paragraph": {"type": "integer"},
        "comment": {"type": "string"}}}}}}},
  "unused_biography": {"type": "array", "items": {"type": "object", "required": ["id", "why_it_fits"],
     "properties": {"id": {"type": "string"}, "why_it_fits": {"type": "string"}}}},
  "summary": {"type": "string", "maxLength": 500}}}
```
### 5.3 Rules in SKILL.md
- Structure: does it answer the prompt as asked, is there an arc, does every paragraph do work; the word
  count verdict comes from `exact_checks`, never recounted.
- Content fit: which biography entries are used; which unused entries fit this programme better (`unused_biography`).
- Programme fit: references to courses, tracks, features; flag any that are not in the stored curriculum.
- Accuracy: claims not supported by the biography; anticipated entries stated as achieved; wrong-school hits
  from `exact_checks` repeated with paragraph numbers.
- Never rewrite: comments point at the problem and the kind of fix ("name the course you would take and why"),
  at most 60 words each, no replacement sentences or paragraphs.

## 6. Essay eval set (C-4b-01 to C-4b-04)
`tests/evals/essay/` — invented owner biography and 13 essays for invented programme data:
10 essays with exactly one planted flaw each (doesn't answer the prompt; over the word limit; a course
not in the curriculum; an unsupported claim; an anticipated honour stated as achieved; a competitor
school named; a paragraph that restates the CV only; no reason for this programme; key quantitative
experience omitted while a weaker one is used; conclusion contradicts the opening) and 3 clean essays.
`flaws.yaml` maps each essay to the criterion and paragraph where the flaw must be reported.
`tasks.py eval-essay` reports `schema_failures`, `planted_flaws_detected` (flaw counted when the right
criterion is rated adequate/weak **and** a comment or reason references it; for content-fit omissions,
the omitted entry appears in `unused_biography`), `rewrite_violations` (comment > 60 words, or any comment
containing a sentence of ≥ 12 words in quotation marks not present in the draft), and
`clean_essay_false_alarms` (accuracy rated weak on a clean essay). Output `reports/eval_essay.json` and `.md`.

## 7. Skill: `/research-programme` (Phase 2c)
Input: `{"query": "Berkeley Haas MFE", "url": null, "today": "YYYY-MM-DD"}`. Tools: WebSearch, WebFetch.
Output: a programme object matching docs/01 §3 with `verification: unverified`, every filled field paired
with a `source_url` (fields not found are null with a note), deadlines with zone and `time_known`.
The app validates it, shows a preview, and writes `programmes/<id>.yaml` only after the user clicks Save.
**Official sources only (DECISIONS #31, #37).** The skill cites only the university's or school's own pages and
proposes the programme's `official_domains`. A field no official page provides stays null with the note
"No official source found"; the skill never falls back to a third-party source. The app checks every cited
URL in Python: its host must equal, or be a subdomain on a dot boundary of, one of the confirmed
`official_domains` (`business.columbia.edu` matches `columbia.edu`; `princeton.edu.evil.com` and
`evilprinceton.edu` do not), or be on `config.research.official_shared_domains` (e.g. `mba.com` for GMAC).
Any other source is refused at preview with a message naming the field, and Save stays blocked. The preview
first shows the proposed `official_domains` for the user to confirm or edit; only then do the checks run.
Save is also blocked until a deadline with `tz` and `source_url` exists (DECISIONS #13).

### 7.1 Re-research
Input: `{"programme_id": "...", "current": {stored programme}, "today": "YYYY-MM-DD"}`. The "Re-research" button
on a programme page runs the skill for that programme; the app shows a field-by-field comparison with the
stored data (new / changed / unchanged, each with its source URL). The user accepts or rejects each change;
nothing is written until Save, and a save with every change rejected writes nothing. Accepted changes to a
deadline's date, time, zone or source reset that deadline to unverified (DECISIONS #21, #30); research never
sets `verified`. A programme without `official_domains` gets them prefilled from the hosts of its stored
source URLs, and the user confirms them before the comparison runs. Saving keeps the file's shape and
comments (DECISIONS #29b).

## 8. Usage and cost
Calls happen only on explicit clicks or eval runs. The Settings page shows the month's call count and
summed `total_cost_usd` from `reports/ai_usage.jsonl` (an estimate; subscription usage draws on the
Agent SDK credit).

## 9. Skill: `/extract-courses` (Phase 2c, DECISIONS #44)
Input: `{"text": "(identifier-stripped document text)", "institution_hint": null}` (docs/02 §10). No tools.
Output (`src/hub/ai/schemas/extract_courses.json`):
```json
{"type": "object", "additionalProperties": false, "required": ["courses", "warnings"],
 "properties": {"courses": {"type": "array", "items": {"type": "object", "additionalProperties": false,
   "required": ["name", "code", "institution", "grade", "credits", "term", "lecturers", "description"],
   "properties": {"name": {"type": "string"}, "code": {"type": ["string", "null"]},
     "institution": {"type": ["string", "null"]}, "grade": {"type": ["string", "null"]},
     "credits": {"type": ["number", "null"]}, "term": {"type": ["string", "null"]},
     "lecturers": {"type": "array", "items": {"type": "string"}},
     "description": {"type": "string"}}}},
   "warnings": {"type": "array", "items": {"type": "string"}}}}
```
Rules in SKILL.md: copy names, codes, grades and credits exactly as written; lecturers as written, in the
document's order; never infer, round or convert a grade; a field not in the document is null; never output a
student number, date of birth or national ID number. Python then strips identifiers again, checks every grade
verbatim against the source text (docs/02 §10), validates the schema and shows the preview; nothing is written
until the user accepts.
Eval set `tests/evals/courses/`: an invented 28-course transcript (invented student, institutions and lecturers)
as .docx, text PDF and pasted text, with identifiers in several formats in English and Dutch, course codes and
other numbers that resemble IDs, and `expected.json`. `tasks.py live -k courses` grades: every course found with
exact name, grade and credits; 0 false grades; no identifier in the output or in data_dir; every lecturer listed
once with all their courses (C-2c-03).
