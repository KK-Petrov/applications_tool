"""Re-research: the field-by-field comparison and applying accepted changes (C-2c-02; docs/04 §7.1; US-18).

Every test name contains ``us18`` because the criterion's command selects with ``-k us18``.

- The comparison marks each field new / changed / unchanged, or "kept" when research found nothing for a
  stored value (research never removes stored data); each row carries its source URL.
- Nothing is written until Save; a Save with every change rejected leaves the file byte-identical.
- An accepted change to a deadline's date, time, zone or source resets that deadline to unverified
  (DECISIONS #21, #30); research never sets ``verified``. Any other accepted change makes the programme
  unverified ("Researched, not checked", rule 11).
- Saving keeps the file's comments and shape (#29b); a file changed since the comparison is refused; a row
  whose source is not official can't be accepted.
"""

from __future__ import annotations

import difflib
import json
import re
from datetime import date
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from hub import research
from hub.models.common import Verification
from hub.rules.programmes import badge
from hub.store import Store

from helpers import FormClient, data_snapshot, form_fields, mirror_output, set_field, sourced, write_recording

TODAY = date(2026, 10, 30)
SHARED = ["mba.com"]
DOMAINS = ["princeton.edu"]
PID = "princeton-mfin"
BCF = "https://bcf.princeton.edu/academic-programs/master-in-finance/apply-for-the-master-in-finance-program"
GRAD = "https://gradschool.princeton.edu/academics/degrees-requirements/fields-study/finance"


def _path(data_dir: Path) -> Path:
    return data_dir / "programmes" / f"{PID}.yaml"


def _programme(config):
    return next(p for p in Store.from_config(config).load_programmes()[0] if p.id == PID)


def _changed_output(config) -> dict:
    """Research that finds an earlier deadline, two letters instead of three, and a new requirement."""
    out = mirror_output(_programme(config), BCF)
    out["deadlines"][0]["due_local"] = "2026-12-01T23:59"
    out["letters_required"] = sourced(2, BCF)
    out["requirements"].append({"id": None, "label": "Video essay", "note": None, "source_url": BCF})
    return out


def _by_id(changes: list[research.Change]) -> dict[str, research.Change]:
    return {c.id: c for c in changes}


# ---- pure comparison and apply -----------------------------------------------------------------------


def test_us18_compare_marks_new_changed_unchanged_with_sources(config) -> None:
    current = _programme(config)
    changes = _by_id(research.compare(current, _changed_output(config), DOMAINS, SHARED))
    assert changes["deadline:main"].kind == "changed"
    assert changes["deadline:main"].source_url == GRAD
    assert changes["letters_required"].kind == "changed"
    assert (changes["letters_required"].old, changes["letters_required"].new) == ("3", "2")
    assert changes["letters_required"].source_url == BCF
    new_req = [c for c in changes.values() if c.id.startswith("requirement:") and c.kind == "new"]
    assert [c.new for c in new_req] == ["Video essay"]
    assert changes["name"].kind == "unchanged" and changes["school"].kind == "unchanged"
    assert changes["requirement:cv"].kind == "unchanged"
    assert all(c.refused is None for c in changes.values())
    assert all(c.source_url for c in changes.values() if c.kind in {"new", "changed"})


def test_us18_stored_values_research_did_not_find_are_kept_not_removed(config) -> None:
    out = mirror_output(_programme(config), BCF)
    out["requirements"] = out["requirements"][:2]  # research found only two requirements
    out["letters_note"] = {"value": None, "source_url": None, "note": "No official source found"}
    changes = _by_id(research.compare(_programme(config), out, DOMAINS, SHARED))
    assert changes["letters_note"].kind == "kept"
    assert changes["requirement:gmat"].kind == "kept"
    accepted = {c.id for c in changes.values() if c.kind in {"new", "changed"}}
    assert research.apply(_programme(config), out, DOMAINS, accepted, TODAY) is None  # nothing to apply


def test_us18_apply_with_nothing_accepted_returns_none(config) -> None:
    assert research.apply(_programme(config), _changed_output(config), DOMAINS, set(), TODAY) is None


