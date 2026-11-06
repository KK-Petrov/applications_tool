# Phase 2c: Programme research

## Goal
A new programme is researched from official pages and added in under five minutes, marked unverified with
sources; a stored programme can be re-researched and the user accepts or rejects each change field by field.

**Recommended:** 1–2 sessions · high effort. If the phase won't finish within that, stop at a clean point and run `/handoff`.

## Read first
docs/04_ai_features.md §1, §7 (all of it carefully); docs/01_data_contract.md §3 (incl. `official_domains`);
docs/02_domain_rules.md §6; docs/03_ui_spec.md §1, §2 (US-16, US-18), §4.2, §5;
docs/DECISIONS.md #13, #21, #30, #31, #37; config/project.yaml → `claude`, `research`.

## Tasks
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

## Tests first
In your plan, map every automated criterion below to the test or script that checks it and the command
that runs it. After approval, write those tests **before** the implementation and show that they fail
for the right reason. Never edit a test to make it pass without saying so and getting approval.
Required supporting tests in `tests/test_research_preview.py` (fast, fake Claude):
- a non-official host is refused at preview, and the message names the field;
- a subdomain of an official domain is accepted;
- look-alike hosts (`princeton.edu.evil.com`, `evilprinceton.edu`) are refused;
- an `mba.com` source is accepted;
- re-research on a programme without `official_domains` asks for confirmation before comparing.

## Acceptance criteria
Fixed at the design freeze (`config/criteria.json`) and guarded; never lowered without the user's approval. Criteria of earlier phases must keep passing.
- [ ] **C-5-01** Researching two programmes yields schema-valid files where every filled field has a source URL and the programme is marked unverified. *Verify:* `uv run python tasks.py live -k research`. *(automated, gate)*
- [ ] **C-2c-02** Re-research writes nothing until the user saves; accepted deadline changes reset those deadlines to unverified; rejected changes leave the file byte-identical. *Verify:* `uv run pytest tests/test_research_merge.py tests/e2e/test_research.py -k us18`. *(automated, gate)*

## Reviewer
Run **ai-output-reviewer** and **ui-reviewer** at the end.

## You check
- Research one programme you are considering; open every cited source and check it is the school's own page.
- Re-research one of your programmes, reject one change and accept another; confirm the file shows only the accepted change.

## Out of scope
Usage panel, README and the final end-to-end pass (Phase 5); reminders (3).
