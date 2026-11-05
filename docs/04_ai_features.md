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
Command (argv list, never a shell string):
```
{claude.path} -p "/{skill} The input is the JSON document on stdin." \
  --output-format json --json-schema '{schema JSON}' \
  --permission-mode dontAsk [--allowedTools "WebSearch,WebFetch"]
```
- `cwd` = repository root, so project skills load. Do **not** use `--bare`: bare mode ignores the
  subscription login and skills.
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
- Verify in Phase 2b with one live call that a user-invoked skill named at the start of a `-p` prompt is
  expanded; if not, fall back to `--append-system-prompt-file .claude/skills/{skill}/SKILL.md` and log a decision.

## 2. Skill: `/rec-email`
### 2.1 Input payload (built in Python by `src/hub/ai/payloads.py`)
```json
{"owner": {"name": "…"},
 "professor": {"name": "Prof. Mulder", "surname": "Mulder", "university": "RSM", "course": "…",
               "course_description": "(verbatim text)", "grade": null, "term": null, "interaction": null, "tone": "formal"},
 "programmes": [{"short_name": "Princeton MFin", "full_name": "Princeton Master in Finance",
                 "deadline_text": "15 December 2026, 11:59 pm ET", "submission": "via the Graduate School portal",
                 "letters_note": "…"}],
 "biography": [{"id": "gmat", "category": "test", "title": "…", "detail": "…", "state": "actual"}],
 "constraints": {"min_words": 180, "max_words": 280}}
```
`deadline_text` is formatted in Python in the **school's** zone; the model must copy it exactly.
Only programmes ticked for this letter are included.

### 2.2 Rules in SKILL.md
1. Formal register; open with exactly `Dear Professor {surname},`; close with `Kind regards,` then the owner's name.
2. Structure: (a) who the owner is and the course taken with the professor (term, grade if given);
   (b) what he did or learned in the course, drawn from the course description and the interaction;
   (c) why these programmes, from goal/experience entries; (d) the programmes and deadlines as a short
   list, copied exactly; (e) offer of CV, transcript and a short summary of points the professor could
   speak to; ask whether the professor feels able to write a strong letter; thanks.
3. 180–280 words in the body. Subject: `Request for a letter of recommendation: {programme type} applications`.
4. Use only facts in the payload. Never invent grades, projects, conversations, rankings or praise.
   Anticipated entries are written as anticipated ("I expect to graduate cum laude").
5. If the payload lacks a grade or interaction, write around it and add a warning such as
   "No specific interaction given; paragraph (b) relies on the course description only."
6. Never mention programmes not in the payload.

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
9. two programmes with different zones (ET and CT); 10. professor name with a particle ("Prof. van der Berg", surname "van der Berg").
`tests/evals/email/calibration/` — 3 hand-written emails each containing one invented claim; the grader must flag all 3.

`tasks.py eval-email` runs `/rec-email` once per case (live), then graders in `tests/evals/email/graders.py`:
- `schema_failures`: output fails the schema.
- `fact_failures`: body lacks the professor's surname in the salutation, the course name, or any selected
  programme's `short_name` **and** its exact `deadline_text`; or contains any unselected programme's
  short name, school or alias.
- `form_failures`: body word count (docs/02 §7) outside 180–280; salutation not exactly `Dear Professor {surname},`;
  an anticipated entry stated as achieved (regex for its title without "expect"/"anticipat"/"on track").
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

## 7. Skill: `/research-programme` (Phase 5)
Input: `{"query": "Berkeley Haas MFE", "url": null, "today": "YYYY-MM-DD"}`. Tools: WebSearch, WebFetch.
Output: a programme object matching docs/01 §3 with `verification: unverified`, every filled field paired
with a `source_url` (fields not found are null with a note), deadlines with zone and `time_known`.
The app validates it, shows a preview, and writes `programmes/<id>.yaml` only after the user clicks Save.
Prefer the school's own pages; third-party sources set `verification_note: Third-party source`.

## 8. Usage and cost
Calls happen only on explicit clicks or eval runs. The Settings page shows the month's call count and
summed `total_cost_usd` from `reports/ai_usage.jsonl` (an estimate; subscription usage draws on the
Agent SDK credit).
