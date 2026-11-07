"""Research a programme: official sources, domain confirmation and the preview (docs/04 §7; US-16; fast, fake
Claude Code).

- A source counts as official only if its host equals one of the confirmed ``official_domains``, or is a
  subdomain on a dot boundary, or is on ``config.research.official_shared_domains`` (mba.com). Everything
  else is refused at preview with a message naming the field, and Save stays blocked.
- Proposed domains are confirmed by the user first; a single-label name or a public suffix (edu, ac.uk …)
  is never accepted as a school's domain.
- A filled field needs a source; a field without an official source stays null with "No official source
  found"; Save needs a deadline with a zone and a source (DECISIONS #13).
- The built programme is always unverified (rule 11). Nothing is written before Save; Save never
  overwrites an existing programme. A programme without ``official_domains`` gets them prefilled from its
  stored source hosts, and the user confirms them before the comparison runs (re-research).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from hub import research
from hub.app import create_app
from hub.models.common import Verification
from hub.models.programme import Programme
from hub.store import DataError, Store

from helpers import (
    BASE,
    EXAMPLE_DEADLINES_SRC,
    EXAMPLE_SRC,
    FIXED_NOW,
    NO_OFFICIAL_SOURCE,
    FormClient,
    assert_only_usage_line,
    data_snapshot,
    form_fields,
    mirror_output,
    not_found,
    research_output,
    set_field,
    sourced,
    write_recording,
)

TODAY = date(2026, 10, 30)
SHARED = ["mba.com"]
DOMAINS = ["example.edu"]


# ---- the official-source check (rule 13: Python, never the model) ------------------------------------


@pytest.mark.parametrize(
    ("url", "domains", "official"),
    [
        ("https://princeton.edu/finance", ["princeton.edu"], True),
        ("https://bcf.princeton.edu/academic-programs", ["princeton.edu"], True),
        ("https://business.columbia.edu/x", ["columbia.edu"], True),  # a subdomain on a dot boundary
        ("https://academics.business.columbia.edu/x", ["columbia.edu"], True),
        ("https://PRINCETON.EDU./x", ["princeton.edu"], True),  # case and a trailing dot
        ("https://princeton.edu:443/x", ["princeton.edu"], True),  # a port
        ("http://princeton.edu/x", ["princeton.edu"], True),
        ("https://www.mba.com/exams/gmat", ["princeton.edu"], True),  # shared official body (GMAC)
        ("https://mba.com/x", [], True),
        ("https://princeton.edu.evil.com/x", ["princeton.edu"], False),  # look-alike: suffix elsewhere
        ("https://evilprinceton.edu/x", ["princeton.edu"], False),  # look-alike: no dot boundary
        ("https://example-aggregator.com/princeton", ["princeton.edu"], False),
        ("https://notmba.com/x", ["princeton.edu"], False),
        ("https://princeton.edu@example.com/x", ["princeton.edu"], False),  # userinfo: host is example.com
        ("https://user@example.org/x", ["example.org"], False),  # any userinfo, even on an own host
        ("https://128.112.0.1/x", ["princeton.edu"], False),  # an IP address
        ("ftp://princeton.edu/x", ["princeton.edu"], False),
        ("javascript:alert(1)", ["princeton.edu"], False),
        ("princeton.edu/x", ["princeton.edu"], False),  # no scheme
        ("", ["princeton.edu"], False),
    ],
)
def test_is_official(url: str, domains: list[str], official: bool) -> None:
    assert research.is_official(url, domains, SHARED) is official


@pytest.mark.parametrize(
    ("domain", "problem"),
    [
        ("edu", "edu has a single label"),
        ("com", "com has a single label"),
        ("princeton", "princeton has a single label"),
        ("ac.uk", "ac.uk is a public suffix"),
        ("co.uk", "co.uk is a public suffix"),
        ("edu.au", "edu.au is a public suffix"),
        ("github.io", "github.io is a public suffix"),
        ("princeton edu", "princeton edu is not a domain name"),
        ("https://princeton.edu", "https://princeton.edu is not a domain name"),
    ],
)
def test_check_domains_refuses_single_labels_public_suffixes_and_junk(domain: str, problem: str) -> None:
    domains, problems = research.check_domains([domain])
    assert domains == []
    assert len(problems) == 1 and problems[0].startswith(problem), problems


def test_check_domains_accepts_school_domains_lowercased_and_deduplicated() -> None:
    domains, problems = research.check_domains(
        ["Princeton.EDU", "princeton.edu", " bcf.princeton.edu ", "ox.ac.uk", "unimelb.edu.au", ""]
    )
    assert problems == []
    assert domains == ["princeton.edu", "bcf.princeton.edu", "ox.ac.uk", "unimelb.edu.au"]


def test_check_domains_needs_at_least_one() -> None:
    assert research.check_domains(["", "  "]) == ([], ["Enter at least one official domain."])


def test_prefill_domains_uses_the_hosts_of_stored_urls(config) -> None:
    programme = next(p for p in Store.from_config(config).load_programmes()[0] if p.id == "princeton-mfin")
    bare = programme.model_copy(update={"official_domains": []})
    assert research.prefill_domains(bare) == ["bcf.princeton.edu", "gradschool.princeton.edu"]


# ---- the preview checks -----------------------------------------------------------------------------


def _check(output: dict, domains: list[str] = DOMAINS) -> research.Proposal:
    return research.check_proposal(output, domains, SHARED)


def test_an_all_official_proposal_can_be_saved() -> None:
    proposal = _check(research_output())
    assert proposal.problems == [] and proposal.can_save


def test_a_non_official_host_is_refused_naming_the_field() -> None:
    out = research_output()
    out["deadlines"][0]["source_url"] = "https://example-aggregator.com/example-msqf"
    proposal = _check(out)
    assert "Deadline 1 · Source URL: example-aggregator.com is not an official source" in proposal.problems
    assert not proposal.can_save


@pytest.mark.parametrize(
    ("field", "label"),
    [
        ("name", "Name"),
        ("school", "School"),
        ("portal_url", "Portal"),
        ("letters_required", "Letters required"),
        ("curriculum_summary", "Curriculum summary"),
    ],
)
def test_every_sourced_field_is_checked(field: str, label: str) -> None:
    out = research_output()
    out[field] = dict(out[field], source_url="https://princeton.edu.evil.com/p")
    proposal = _check(out)
    assert f"{label} · Source URL: princeton.edu.evil.com is not an official source" in proposal.problems
    assert not proposal.can_save


def test_list_items_and_sources_are_checked_and_numbered() -> None:
    out = research_output()
    out["requirements"][1]["source_url"] = "https://evilexample.edu/req"
    out["essays"][0]["source_url"] = "https://example.edu.evil.com/essay"
    out["curriculum_courses"][0]["source_url"] = "https://blog.example.com/courses"
    out["sources"].append({"url": "https://example-aggregator.com/x", "title": "An aggregator"})
    out["fee"]["source_url"] = "https://fees.example.org/"
    problems = _check(out).problems
    assert "Requirement 2 · Source URL: evilexample.edu is not an official source" in problems
    assert "Essay 1 · Source URL: example.edu.evil.com is not an official source" in problems
    assert "Curriculum course 1 · Source URL: blog.example.com is not an official source" in problems
    assert "Source 3 · URL: example-aggregator.com is not an official source" in problems
    assert "Fee · Source URL: fees.example.org is not an official source" in problems


def test_a_subdomain_of_an_official_domain_is_accepted() -> None:
    out = research_output()
    out["deadlines"][0]["source_url"] = "https://admissions.grad.example.edu/deadlines"
    assert _check(out).problems == []


def test_an_mba_com_source_is_accepted() -> None:
    out = research_output()
    out["requirements"][0]["source_url"] = "https://www.mba.com/exams/gmat-exam"
    assert _check(out).problems == []


@pytest.mark.parametrize("host", ["example.edu.evil.com", "evilexample.edu"])
def test_look_alike_hosts_are_refused(host: str) -> None:
    out = research_output()
    out["portal_url"] = sourced("https://apply.example.edu/msqf", f"https://{host}/apply")
    assert f"Portal · Source URL: {host} is not an official source" in _check(out).problems


def test_a_filled_field_without_a_source_is_refused() -> None:
    out = research_output(letters_required=sourced(3, None))
    proposal = _check(out)
    assert "Letters required · Source URL: missing; every filled field needs an official source" in (
        proposal.problems
    )
    assert not proposal.can_save


def test_fields_without_an_official_source_stay_null_and_are_listed() -> None:
    out = research_output(letters_required=not_found(), ai_policy=not_found(), unit=not_found())
    proposal = _check(out)
    assert proposal.problems == [] and proposal.can_save
    assert {"Unit", "Letters required", "AI policy"} <= set(proposal.not_found)


def test_save_is_blocked_without_a_deadline() -> None:
    proposal = _check(research_output(deadlines=[]))
    assert not proposal.can_save
    assert (
        "Save needs at least one deadline with a time zone and an official source URL." in proposal.problems
    )


def test_a_deadline_with_a_bad_zone_or_date_is_a_problem() -> None:
    out = research_output()
    out["deadlines"][0]["tz"] = "Eastern"
    out["deadlines"].append(dict(out["deadlines"][0], tz="America/Chicago", due_local="5 January 2027"))
    problems = _check(out).problems
    assert any(p.startswith("Deadline 1 · Time zone:") for p in problems), problems
    assert any(p.startswith("Deadline 2 · Date and time:") for p in problems), problems


def test_an_output_claiming_verified_is_refused() -> None:
    with pytest.raises(ValueError, match="unverified"):
        _check(research_output(verification="verified"))


# ---- building the programme ---------------------------------------------------------------------------


def test_build_programme_is_unverified_with_sources_and_notes() -> None:
    out = research_output(letters_required=not_found(), letters_note=not_found())
    out["fee"] = {"amount": None, "currency": "USD", "note": None, "source_url": None}
    programme = research.build_programme(out, DOMAINS, taken_ids=set(), today=TODAY)
    assert isinstance(programme, Programme)
    assert programme.id == "example-msqf"
    assert programme.verification is Verification.UNVERIFIED
    assert programme.verification_note == "Researched, not checked"
    assert programme.verified_on is None
    assert all(d.verification is Verification.UNVERIFIED for d in programme.deadlines)
    assert programme.official_domains == DOMAINS
    assert [(s.url, s.checked) for s in programme.sources] == [
        (EXAMPLE_SRC, TODAY),
        (EXAMPLE_DEADLINES_SRC, TODAY),
    ]
    assert programme.letters_required is None and programme.letters_note == NO_OFFICIAL_SOURCE
    assert programme.fee.amount is None and programme.fee.note == NO_OFFICIAL_SOURCE
    assert programme.ai_policy.text is None
    assert [r.label for r in programme.requirements] == ["Resume", "Two letters of recommendation"]
    assert programme.essays[0].word_limit == 500
    assert programme.curriculum.courses[0].name == "Stochastic Calculus for Finance"
    assert programme.steps == [] and programme.documents == [] and programme.interviews == []
    d = programme.deadlines[0]
    assert (d.due_local.isoformat(), d.tz, d.source_url) == (
        "2027-01-05T17:00:00",
        "America/New_York",
        EXAMPLE_DEADLINES_SRC,
    )


def test_build_programme_never_reuses_a_taken_id() -> None:
    programme = research.build_programme(research_output(), DOMAINS, taken_ids={"example-msqf"}, today=TODAY)
    assert programme.id == "example-msqf-2"


def test_build_programme_refuses_a_proposal_with_problems() -> None:
    out = research_output()
    out["deadlines"][0]["source_url"] = "https://example-aggregator.com/x"
    with pytest.raises(ValueError, match="not an official source"):
        research.build_programme(out, DOMAINS, taken_ids=set(), today=TODAY)


# ---- routes: new research (US-16) ----------------------------------------------------------------------


@pytest.fixture
def recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the fake Claude Code at a research output; returns a setter."""

    def use(output: dict) -> None:
        monkeypatch.setenv("FAKE_CLAUDE_RECORDING", str(write_recording(tmp_path, output)))

    return use