def test_us18_unchanged_or_kept_rows_cannot_be_accepted(config) -> None:
    with pytest.raises(ValueError, match="cannot be accepted"):
        research.apply(_programme(config), _changed_output(config), DOMAINS, {"name"}, TODAY)


def test_us18_a_row_with_a_non_official_source_is_refused_and_cannot_be_accepted(config) -> None:
    out = _changed_output(config)
    out["letters_required"] = sourced(2, "https://example-aggregator.com/princeton")
    change = _by_id(research.compare(_programme(config), out, DOMAINS, SHARED))["letters_required"]
    assert change.refused == "Letters required · Source URL: example-aggregator.com is not an official source"
    with pytest.raises(ValueError, match="not an official source"):
        research.apply(_programme(config), out, DOMAINS, {"letters_required"}, TODAY)


def _verified(config) -> None:
    """Make the stored Princeton verified, with a second deadline, both verified (as Mark verified does)."""
    store = Store.from_config(config)
    p = _programme(config)
    second = p.deadlines[0].model_copy(update={"id": "round-2", "label": "Round 2"})
    second = second.model_copy(update={"due_local": second.due_local.replace(year=2027, month=1, day=10)})
    p.deadlines = [*p.deadlines, second]
    p.verification, p.verification_note, p.verified_on = Verification.VERIFIED, None, TODAY
    for d in p.deadlines:
        d.verification = Verification.VERIFIED
    store.save_programme(p)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("due_local", "2026-12-01T23:59"),  # date
        ("due_local", "2026-12-15T17:00"),  # time
        ("tz", "America/Chicago"),  # zone
        ("source_url", "https://gradschool.princeton.edu/admission/deadlines"),  # source (#30)
    ],
)
def test_us18_an_accepted_deadline_change_resets_only_that_deadline(config, field: str, value: str) -> None:
    _verified(config)
    current = _programme(config)
    out = mirror_output(current, BCF)
    out["deadlines"][0][field] = value
    updated = research.apply(current, out, DOMAINS, {"deadline:main"}, TODAY)
    assert updated is not None
    main, second = updated.deadlines
    assert main.verification is Verification.UNVERIFIED
    assert second.verification is Verification.VERIFIED  # the other deadline is untouched
    # A deadline-only change follows #21: the programme stays verified, its badge says why.
    assert updated.verification is Verification.VERIFIED
    assert badge(updated).reason == "Deadline changed, not checked"


def test_us18_a_label_only_deadline_change_keeps_its_verification(config) -> None:
    _verified(config)
    current = _programme(config)
    out = mirror_output(current, BCF)
    out["deadlines"][0]["label"] = "Final application deadline"
    updated = research.apply(current, out, DOMAINS, {"deadline:main"}, TODAY)
    assert updated is not None and updated.deadlines[0].label == "Final application deadline"
    assert updated.deadlines[0].verification is Verification.VERIFIED


def test_us18_a_new_deadline_is_unverified(config) -> None:
    _verified(config)
    current = _programme(config)
    out = mirror_output(current, BCF)
    out["deadlines"].append(dict(out["deadlines"][0], id=None, label="Round 3", due_local="2027-02-01T23:59"))
    changes = [c for c in research.compare(current, out, DOMAINS, SHARED) if c.kind == "new"]
    (new,) = changes
    updated = research.apply(current, out, DOMAINS, {new.id}, TODAY)
    assert updated is not None
    added = updated.deadlines[-1]
    assert added.label == "Round 3" and added.verification is Verification.UNVERIFIED


def test_us18_any_other_accepted_change_makes_the_programme_unverified(config) -> None:
    _verified(config)
    current = _programme(config)
    updated = research.apply(current, _changed_output(config), DOMAINS, {"letters_required"}, TODAY)
    assert updated is not None and updated.letters_required == 2
    assert updated.verification is Verification.UNVERIFIED
    assert updated.verification_note == "Researched, not checked"
    assert all(d.verification is Verification.VERIFIED for d in updated.deadlines)  # deadlines untouched


def test_us18_research_never_sets_verified(config) -> None:
    current = _programme(config)  # unverified
    out = _changed_output(config)
    every = {c.id for c in research.compare(current, out, DOMAINS, SHARED) if c.kind in {"new", "changed"}}
    updated = research.apply(current, out, DOMAINS, every, TODAY)
    assert updated is not None
    assert updated.verification is Verification.UNVERIFIED and updated.verified_on is None
    assert all(d.verification is Verification.UNVERIFIED for d in updated.deadlines)
    with pytest.raises(ValueError, match="unverified"):
        research.compare(current, dict(out, verification="verified"), DOMAINS, SHARED)


