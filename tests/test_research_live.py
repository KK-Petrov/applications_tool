"""C-5-01 (live): researching two programmes yields schema-valid files where every filled field has a source
URL and the programme is marked unverified. Real ``/research-programme`` calls on the owner's subscription.

    uv run python tasks.py live -k research

For each programme the test checks, in Python:
1. the output matches ``research_programme.json`` (``run_skill`` validates; a failure is a SkillError);
2. ``verification`` is ``unverified`` and the built programme and every deadline are unverified;
3. every proposed official domain lies within the school's **expected** domain, written here by hand, so
   the skill cannot vouch for its own sources; every source then passes the official-source check against
   that expected domain (or ``config.research.official_shared_domains``), so every filled field has an
   official source URL;
4. the programme is written with ``Store.create_programme`` to the test's temporary data_dir (never the
   owner's: ``HUB_DATA_DIR`` points at tmp_path) and loads back schema-valid.
Evidence: ``reports/live_research.json`` (outputs, problems, cost; no owner data is involved).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from hub import research
from hub.ai.skills import load_schema, run_skill
from hub.config import load_config
from hub.models.common import Verification
from hub.store import Store

from paths import REPO_ROOT

pytestmark = pytest.mark.live

CASES = {
    "Berkeley Haas MFE": ["berkeley.edu"],
    "Carnegie Mellon MSCF": ["cmu.edu"],
}
REPORT = REPO_ROOT / "reports" / "live_research.json"


def _record(query: str, entry: dict) -> None:
    REPORT.parent.mkdir(exist_ok=True)
    data = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.exists() else {}
    data[query] = entry
    REPORT.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


@pytest.mark.parametrize("query", list(CASES))
def test_research_live_programme(query: str, tmp_path: Path) -> None:
    config = load_config()
    data_dir = config.storage.data_dir
    assert tmp_path.resolve() in data_dir.resolve().parents, "live tests must use a temporary data_dir"
    expected = CASES[query]
    shared = config.research.official_shared_domains
    today = date.today()
    result = run_skill(
        "research-programme",
        research.research_payload(query, None, today),
        load_schema("research_programme"),
        config=config,
        action="research_programme",
        subject={},
        source="live",
        timeout_s=config.claude.research_timeout_s,
    )
    output = result.data
    proposed_ok, proposed_problems = research.check_domains(output["official_domains"])
    outside = [d for d in proposed_ok if not research.is_official(f"https://{d}/", expected, [])]
    proposal = research.check_proposal(output, expected, shared)
    entry = {
        "query": query,
        "expected_domains": expected,
        "cost_usd": result.cost_usd,
        "duration_s": result.duration_s,
        "num_turns": result.raw.get("num_turns"),
        "proposed_domains": output["official_domains"],
        "proposed_domain_problems": proposed_problems,
        "proposed_outside_expected": outside,
        "problems": proposal.problems,
        "not_found": proposal.not_found,
        "output": output,
    }
    _record(query, entry)

    assert output["verification"] == "unverified"
    assert proposed_problems == [] and outside == [], (proposed_problems, outside)
    assert proposal.problems == [], proposal.problems  # every filled field: an official source URL
    programme = research.build_programme(output, expected, taken_ids=set(), today=today, shared=shared)
    store = Store.from_config(config)
    store.create_programme(programme)
    (loaded,), errors = store.load_programmes()
    assert errors == []
    assert loaded.verification is Verification.UNVERIFIED
    assert loaded.deadlines and all(d.verification is Verification.UNVERIFIED for d in loaded.deadlines)
    assert all(d.tz and d.source_url for d in loaded.deadlines)
    entry["saved_file"] = (data_dir / "programmes" / f"{loaded.id}.yaml").read_text(encoding="utf-8")
    entry["passed"] = True
    _record(query, entry)
