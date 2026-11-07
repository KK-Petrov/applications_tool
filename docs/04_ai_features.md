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
  --max-turns {claude.max_turns, or claude.research_max_turns for a skill with web tools} \
  --allowedTools "{the skill's tools; the flag is left out for a skill without tools}"
```
`dontAsk` denies every tool that isn't approved, even one listed in `--tools`, so a skill's own tools (and only
those) are approved by name with `--allowedTools`. Skills that browse need many dependent turns, so they get
`claude.research_max_turns` (40). Both were proved live in Phase 2c (DECISIONS #61): without `--allowedTools`,
WebSearch and WebFetch were denied; a cap of 2 stopped four dependent fetches (`error_max_turns`); under these
flags the session had only WebSearch, WebFetch and StructuredOutput, and a control run without the lockdown
did run its shell command and write its file (`uv run python tasks.py live tests/test_claude_live.py -k
"web_tools or web_turn"`).
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
- Log one line per call (§8; docs/01 §11): skill, action, subject (IDs only), source, duration,
  `total_cost_usd`, ok/error. Never payloads or outputs. App calls go to `<data_dir>/logs/ai_usage.jsonl`, eval
  and live calls to `reports/ai_usage.jsonl`.
- On any failure path the only changes in data_dir are exactly one appended line in logs/ai_usage.jsonl and,
  when the same request saved a data file first, that file and exactly one appended hub.log line naming it;
  nothing else changes (C-2b-05; the check was made stricter by DECISIONS #60, wording and threshold unchanged).
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
with action, subject IDs and source but without the payload; on failure nothing in data_dir changes but that
one appended line) after adding a harness there.

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
                 "letters_note": "…",
                 "curriculum": {"summary": "…", "courses": [{"name": "…", "description": "…"}]}}],
 "biography": [{"id": "gmat", "category": "test", "title": "…", "detail": "…", "state": "actual"}],
 "selected_biography_ids": ["ra-empirical", "gmat", "python"],
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
(DECISIONS #44). With links, a manual description fills (a) the linked course whose name (or "name
(code)") equals the manual course, ignoring case, else (b) the only linked course without a description, else (c) it is
sent as its own course entry and the draft gets the warning "Check which course this description belongs to."; the
stored courses never change (DECISIONS #66). `surname` is the salutation form (docs/02 §9: "Van den Berg" standing alone).
From Phase 4a (DECISIONS #59): `curriculum` is the programme's stored curriculum, with bracketed editorial
notes ("[Course list: run research]") removed from the summary; `biography` holds every entry and
`selected_biography_ids` names the 3–5 chosen in Python (§2.4), most relevant first.

### 2.2 Rules in SKILL.md
1. Formal register; open with exactly `Dear {title} {surname},` (e.g. `Dear Dr. De Vries,`); close with `Kind regards,` then the owner's name.
2. Structure: (a) who the owner is and the course taken with the professor (term, grade if given);
   (b) what he did or learned in the course, drawn from the course description and the interaction;
   (c) why these programmes: the course-to-programme link (rule 8) and the selected entries (rule 9);
   (d) the programmes and deadlines as a short list, copied exactly; (e) a brief offer of CV and
   transcript, the question whether the professor feels able to write a strong letter, and thanks, kept
   short to make room for (b) and (c) (DECISIONS #59).
3. 180–280 words in the body. Subject: `constraints.subject`, i.e. `Request for a letter of recommendation:
   master's programmes in finance` (DECISIONS #48).
4. Use only facts in the payload. Never invent grades, projects, conversations, rankings or praise.
   Anticipated entries are written as anticipated ("I expect to graduate cum laude").
5. If the payload lacks a grade or interaction, write around it and add a warning such as
   "No specific interaction given; paragraph (b) relies on the course description only."