def test_us18_a_new_requirement_keeps_the_stored_ticks(config) -> None:
    store = Store.from_config(config)
    p = _programme(config)
    p.requirements[1].done = True  # the owner ticked "Resume / CV"
    store.save_programme(p)
    current = _programme(config)
    out = _changed_output(config)
    (new,) = [
        c
        for c in research.compare(current, out, DOMAINS, SHARED)
        if c.id.startswith("requirement:") and c.kind == "new"
    ]
    updated = research.apply(current, out, DOMAINS, {new.id}, TODAY)
    assert updated is not None
    assert updated.requirements[1].done is True
    assert updated.requirements[-1].label == "Video essay" and updated.requirements[-1].done is False


# ---- routes: nothing written before Save; byte-identical when everything is rejected -------------------


@pytest.fixture
def compared(client: FormClient, config, data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Run Re-research on Princeton with the changed output; return the comparison page's HTML."""
    rec = write_recording(tmp_path, _changed_output(config))
    monkeypatch.setenv("FAKE_CLAUDE_RECORDING", str(rec))
    res = client.post(f"/programmes/{PID}/research", data={})
    assert res.status_code == 200
    return res.text


def _apply(client: FormClient, html: str, accept: list[str]):
    fields = [(k, v) for k, v in form_fields(html, "research-apply") if k != "accept"]
    fields += [("accept", a) for a in accept]
    return client.post(f"/programmes/{PID}/research/apply", data=fields)


def test_us18_the_comparison_page_writes_nothing(
    client: FormClient, config, data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = write_recording(tmp_path, _changed_output(config))
    monkeypatch.setenv("FAKE_CLAUDE_RECORDING", str(rec))
    before = data_snapshot(data_dir)
    html = client.post(f"/programmes/{PID}/research", data={}).text
    after = data_snapshot(data_dir)
    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    assert changed <= {"logs/", "logs/ai_usage.jsonl"}  # only the call's usage line
    assert 'data-change="deadline:main"' in html and 'data-kind="changed"' in html
    assert GRAD in html and BCF in html  # each row with its source
    # Accept boxes start unticked; only new / changed rows have one.
    boxes = re.findall(r'<input[^>]*name="accept"[^>]*>', html)
    values = {re.search(r'value="([^"]+)"', b).group(1) for b in boxes}
    assert values >= {"deadline:main", "letters_required"} and "name" not in values
    assert not [b for b in boxes if "checked" in b]


def test_us18_save_with_everything_rejected_leaves_the_file_byte_identical(
    client: FormClient, data_dir: Path, compared: str
) -> None:
    path = _path(data_dir)
    before_bytes, before_mtime = path.read_bytes(), path.stat().st_mtime_ns
    before = data_snapshot(data_dir)
    res = _apply(client, compared, [])
    assert res.status_code in {200, 303}
    assert path.read_bytes() == before_bytes and path.stat().st_mtime_ns == before_mtime
    assert data_snapshot(data_dir) == before
    page = res.text if res.status_code == 200 else client.get(res.headers["location"]).text
    assert "No change was accepted, so nothing was saved." in page


def test_us18_accept_one_reject_another_changes_only_the_accepted_lines(
    client: FormClient, data_dir: Path, compared: str
) -> None:
    path = _path(data_dir)
    before = path.read_text(encoding="utf-8")
    res = _apply(client, compared, ["letters_required"])  # the deadline change is rejected
    assert res.status_code == 303 and res.headers["location"] == f"/programmes/{PID}"
    after = path.read_text(encoding="utf-8")
    diff = [
        line
        for line in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0)
        if line[:1] in "+-" and not line.startswith(("+++", "---"))
    ]
    assert sorted(diff) == sorted(
        [
            "-verification_note: Last cycle's date",
            "+verification_note: Researched, not checked",
            "-letters_required: 3",
            "+letters_required: 2",
        ]
    ), diff
    stored = YAML(typ="safe").load(after)
    assert stored["deadlines"][0]["due_local"] == "2026-12-15T23:59"  # the rejected change is not there


def test_us18_accepted_deadline_change_is_saved_unverified(
    client: FormClient, data_dir: Path, compared: str
) -> None:
    res = _apply(client, compared, ["deadline:main"])
    assert res.status_code == 303
    stored = YAML(typ="safe").load(_path(data_dir).read_text(encoding="utf-8"))
    main = stored["deadlines"][0]
    assert main["due_local"] == "2026-12-01T23:59" and main["verification"] == "unverified"
    assert stored["letters_required"] == 3  # not accepted


def test_us18_saving_keeps_comments(client: FormClient, data_dir: Path, compared: str) -> None:
    path = _path(data_dir)
    comments_before = [line for line in path.read_text(encoding="utf-8").splitlines() if "#" in line]
    assert comments_before
    assert _apply(client, compared, ["letters_required", "deadline:main"]).status_code == 303
    comments_after = [line for line in path.read_text(encoding="utf-8").splitlines() if "#" in line]
    assert comments_after == comments_before


def test_us18_a_file_changed_since_the_comparison_is_refused(
    client: FormClient, data_dir: Path, compared: str
) -> None:
    path = _path(data_dir)
    path.write_text(
        path.read_text(encoding="utf-8").replace("Bendheim Center for Finance", "Bendheim Centre"),
        encoding="utf-8",
    )
    edited = path.read_bytes()
    before = data_snapshot(data_dir)
    res = _apply(client, compared, ["letters_required"])
    assert res.status_code == 409
    assert "The file changed since the comparison. Nothing was saved." in res.text
    assert path.read_bytes() == edited and data_snapshot(data_dir) == before


def test_us18_a_forced_accept_of_a_refused_row_writes_nothing(
    client: FormClient, config, data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _changed_output(config)
    out["letters_required"] = sourced(2, "https://example-aggregator.com/princeton")
    monkeypatch.setenv("FAKE_CLAUDE_RECORDING", str(write_recording(tmp_path, out)))
    html = client.post(f"/programmes/{PID}/research", data={}).text
    assert "Letters required · Source URL: example-aggregator.com is not an official source" in html
    before = data_snapshot(data_dir)
    res = _apply(client, html, ["letters_required"])
    assert res.status_code == 409
    assert data_snapshot(data_dir) == before


def test_us18_a_tampered_proposal_is_rechecked_on_save(
    client: FormClient, data_dir: Path, compared: str
) -> None:
    """The proposal travels in a hidden field; the server re-runs every check on Save."""
    fields = form_fields(compared, "research-apply")
    proposal = json.loads(dict(fields)["proposal"])
    proposal["letters_required"]["source_url"] = "https://example-aggregator.com/x"
    fields = set_field(fields, "proposal", json.dumps(proposal)) + [("accept", "letters_required")]
    before = data_snapshot(data_dir)
    res = client.post(f"/programmes/{PID}/research/apply", data=fields)
    assert res.status_code == 409
    assert data_snapshot(data_dir) == before


def test_us18_confirmed_domains_are_a_change_like_any_other(
    client: FormClient, config, data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A programme without official_domains: rejecting everything (domains included) writes nothing;
    accepting the domains row writes exactly that field."""
    path = _path(data_dir)
    path.write_text(
        path.read_text(encoding="utf-8").replace("official_domains:\n  - princeton.edu\n", ""),
        encoding="utf-8",
    )
    original = path.read_bytes()
    monkeypatch.setenv("FAKE_CLAUDE_RECORDING", str(write_recording(tmp_path, _changed_output(config))))
    html = client.post(f"/programmes/{PID}/research", data={}).text
    confirmed = set_field(form_fields(html, "research-domains"), "domains", "princeton.edu")
    compared = client.post(f"/programmes/{PID}/research", data=confirmed).text
    assert 'data-change="official_domains"' in compared
    _apply(client, compared, [])
    assert path.read_bytes() == original
    assert _apply(client, compared, ["official_domains"]).status_code == 303
    stored = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    assert stored["official_domains"] == ["princeton.edu"]
    assert stored["letters_required"] == 3 and stored["verification_note"] == "Last cycle's date"