def _changed(before: dict[str, bytes], after: dict[str, bytes]) -> set[str]:
    return {k for k in set(before) | set(after) if before.get(k) != after.get(k)}


def _usage_lines(data_dir: Path) -> list[dict]:
    path = data_dir / "logs" / "ai_usage.jsonl"
    return (
        [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    )


def test_run_shows_the_domains_step_first_and_writes_nothing(
    client: FormClient, data_dir: Path, recorded
) -> None:
    recorded(research_output(official_domains=["example.edu"]))
    before = data_snapshot(data_dir)
    res = client.post("/programmes/research", data={"query": "Example MSQF", "url": ""})
    assert res.status_code == 200
    html = res.text
    assert 'id="research-domains"' in html and 'id="research-save"' not in html  # no preview yet
    fields = dict(form_fields(html, "research-domains"))
    assert fields["domains"].split() == ["example.edu"] and fields["step"] == "check"
    assert _changed(before, data_snapshot(data_dir)) <= {"logs/", "logs/ai_usage.jsonl"}
    (line,) = _usage_lines(data_dir)
    assert (line["action"], line["subject"], line["source"], line["ok"]) == (
        "research_programme",
        {},
        "app",
        True,
    )


def test_check_shows_the_preview_with_sources_and_nulls(client: FormClient, data_dir: Path, recorded) -> None:
    recorded(research_output(ai_policy=not_found()))
    html = client.post("/programmes/research", data={"query": "Example MSQF"}).text
    fields = form_fields(html, "research-domains")
    before = data_snapshot(data_dir)
    res = client.post("/programmes/research", data=fields)
    assert res.status_code == 200
    preview = res.text
    assert 'id="research-save"' in preview
    assert EXAMPLE_DEADLINES_SRC in preview and EXAMPLE_SRC in preview
    assert NO_OFFICIAL_SOURCE in preview
    assert "is not an official source" not in preview
    assert data_snapshot(data_dir) == before  # checking runs in Python only: no call, no write


def test_check_refuses_a_public_suffix_domain_and_stays_on_the_domains_step(
    client: FormClient, data_dir: Path, recorded
) -> None:
    recorded(research_output())
    html = client.post("/programmes/research", data={"query": "Example MSQF"}).text
    fields = set_field(form_fields(html, "research-domains"), "domains", "edu")
    res = client.post("/programmes/research", data=fields)
    assert res.status_code == 200
    assert 'id="research-domains"' in res.text and 'id="research-save"' not in res.text
    assert "edu has a single label" in res.text


def test_a_third_party_source_blocks_save_in_the_page(client: FormClient, data_dir: Path, recorded) -> None:
    out = research_output()
    out["deadlines"][0]["source_url"] = "https://example-aggregator.com/example-msqf"
    recorded(out)
    html = client.post("/programmes/research", data={"query": "Example MSQF"}).text
    preview = client.post("/programmes/research", data=form_fields(html, "research-domains")).text
    assert "Deadline 1 · Source URL: example-aggregator.com is not an official source" in preview
    save_button = preview.split('id="research-save"', 1)[1].split("</form>", 1)[0]
    assert "disabled" in save_button
    # A forced submit is re-checked on the server: refused, nothing written.
    before = data_snapshot(data_dir)
    res = client.post("/programmes/research/save", data=form_fields(preview, "research-save"))
    assert res.status_code == 409
    assert "is not an official source" in res.text
    assert data_snapshot(data_dir) == before


def test_save_writes_an_unverified_programme(client: FormClient, data_dir: Path, recorded) -> None:
    recorded(research_output())
    html = client.post("/programmes/research", data={"query": "Example MSQF"}).text
    preview = client.post("/programmes/research", data=form_fields(html, "research-domains")).text
    res = client.post("/programmes/research/save", data=form_fields(preview, "research-save"))
    assert res.status_code == 303 and res.headers["location"] == "/programmes/example-msqf"
    stored = YAML(typ="safe").load(
        (data_dir / "programmes" / "example-msqf.yaml").read_text(encoding="utf-8")
    )
    assert stored["verification"] == "unverified"
    assert stored["verification_note"] == "Researched, not checked"
    assert all(d["verification"] == "unverified" for d in stored["deadlines"])
    assert stored["official_domains"] == ["example.edu"]
    assert stored["deadlines"][0]["source_url"] == EXAMPLE_DEADLINES_SRC


def test_save_with_no_deadline_is_blocked_until_one_is_entered(
    client: FormClient, data_dir: Path, recorded
) -> None:
    recorded(research_output(deadlines=[]))
    html = client.post("/programmes/research", data={"query": "Example MSQF"}).text
    preview = client.post("/programmes/research", data=form_fields(html, "research-domains")).text
    assert "Save needs at least one deadline with a time zone and an official source URL." in preview
    fields = form_fields(preview, "research-save")
    before = data_snapshot(data_dir)
    assert client.post("/programmes/research/save", data=fields).status_code == 409
    assert data_snapshot(data_dir) == before
    # The user enters the deadline in the preview's empty row, from the school's own page.
    for name, value in [
        ("deadline-0-label", "Round 1"),
        ("deadline-0-date", "2027-01-05"),
        ("deadline-0-time", "17:00"),
        ("deadline-0-tz", "America/New_York"),
        ("deadline-0-time_known", "on"),
        ("deadline-0-source_url", EXAMPLE_DEADLINES_SRC),
    ]:
        fields = set_field(fields, name, value)
    res = client.post("/programmes/research/save", data=fields)
    assert res.status_code == 303
    stored = YAML(typ="safe").load(
        (data_dir / "programmes" / "example-msqf.yaml").read_text(encoding="utf-8")
    )
    assert stored["deadlines"][0]["due_local"] == "2027-01-05T17:00"
    assert stored["deadlines"][0]["verification"] == "unverified"


def test_a_user_entered_deadline_source_must_be_official_too(
    client: FormClient, data_dir: Path, recorded
) -> None:
    recorded(research_output())
    html = client.post("/programmes/research", data={"query": "Example MSQF"}).text
    preview = client.post("/programmes/research", data=form_fields(html, "research-domains")).text
    fields = set_field(
        form_fields(preview, "research-save"), "deadline-0-source_url", "https://deadlines.example.com/"
    )
    before = data_snapshot(data_dir)
    res = client.post("/programmes/research/save", data=fields)
    assert res.status_code == 409
    assert "Deadline 1 · Source URL: deadlines.example.com is not an official source" in res.text
    assert data_snapshot(data_dir) == before


def test_save_never_overwrites_an_existing_programme(client: FormClient, data_dir: Path, recorded) -> None:
    out = research_output(short_name=sourced("Princeton MFin", EXAMPLE_SRC))
    recorded(out)
    original = (data_dir / "programmes" / "princeton-mfin.yaml").read_bytes()
    html = client.post("/programmes/research", data={"query": "Princeton"}).text
    preview = client.post("/programmes/research", data=form_fields(html, "research-domains")).text
    res = client.post("/programmes/research/save", data=form_fields(preview, "research-save"))
    assert res.status_code == 303 and res.headers["location"] == "/programmes/princeton-mfin-2"
    assert (data_dir / "programmes" / "princeton-mfin.yaml").read_bytes() == original


def test_store_create_programme_is_exclusive(config, data_dir: Path) -> None:
    store = Store.from_config(config)
    programme = research.build_programme(research_output(), DOMAINS, taken_ids=set(), today=TODAY)
    store.create_programme(programme)
    with pytest.raises(DataError, match="already exists"):
        store.create_programme(programme.model_copy(update={"name": "Another"}))
    # A file differing only in case counts as the same file (Windows treats them as one).
    other = programme.model_copy(update={"id": "other-msqf"})
    hand_made = b"# hand-made"
    (data_dir / "programmes" / "Other-MSQF.yaml").write_bytes(hand_made)
    with pytest.raises(DataError, match="already exists"):
        store.create_programme(other)
    assert (data_dir / "programmes" / "Other-MSQF.yaml").read_bytes() == hand_made


@pytest.mark.parametrize("mode", ["exit", "timeout", "invalid_json", "schema_mismatch"])
def test_a_failed_research_call_shows_the_message_and_writes_only_its_usage_line(
    client: FormClient, data_dir: Path, mode: str, monkeypatch: pytest.MonkeyPatch, config
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
    if mode == "timeout":
        monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "5")
        claude = config.claude.model_copy(update={"research_timeout_s": 1})
        client = FormClient(
            create_app(config.model_copy(update={"claude": claude}), clock=lambda: FIXED_NOW),
            base_url=BASE,
            headers={"Origin": BASE},
            follow_redirects=False,
        )
    before = data_snapshot(data_dir)
    res = client.post("/programmes/research", data={"query": "Example MSQF"})
    assert res.status_code == 409
    assert "Nothing was saved." in res.text
    error = {
        "exit": "failed",
        "timeout": "timeout",
        "invalid_json": "invalid_output",
        "schema_mismatch": "invalid_output",
    }
    assert_only_usage_line(
        data_dir, before, ok=False, error=error[mode], action="research_programme", subject={}, source="app"
    )


def test_an_empty_query_runs_nothing(client: FormClient, data_dir: Path) -> None:
    before = data_snapshot(data_dir)
    res = client.post("/programmes/research", data={"query": "  "})
    assert res.status_code == 409
    assert "Enter a programme to research" in res.text
    assert data_snapshot(data_dir) == before


def test_research_entry_points_are_on_the_dashboard_and_add_programme(client: FormClient) -> None:
    for page in ("/", "/programmes/new"):
        html = client.get(page).text
        assert 'action="/programmes/research"' in html and "Research a programme" in html, page


# ---- routes: re-research on a programme without official_domains (US-18) -----------------------------


def _drop_domains(data_dir: Path) -> None:
    path = data_dir / "programmes" / "princeton-mfin.yaml"
    text = path.read_text(encoding="utf-8").replace("official_domains:\n  - princeton.edu\n", "")
    path.write_text(text, encoding="utf-8")


def test_reresearch_without_official_domains_asks_for_confirmation_before_comparing(
    client: FormClient, config, data_dir: Path, recorded
) -> None:
    _drop_domains(data_dir)
    programme = next(p for p in Store.from_config(config).load_programmes()[0] if p.id == "princeton-mfin")
    assert programme.official_domains == []
    recorded(mirror_output(programme, programme.sources[0].url))
    res = client.post("/programmes/princeton-mfin/research", data={})
    assert res.status_code == 200
    html = res.text
    assert 'id="research-domains"' in html
    assert 'id="research-apply"' not in html and "data-change=" not in html  # no comparison yet
    fields = dict(form_fields(html, "research-domains"))
    assert fields["domains"].split() == ["bcf.princeton.edu", "gradschool.princeton.edu"]
    # Confirming (here shortened to the school's domain) runs the comparison.
    confirmed = set_field(form_fields(html, "research-domains"), "domains", "princeton.edu")
    compared = client.post("/programmes/princeton-mfin/research", data=confirmed).text
    assert 'id="research-apply"' in compared and "data-change=" in compared


def test_reresearch_with_official_domains_compares_at_once(
    client: FormClient, config, data_dir: Path, recorded
) -> None:
    programme = next(p for p in Store.from_config(config).load_programmes()[0] if p.id == "princeton-mfin")
    recorded(mirror_output(programme, programme.sources[0].url))
    html = client.post("/programmes/princeton-mfin/research", data={}).text
    assert 'id="research-apply"' in html and "data-change=" in html
    (line,) = _usage_lines(data_dir)
    assert (line["action"], line["subject"], line["source"]) == (
        "reresearch_programme",
        {"programme_id": "princeton-mfin"},
        "app",
    )


def test_the_portal_link_itself_must_be_official() -> None:
    """The portal URL is a link the owner will click, so its own host is checked too (ai-output review)."""
    out = research_output(portal_url=sourced("https://apply-example-msqf.com/start", EXAMPLE_SRC))
    assert "Portal · URL: apply-example-msqf.com is not an official source" in _check(out).problems
