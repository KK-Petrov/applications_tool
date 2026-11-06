# Phase 2b: Recommendation email generator

## Goal
"Generate email" produces a formal, tailored request the user would send with light edits, carrying the
exact stored deadlines and no invented facts.

**Recommended:** 1–2 sessions · high effort. If the phase won't finish within that, stop at a clean point and run `/handoff`.

## Read first
docs/04_ai_features.md §1–4 (all of it carefully); docs/02_domain_rules.md §1, §7; docs/03_ui_spec.md §2 (US-10), §4.3;
config/project.yaml → `email`, `claude`.

## Tasks
1. **Wrapper.** `src/hub/ai/claude_cli.py` per docs/04 §1.1, with `tests/fakes/fake_claude.py` and recordings;
   failure-path tests for not installed, timeout, non-zero exit, invalid JSON, schema mismatch (C-2b-05).
2. **Live check first.** One manual live call (`uv run python tasks.py live -k skill_expansion`) proving a
   project skill named in the `-p` prompt is expanded and `structured_output` is returned. If not, apply the
   fallback in docs/04 §1.1 and add a DECISIONS.md row before continuing.
3. **Payload builder.** `src/hub/ai/payloads.py`: rec-email payload from professor, ticked programmes
   (deadline_text formatted in the school zone), biography (empty list allowed until 4a; read `biography.yaml`
   if present) and owner.
4. **Skills.** `.claude/skills/rec-email/SKILL.md` with the rules of docs/04 §2.2 and an example input/output;
   `.claude/skills/grade-claims/SKILL.md` per §3. Schemas in `src/hub/ai/schemas/`.
5. **Eval set and graders.** `tests/evals/email/` with the 10 cases and 3 calibration emails of docs/04 §4;
   `graders.py`; `tasks.py eval-email` writing `reports/eval_email.json` and `.md` (C-2b-01 to C-2b-04).
6. **UI.** Generate button on the detail panel and on Add professor; result with subject, body, warnings,
   Copy, Regenerate, "Refine in Claude Code" (copies the command), "Mark as requested" (US-10). The draft is
   saved to `professors/<id>.yaml → emails`; ticked letters move to `draft_ready`.
7. **Prompt iteration discipline.** Tune SKILL.md against cases 1–7; keep cases 8–10 held out and report their
   results separately in `eval_email.md`. Never edit graders or cases to make a run pass.

## Tests first
In your plan, map every automated criterion below to the test or script that checks it and the command
that runs it. After approval, write those tests **before** the implementation and show that they fail
for the right reason. Never edit a test to make it pass without saying so and getting approval.

## Acceptance criteria
Fixed at the design freeze (`config/criteria.json`) and guarded; never lowered without the user's approval. Criteria of earlier phases must keep passing.
- [ ] **C-2b-01** Every generated email validates against its JSON schema — `email_schema_failures == 0`. *Verify:* `uv run python tasks.py eval-email` → reports/eval_email.json: schema_failures. *(automated, gate)*
- [ ] **C-2b-02** Every eval email names the right professor and course, lists each selected programme with its exact stored deadline, and mentions no unselected programme — `email_fact_failures == 0`. *Verify:* `uv run python tasks.py eval-email` → reports/eval_email.json: fact_failures. *(automated, gate)*
- [ ] **C-2b-03** Eval emails are 180–280 words, open with 'Dear {title} {surname},' and never state an anticipated achievement as achieved — `email_form_failures == 0`. *Verify:* `uv run python tasks.py eval-email` → reports/eval_email.json: form_failures. *(automated, gate)*
- [ ] **C-2b-04** No unsupported claims in eval emails, and the claim grader flags all 3 planted calibration emails — `unsupported_claims == 0`. *Verify:* `uv run python tasks.py eval-email` → reports/eval_email.json: unsupported_claims, grader_calibration_passed. *(automated, gate)*
- [ ] **C-2b-05** Failure paths (Claude Code missing, timeout, invalid output) show a clear message and write nothing. *Verify:* `uv run pytest tests/test_claude_cli.py`. *(automated, gate)*
- [ ] **C-2b-06** Five generated emails read as something the user would send. *Verify:* phase 2b prompt, You check. *(human review: you confirm, gate)*

## Reviewer
Run **ai-output-reviewer** at the end.

## You check
- C-2b-06: read five emails in `reports/eval_email.md` and generate one for a real professor. Would you send
  it after light edits? Is the register formal enough? Note anything that sounds unlike you.

## Out of scope
Sending email (never), reminders (3), essay evaluation (4b).
