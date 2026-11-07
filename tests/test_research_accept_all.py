"""Re-research "Accept all changes" and "Clear all" (user request, session 2; DECISIONS #68; docs/04 §7.1).

- Accept all ticks every row that can be accepted (new or changed, source official) and nothing else: never a
  refused row, a "Not found by research, kept" row, an unchanged row or the official_domains row (domains are
  confirmed one by one). It never saves: Save accepted changes still writes and re-checks everything.
- The count reads "{n} of {total} changes selected · {r} refused"; total = every new or changed row (refused
  ones and the domains row included), so it counts what Save could write.
- Without JS, Accept all / Clear all are submits to ``/research/select``, which re-renders the comparison with
  those rows ticked (keeping valid ticks the user already made) and writes nothing; no Claude call is made.

The C-2c-02 tests (tests/test_research_merge.py, tests/e2e/test_research.py) are unchanged; the names here
avoid "us18" so the criterion's ``-k us18`` selection stays as it was.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from hub import research
from hub.store import Store

from helpers import FormClient, data_snapshot, form_fields, mirror_output, set_field, sourced, write_recording

TODAY = date(2026, 10, 30)
SHARED = ["mba.com"]
PID = "princeton-mfin"
BCF = "https://bcf.princeton.edu/academic-programs/master-in-finance/apply-for-the-master-in-finance-program"
AGGREGATOR = "https://example-aggregator.com/princeton"


def _path(data_dir: Path) -> Path:
    return data_dir / "programmes" / f"{PID}.yaml"


def _programme(config):
    return next(p for p in Store.from_config(config).load_programmes()[0] if p.id == PID)


def _output(config) -> dict:
    """A changed deadline, a new requirement, a refused letters change, and one stored requirement research
    didn't find (so it is "kept")."""
    out = mirror_output(_programme(config), BCF)
    out["deadlines"][0]["due_local"] = "2026-12-01T23:59"
    out["letters_required"] = sourced(2, AGGREGATOR)
    out["requirements"].append({"id": None, "label": "Video essay", "note": None, "source_url": BCF})
    out["requirements"].pop(0)
    return out


def _changes(config, domains: list[str]) -> list[research.Change]:
    return research.compare(_programme(config), _output(config), domains, SHARED)


# ---- pure: which rows, and the count ----------------------------------------------------------------------


def test_accept_all_picks_only_acceptable_rows_never_refused_kept_or_domains(config) -> None:
    changes = _changes(config, ["princeton.edu", "bcf.princeton.edu"])  # domains differ: a domains row
    by_id = {c.id: c for c in changes}
    assert by_id["official_domains"].acceptable  # the user could tick it ...
    assert by_id["letters_required"].refused and any(c.kind == "kept" for c in changes)
    ids = research.accept_all_ids(changes)
    assert "official_domains" not in ids  # ... but Accept all never does
    assert "letters_required" not in ids  # refused
    assert not [i for i in ids if by_id[i].kind not in {"new", "changed"}]  # no kept or unchanged row
    assert set(ids) == {c.id for c in changes if c.acceptable and c.id != "official_domains"}
    assert {"deadline:main", "requirement:video-essay"} <= set(ids)
    assert ids == [c.id for c in changes if c.id in set(ids)]  # comparison order


def test_the_count_names_selected_total_and_refused(config) -> None:
    changes = _changes(config, ["princeton.edu", "bcf.princeton.edu"])
    ids = research.accept_all_ids(changes)
    total = sum(1 for c in changes if c.kind in {"new", "changed"})
    assert total == len(ids) + 2  # + the refused row + the domains row
    assert research.selection_text(changes, set()) == f"0 of {total} changes selected · 1 refused"
    assert research.selection_text(changes, set(ids)) == f"{len(ids)} of {total} changes selected · 1 refused"
    # a ticked id that isn't acceptable (refused, kept, unknown) is not counted
    assert research.selection_text(changes, {"letters_required", "name", "bogus"}).startswith("0 of ")


def test_the_count_without_refused_rows_and_in_the_singular(config) -> None:
    out = mirror_output(_programme(config), BCF)
    out["letters_required"] = sourced(2, BCF)
    changes = research.compare(_programme(config), out, ["princeton.edu"], SHARED)
    assert research.selection_text(changes, set()) == "0 of 1 change selected"
    assert research.selection_text(changes, {"letters_required"}) == "1 of 1 change selected"


# ---- routes: Select writes nothing and calls nothing; apply re-checks as before ---------------------------


@pytest.fixture
def calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "fake-calls.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(path))
    return path


@pytest.fixture
def compared(client: FormClient, config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, calls: Path) -> str:
    monkeypatch.setenv("FAKE_CLAUDE_RECORDING", str(write_recording(tmp_path, _output(config))))
    res = client.post(f"/programmes/{PID}/research", data={})
    assert res.status_code == 200
    return res.text


def _ticked(html: str) -> set[str]:
    boxes = re.findall(r'<input[^>]*name="accept"[^>]*>', html)
    return {re.search(r'value="([^"]+)"', b).group(1) for b in boxes if re.search(r"\schecked", b)}


def _select(client: FormClient, html: str, which: str, ticked: list[str] | None = None):
    fields = [(k, v) for k, v in form_fields(html, "research-apply") if k != "accept"]
    fields += [("accept", a) for a in ticked or []]
    return client.post(f"/programmes/{PID}/research/select", data=[*fields, ("select", which)])