6. Never mention programmes not in the payload.
7. Word the opening and closing sentences anew for each professor; no fixed stock sentence; structure and
   facts unchanged. Months are written in full ("from January to June 2026"), never changing the date
   (DECISIONS #49). The opening and closing draw on this professor's course-to-programme link (rule 8),
   not on a generic phrase (DECISIONS #59).
8. **Course-to-programme link** (DECISIONS #59). Link the professor's course to at least one selected
   programme through a programme element: a course name from that programme's `curriculum.courses`, copied
   verbatim, or, when the programme has no stored courses, a phrase of at least 3 words copied verbatim from
   `curriculum.summary` (no course is then named). Name the course topic in words from the professor's course
   name or description. Declare every link in `programme_links`. Never name a course, track or feature of a
   programme that is not in its `curriculum`.
9. **Selected background** (DECISIONS #59). Use at least 2 of the entries in `selected_biography_ids`,
   keeping each entry's key terms (names, numbers, distinctive words) as written; other entries only where
   paragraph (a) needs them.

### 2.3 Output schema (`src/hub/ai/schemas/rec_email.json`)
```json
{"type": "object", "additionalProperties": false,
 "required": ["subject", "body", "used_biography_ids", "programme_links", "warnings"],
 "properties": {"subject": {"type": "string", "maxLength": 140},
                "body": {"type": "string"},
                "used_biography_ids": {"type": "array", "items": {"type": "string"}},
                "programme_links": {"type": "array", "items": {"type": "object", "additionalProperties": false,
                   "required": ["programme", "from_course_topic", "programme_element"],
                   "properties": {"programme": {"type": "string"}, "from_course_topic": {"type": "string"},
                                  "programme_element": {"type": "string"}}}},
                "warnings": {"type": "array", "items": {"type": "string"}}}}
```
`programme_links` is added in Phase 4a (DECISIONS #59).

### 2.4 Checks in Python (`payloads.py`, `email_checks.py`; rule 13; DECISIONS #59, Phase 4a)
The counts (3–5 selected, at least 2 used, summary phrase of at least 3 words) are parameters in
`config/project.yaml → email`.
- **Terms:** case-folded words and numbers, minus a fixed stop-word list.
- **Selection:** score = 2 × terms shared with the professor's linked course names and descriptions
  + 1 × terms shared with the selected programmes' curriculum (course names, descriptions, summary)
  + 1 per tag found in either. Up to 5 entries with a score above 0, highest first, ties by bank order;
  fewer than 3 are topped up to 3 in bank order. An entry with no distinctive term is never selected.
- **Distinctive terms** of an entry: terms of its title and detail found in no other biography entry, the
  professor's course text or the programme data.
- **Used:** a selected entry counts as used when at least 2 of its distinctive terms appear in the body (all
  of them if it has fewer). `used_biography_ids` is not trusted; an ID in it that fails this check is
  reported as `biography_id_mismatches`.
- **Valid link:** `programme` is a selected `short_name`; `from_course_topic` appears in a linked course's
  name or description (ignoring case); `programme_element` is a stored course name of that programme,
  verbatim, or, when the programme has no stored courses, a verbatim summary phrase of at least 3 words;
  topic and element both appear in the body.
- **Invented programme element:** a `programme_element` not found verbatim in that programme's stored
  curriculum as the rule above allows. Python checks declared links only; a course named in the body but
  not declared is left to `/grade-claims` (§3) and the 4th calibration email (§4).
- **In the app** failed checks are warnings next to the draft and never block. A link through an unverified
  programme adds "{short_name}'s curriculum is unverified — check the course named before sending", after
  the deadline warnings and saved with the email (rule 11).

## 3. Skill: `/grade-claims` (eval only)
Input: `{"text": "(email body)", "facts": (the rec-email payload)}`. Output: `{"unsupported_claims":
[{"quote": "…", "reason": "…"}]}`. A claim is unsupported if no payload field states or directly implies
it. Courtesy phrases ("I greatly enjoyed your course") are not claims; factual statements about
performance, projects, contact or achievements are. From Phase 4a the facts include each programme's
`curriculum` (DECISIONS #59): a course or feature named there is supported; one not in it is unsupported.

## 4. Email eval set and graders (C-2b-01 to C-2b-04, C-4a-04)
`tests/evals/email/cases/` — 10 invented professors at example.org with an invented owner biography:
1. rich record (grade, term, interaction), one programme; 2. no grade, no interaction; 3. all four
programmes; 4. warm tone; 5. 3,000-character pasted description; 6. biography with an **anticipated**
cum laude; 7. very short description (sparse, tempts invention); 8. UVA professor, term "Fall 2026";
9. two programmes with different zones (ET and CT); 10. professor name with a particle ("Prof. van der Berg", surname "van der Berg"; salutation
"Dear Professor Van der Berg,"). Every case sets `title` explicitly; case 8 uses `"Dr."`.
`tests/evals/email/calibration/` — 3 hand-written emails each containing one invented claim; the grader must flag all 3.
From Phase 4a a 4th calibration email (DECISIONS #59) names one invented programme course that is in no stored
curriculum and is not declared in `programme_links`. It is reported as `undeclared_course_caught` (true when
`/grade-claims` flags it), not gated; `grader_calibration_passed` stays on emails 1–3 (C-2b-04).

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
- Eval data from Phase 4a (DECISIONS #59): invented curriculum courses in the frozen Princeton, MIT and Columbia
  copies; Booth keeps only an invented summary (the programme with no stored courses); extra invented
  biography entries matching some cases' courses. C-2b-01 to C-2b-04 are re-run on this set.
- `grounding_failures` (C-4a-04): emails failing any of: fewer than 2 selected entries used (§2.4), no valid
  link, any invented programme element, words outside 180–280. Broken down into `biography_unverified`,
  `no_valid_link`, `invented_programme_elements` and `length`; tuning and held-out cases reported separately.
  `biography_id_mismatches` and `undeclared_course_caught` are reported, not gated.
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

**Payload: programme data only** (the call has web tools, so fetched pages could steer it): new research sends
exactly `{query, url, today}`, re-research `{programme_id, current, today}` with `current` the stored programme
file; no biography, professor, course or settings data (`hub.research.research_payload` /
`reresearch_payload`, pinned by tests/test_research_payloads.py). Turn cap `claude.research_max_turns`, timeout
`claude.research_timeout_s`; `--allowedTools WebSearch,WebFetch` (§1.1, DECISIONS #61).

**Output** (`src/hub/ai/schemas/research_programme.json`, DECISIONS #62): `verification` (only `"unverified"`),
`official_domains`, `sources: [{url, title}]`, `warnings`, and the fields below. A one-value field is
`{"value", "source_url", "note"}`: `name`, `short_name`, `school`, `unit`, `aliases` (a list), `portal_url`,
`letters_required` (integer), `letters_note`, `curriculum_summary`, `ai_policy` (the policy text). List items
carry their own `source_url`: `deadlines: [{id, label, due_local, tz, time_known, source_url}]`,
`requirements: [{id, label, note}]`, `essays: [{id, title, prompt, word_limit}]`,
`curriculum_courses: [{name, description}]`; `fee` is `{amount, currency, note, source_url}`. Research does
not fill steps, documents or interviews (the owner's own workflow). `id` is null for a new item and the stored
id for the same item on re-research.

**Checks in Python** (`src/hub/research.py`, rule 13): a filled field without a source, or with a source not on
the confirmed domains, is refused naming it (`"Deadline 1 · Source URL: example-aggregator.com is not an official
source"`, `"Source 3 · URL: …"`); a URL with userinfo, an IP address or a scheme other than http(s) is never
official. Confirmed domains are lower-cased and deduplicated; a single-label name (`edu`) or a public suffix
(`ac.uk`, `edu.au`, `github.io`; offline list from the `publicsuffixlist` package) is refused. Name, short name
and school are required; deadlines are checked like the form (zone, date and time). The new programme is
written with `Store.create_programme` (never over an existing file; a taken id gets `-2`): `verification:
unverified`, `verification_note: "Researched, not checked"`, every deadline unverified, `sources` = the cited
pages with `checked` = today, and "No official source found" in `letters_note` / `fee.note` when that value
is null.

### 7.1 Re-research
Input: `{"programme_id": "...", "current": {…}, "today": "YYYY-MM-DD"}` where `current` holds only what research
compares (DECISIONS #66, user decision; supersedes "the stored programme file"): id, name, short_name, school, unit,
official_domains, aliases, portal_url, deadlines (id, label, due_local, tz, time_known, source_url), letters_required,
requirements (id, label), essays, curriculum, ai_policy, fee (amount, currency) and sources (url, title). Status, steps,
documents, interviews, verification and every free-text note (requirement notes, letters_note, fee.note) stay local:
the owner's own text never reaches the call that has web tools. The comparison runs in Python against the full file. The "Re-research" button
on a programme page runs the skill for that programme; the app shows a field-by-field comparison with the
stored data (new / changed / unchanged, each with its source URL). The user accepts or rejects each change;
nothing is written until Save, and a save with every change rejected writes nothing. Accepted changes to a
deadline's date, time, zone or source reset that deadline to unverified (DECISIONS #21, #30); research never
sets `verified`. A programme without `official_domains` gets them prefilled from the hosts of its stored
source URLs, and the user confirms them before the comparison runs. Saving keeps the file's shape and
comments (DECISIONS #29b).
Details (DECISIONS #62): deadlines, requirements and essays are matched by `id`, then by label/title ignoring
case; curriculum courses by name. Requirements compare their label only: research never sees the owner's requirement notes
(#66), so it never changes them (#67). Any researched change to a deadline's `time_known` resets that deadline (#67).
The portal link itself must be on an official domain, in new research and in re-research. A stored value research didn't find is shown as "Not found by research,
kept" and is never removed. Confirmed domains that differ from the stored ones are a row like any other
(`official_domains`), so rejecting every row writes nothing. A row whose source is not official shows the
refusal and has no Accept box; the other rows can still be accepted. Accepting any non-deadline row sets the
programme to `unverified` with "Researched, not checked" (rule 11); a deadline-only change keeps the
programme's verification (#21) and resets that deadline. A label-only deadline change keeps its
verification. Accepted rows add their source to `sources` (checked today) if it isn't there. Apply is refused
if the file's sha256 changed since the comparison ("The file changed since the comparison. Nothing was
saved.").

## 8. Usage and cost (DECISIONS #60)
Calls happen only on explicit clicks or eval runs. Every `run_skill` call states what it is for with three
required keyword arguments, written to the usage log (docs/01 §11):
- `action`: what the call is for.

  | Action | Feature | Subject |
  |---|---|---|
  | `generate_email` | Generate email (app) | `professor_id`, `programme_ids` |
  | `research_programme` | Research a new programme (2c) | `{}` (the query is never logged) |
  | `reresearch_programme` | Re-research (2c) | `programme_id` |
  | `import_courses` | Course import (2c) | `{}` (the document is never logged) |
  | `evaluate_essay` | Evaluate an essay (4b) | `programme_id`, `essay_id` |
  | `evaluate_cv` | Evaluate a CV (4b) | `cv_id` (`base` or a programme ID) |
  | `grade_claims` | Eval claim grader | `professor_id`, `programme_ids` of the eval case |
  | `live_check` | Live probes | `{}` |

  Eval runs of `/rec-email` use `generate_email` with source `eval`. `unlabelled` is only for imported old lines.
- `subject`: IDs only (`professor_id`, `programme_ids`, `programme_id`, `essay_id`, `cv_id`, each an ID
  slug). Anything else is a `ValueError` before the call runs, so no name, text or query can reach the log.
- `source`: `app` for clicks in the app (→ `<data_dir>/logs/ai_usage.jsonl`, backed up with the data);
  `eval` for `tasks.py eval-*`; `live` for live tests and `tests/fakes/record.py` (both → the run log
  `reports/ai_usage.jsonl`, gitignored).

The **AI usage page** (`GET /usage`, docs/03 §4.9) reads both logs. It shows each call with what it was for
(names resolved and linked; a deleted item shows its ID), duration, estimated cost and result, and totals for
this month and all time by action and by source, with month and source filters. It states: "Estimated
API-equivalent cost. On your subscription this draws from your monthly Agent SDK credit and isn't billed
separately unless usage credits are enabled." Phase 5 adds this month's total on Settings, linking to the page.
`hub import-usage` copies the lines of the old single log once, as `unlabelled` from source `unknown`. Old
lines mix eval runs, live checks and app clicks and can't be told apart, so no source is guessed.

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
against its own row in the source text (row-anchored: on the same line or table row as the course's code or
name; DECISIONS #64, docs/02 §10), validates the schema and shows the preview; nothing is written until the user
accepts. The call is `run_skill("extract-courses", …, action="import_courses", subject={}, source="app")`, with no
tools and `claude.timeout_s`.
Eval set `tests/evals/courses/`: an invented 28-course transcript (invented student, institutions and lecturers)
as .docx, text PDF and pasted text, with identifiers in several formats in English and Dutch, course codes and
other numbers that resemble IDs, and `expected.json`. `tasks.py live -k courses` grades: every course found with
exact name, grade and credits; 0 false grades; no identifier in the output or in data_dir; every lecturer listed
once with all their courses (C-2c-03).
