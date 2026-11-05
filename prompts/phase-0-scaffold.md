# Phase 0: Scaffold

## Goal
A runnable, tested skeleton: the package layout, the task runner, every data model with its loader,
the seed programmes, and an empty app with the sidebar, so later phases only add features.

**Recommended:** 1 session · medium effort. If the phase won't finish within that, stop at a clean point and run `/handoff`.

## Read first
CLAUDE.md; docs/00_design_brief.md; docs/01_data_contract.md (all); docs/03_ui_spec.md §1, §3, §6;
config/project.yaml; seed/programmes/*.yaml.

## Tasks
1. **Project setup.** `pyproject.toml` (Python 3.12; deps: fastapi, uvicorn, jinja2, python-multipart,
   pydantic, ruamel.yaml, tzdata, jsonschema, google-api-python-client, google-auth-oauthlib, typer;
   dev: pytest, pytest-playwright, axe-playwright-python, ruff). Console script `hub = hub.cli:app`.
   `uv sync` must work on Windows.
2. **tasks.py.** Targets `dev`, `init-data`, `test-fast`, `test`, `e2e`, `live`, `eval-email`, `eval-essay`,
   `lint`, `validate` exactly as in CLAUDE.md (eval and live targets may print "not yet implemented" until
   their phase). `test-fast` = `pytest -m "not e2e and not live" -q`. Cross-platform: no shell-specific syntax.
3. **Config loader.** `src/hub/config.py` reads `config/project.yaml` into a typed object; env var
   `HUB_DATA_DIR` overrides `storage.data_dir` (used by tests and handy for the user).
4. **Models and store.** Pydantic models for every file in docs/01 §2–8 and `src/hub/store.py` with atomic,
   comment-preserving writes and `DataError(path, field, message)` (docs/01 §9). Tests: every seed file
   validates; a broken fixture yields an error naming file and field (C-0-04).
5. **init-data.** `hub init-data` per docs/01 §9; never overwrites; asks for owner name and email.
6. **App shell.** FastAPI app, base template with sidebar and header (date, zone selector placeholder),
   `static/css/tokens.css` with every token from docs/03 §6, vendored `static/htmx.min.js`, empty pages for
   each sidebar link, DataError banner.
7. **Privacy guard.** `.gitignore` covers `data/`, `.secrets/`, `reports/`; `tests/test_privacy.py` per C-0-03;
   test fixtures under `tests/fixtures/data/` use invented people at example.com/example.org.
8. **Test harness.** pytest config with markers `e2e` and `live`; a fixture that builds a temp `data_dir`
   from fixtures and starts the app on a free port for Playwright; one smoke e2e test that loads `/`.

## Tests first
In your plan, map every automated criterion below to the test or script that checks it and the command
that runs it. After approval, write those tests **before** the implementation and show that they fail
for the right reason. Never edit a test to make it pass without saying so and getting approval.

## Acceptance criteria
Fixed at the design freeze (`config/criteria.json`) and guarded; never lowered without the user's approval. Criteria of earlier phases must keep passing.
- [ ] **C-0-01** From a fresh clone on Windows, setup and the full test suite pass. *Verify:* fresh clone, then `uv sync && uv run playwright install chromium && uv run python tasks.py test`. *(automated, gate)*
- [ ] **C-0-02** Fast tests finish quickly — `test_fast_seconds < 60 s`. *Verify:* time `uv run python tasks.py test-fast`. *(automated, gate)*
- [ ] **C-0-03** No personal data or secrets in the repository; the data folder and token files are outside Git. *Verify:* `uv run pytest tests/test_privacy.py` (scans tracked files for email addresses other than example.com/example.org, tokens, and data/ paths; checks .gitignore). *(automated, gate)*
- [ ] **C-0-04** Every seed file validates against the schema; an invalid file produces an error naming the file and field. *Verify:* `uv run pytest tests/test_schema.py`. *(automated, gate)*

## Reviewer
No reviewer for this phase; run `/validate`.

## You check
- Run `uv run python tasks.py dev`, open http://127.0.0.1:8765 and see the sidebar and empty pages.
- Set `storage.data_dir` in config/project.yaml to your OneDrive folder, run `uv run hub init-data`,
  and check that four programme files appear there.

## Out of scope
Any real page content (Phase 1 onwards), Google, Claude calls.