def test_the_comparison_offers_accept_all_and_clear_all_as_no_js_submits(config, compared: str) -> None:
    assert re.search(
        rf'<button[^>]*formaction="/programmes/{PID}/research/select"[^>]*name="select"[^>]*value="all"',
        compared,
    )
    assert re.search(r'<button[^>]*name="select"[^>]*value="none"', compared)
    assert "Accept all changes" in compared and "Clear all" in compared
    assert not _ticked(compared)  # boxes still start unticked
    # the JS ticks exactly the boxes the server marks with data-accept-all
    boxes = re.findall(r'<input[^>]*name="accept"[^>]*>', compared)
    marked = {re.search(r'value="([^"]+)"', b).group(1) for b in boxes if "data-accept-all" in b}
    assert marked == set(research.accept_all_ids(_changes(config, ["princeton.edu"])))
    assert re.search(r'id="selection-count"[^>]*>0 of \d+ changes selected · 1 refused<', compared)


def test_select_all_ticks_the_acceptable_rows_writes_nothing_and_calls_nothing(
    client: FormClient, config, data_dir: Path, compared: str, calls: Path
) -> None:
    before, calls_before = data_snapshot(data_dir), calls.read_text(encoding="utf-8")
    res = _select(client, compared, "all")
    assert res.status_code == 200
    expected = set(research.accept_all_ids(_changes(config, ["princeton.edu"])))
    assert _ticked(res.text) == expected
    assert "letters_required" not in _ticked(res.text) and "official_domains" not in _ticked(res.text)
    assert data_snapshot(data_dir) == before  # nothing written, not even a usage line
    assert calls.read_text(encoding="utf-8") == calls_before  # no Claude call
    total = sum(1 for c in _changes(config, ["princeton.edu"]) if c.kind in {"new", "changed"})
    assert f"{len(expected)} of {total} changes selected · 1 refused" in res.text


def test_select_all_keeps_valid_ticks_and_ignores_forged_ones(
    client: FormClient, config, data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _path(data_dir)
    path.write_text(
        path.read_text(encoding="utf-8").replace("official_domains:\n  - princeton.edu\n", ""),
        encoding="utf-8",
    )
    monkeypatch.setenv("FAKE_CLAUDE_RECORDING", str(write_recording(tmp_path, _output(config))))
    html = client.post(f"/programmes/{PID}/research", data={}).text
    confirmed = set_field(form_fields(html, "research-domains"), "domains", "princeton.edu")
    compared = client.post(f"/programmes/{PID}/research", data=confirmed).text
    before = data_snapshot(data_dir)
    res = _select(client, compared, "all", ["official_domains", "letters_required", "name", "bogus"])
    ticked = _ticked(res.text)
    assert "official_domains" in ticked  # the user's own tick stays
    assert not ticked & {"letters_required", "name", "bogus"}  # refused, unchanged, unknown: never ticked
    assert data_snapshot(data_dir) == before


def test_clear_all_unticks_everything(client: FormClient, data_dir: Path, compared: str) -> None:
    ticked = _ticked(_select(client, compared, "all").text)
    assert ticked
    res = _select(client, compared, "none", sorted(ticked))
    assert res.status_code == 200 and _ticked(res.text) == set()
    assert "0 of " in res.text


def test_save_after_select_all_writes_only_those_rows_and_still_rechecks(
    client: FormClient, config, data_dir: Path, compared: str
) -> None:
    stored_before = len(_programme(config).requirements)
    selected = _select(client, compared, "all").text
    fields = form_fields(selected, "research-apply")  # what the browser now submits (ticked boxes included)
    assert ("accept", "deadline:main") in fields
    res = client.post(f"/programmes/{PID}/research/apply", data=fields)
    assert res.status_code == 303
    stored = YAML(typ="safe").load(_path(data_dir).read_text(encoding="utf-8"))
    assert stored["deadlines"][0]["due_local"] == "2026-12-01T23:59"
    assert stored["deadlines"][0]["verification"] == "unverified"
    assert "Video essay" in [r["label"] for r in stored["requirements"]]
    assert stored["letters_required"] == 3  # refused: never written
    assert len(stored["requirements"]) == stored_before + 1  # the kept requirement is not removed


def test_select_keeps_the_comparison_digest_so_a_changed_file_is_still_refused(
    client: FormClient, data_dir: Path, compared: str
) -> None:
    digest = dict(form_fields(compared, "research-apply"))["digest"]
    path = _path(data_dir)
    path.write_text(path.read_text(encoding="utf-8") + "# edited by hand\n", encoding="utf-8")
    selected = _select(client, compared, "all").text
    assert dict(form_fields(selected, "research-apply"))["digest"] == digest
    edited = path.read_bytes()
    res = client.post(f"/programmes/{PID}/research/apply", data=form_fields(selected, "research-apply"))
    assert res.status_code == 409 and "The file changed since the comparison" in res.text
    assert path.read_bytes() == edited


def test_a_tampered_proposal_on_select_never_ticks_a_refused_row(
    client: FormClient, data_dir: Path, compared: str
) -> None:
    import json

    fields = form_fields(compared, "research-apply")
    proposal = json.loads(dict(fields)["proposal"])
    proposal["deadlines"][0]["source_url"] = AGGREGATOR
    fields = set_field(fields, "proposal", json.dumps(proposal))
    before = data_snapshot(data_dir)
    res = client.post(f"/programmes/{PID}/research/select", data=[*fields, ("select", "all")])
    assert "deadline:main" not in _ticked(res.text)
    assert data_snapshot(data_dir) == before
