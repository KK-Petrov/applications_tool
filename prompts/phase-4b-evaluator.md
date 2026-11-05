# Phase 4b: AI essay and CV evaluator

## Goal
"Evaluate" gives rubric feedback (Structure, Content fit, Programme fit, Accuracy) that finds planted
flaws, points at paragraphs, and never rewrites the user's text.

**Recommended:** 1–2 sessions · high effort. If the phase won't finish within that, stop at a clean point and run `/handoff`.

## Read first
docs/04_ai_features.md §1, §5, §6; docs/02_domain_rules.md §7–8; docs/03_ui_spec.md §2 (US-15), §4.5;
config/project.yaml → `evaluation`.

## Tasks
1. **Payload.** Paragraph-numbered text, programme data (curriculum, prompt, limit, AI policy), biography,
   and the exact checks from 4a (docs/04 §5.1).
2. **Skill.** `.claude/skills/evaluate/SKILL.md` with the rules of docs/04 §5.3 and one worked example; schema
   `src/hub/ai/schemas/evaluate.json`.
3. **Eval set.** `tests/evals/essay/` with 10 planted-flaw essays, 3 clean essays and `flaws.yaml` (docs/04 §6).
4. **Graders.** schema, planted-flaw detection, rewrite violations, clean-essay false alarms;
   `tasks.py eval-essay` → `reports/eval_essay.json` and `.md` (C-4b-01 to C-4b-04).
5. **UI.** Evaluate button; results panel with rating chips, reasons, comments linked to paragraphs (click
   scrolls to the paragraph), unused biography suggestions; result saved as `vN.eval.json` (US-15).
6. **Discipline.** Tune against 7 flaw essays + 2 clean; hold out 3 + 1 and report them separately. Never edit
   graders, cases or `flaws.yaml` to make a run pass.

## Tests first
In your plan, map every automated criterion below to the test or script that checks it and the command
that runs it. After approval, write those tests **before** the implementation and show that they fail
for the right reason. Never edit a test to make it pass without saying so and getting approval.

## Acceptance criteria
Fixed at the design freeze (`config/criteria.json`) and guarded; never lowered without the user's approval. Criteria of earlier phases must keep passing.
- [ ] **C-4b-01** Every evaluation validates against its schema and rates all four rubric criteria with a reason — `evaluation_schema_failures == 0`. *Verify:* `uv run python tasks.py eval-essay` → reports/eval_essay.json: schema_failures. *(automated, gate)*
- [ ] **C-4b-02** Planted flaws detected in the essay eval set — `planted_flaws_detected >= 8 of 10`. *Verify:* `uv run python tasks.py eval-essay` → reports/eval_essay.json: planted_flaws_detected. Must beat: the exact checks alone catch only the word-limit and wrong-school flaws (2 of 10). *(automated, gate)*
- [ ] **C-4b-03** The evaluator never rewrites: no comment exceeds 60 words or contains a replacement paragraph. *Verify:* `uv run python tasks.py eval-essay` → reports/eval_essay.json: rewrite_violations. *(automated, gate)*
- [ ] **C-4b-04** False alarms on clean essays (accuracy rated weak on a clean essay). *Verify:* `uv run python tasks.py eval-essay` → reports/eval_essay.json: clean_essay_false_alarms. *(reported, not gated)*
- [ ] **C-4b-05** Three evaluations of the user's own drafts are useful and fair. *Verify:* phase 4b prompt, You check. *(human review: you confirm, gate)*

## Reviewer
Run **ai-output-reviewer** at the end.

## You check
- C-4b-05: evaluate three of your own drafts. Is each rating fair? Did it find something you had missed?
  Did any comment try to rewrite your sentences?

## Out of scope
Rewriting or generating essays (never); programme research (5).
