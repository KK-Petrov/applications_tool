"""Add standard steps and documents through the routes (DECISIONS #97; docs/03 §1, §4.2).

- the programme page links to ``GET /programmes/{id}/standard``: a preview, every new row ticked, the items
  already there listed without a checkbox, nothing written;
- ``POST /programmes/{id}/standard`` works the proposal out again from the file and writes only the ticked
  keys that are still new, through the store (comments and shape kept); unticked rows are not written; a
  second save adds nothing; an unknown key or nothing ticked is refused with nothing written;
- the programme page then says what was added and shows each step's linked requirement;
- Edit programme says which step lost its link when its requirement was removed (user addition to Q2).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hub.store import Store

from helpers import form_fields, set_field, tree_digest

PID = "probe-std"
FORM = "standard-form"
PROBE = """\
# Probe programme: researched, so no steps or documents yet (a comment the save must keep)
id: probe-std
name: Probe Institute Master in Quantitative Finance
short_name: Probe MQF
school: Probe Institute
deadlines:
  - id: main
    label: Application deadline
    due_local: 2027-02-01T17:00
    tz: America/New_York
    source_url: https://example.com/apply
requirements:
  - id: transcripts
    label: Official transcripts
  - id: tests
    label: GMAT or GRE score  # the owner takes one of the two
  - id: letters
    label: Two letters of recommendation
  - id: essays
    label: Two essays
  - id: fee
    label: Application fee
"""


@pytest.fixture
def probe(data_dir: Path) -> Path:
    path = data_dir / "programmes" / f"{PID}.yaml"
    path.write_text(PROBE, encoding="utf-8", newline="\n")
    return path


def _load(config, pid: str = PID):
    return Store.from_config(config).load_programme(config.storage.data_dir / "programmes" / f"{pid}.yaml")


def _preview(client: TestClient, pid: str = PID) -> str:
    res = client.get(f"/programmes/{pid}/standard")
    assert res.status_code == 200, res.text[-400:]
    return res.text


def test_the_programme_page_links_to_the_preview(client: TestClient, probe: Path) -> None:
    html = client.get(f"/programmes/{PID}").text
    assert f'href="/programmes/{PID}/standard"' in html and "Add standard steps and documents" in html


def test_the_preview_ticks_every_new_row_and_writes_nothing(
    client: TestClient, data_dir: Path, probe: Path
) -> None:
    before = tree_digest(data_dir)
    html = _preview(client)
    assert tree_digest(data_dir) == before
    ticked = [v for k, v in form_fields(html, FORM) if k == "add"]
    assert ticked == [  # no English requirement, so no English score report row
        "step:portal",
        "step:recommendations",
        "step:essays",
        "step:fee",
        "step:submit",
        "document:transcript",
        "document:gmat",
        "document:gre",
    ]
    assert "Linked to: Two letters of recommendation" in html
    assert "From: GMAT or GRE score" in html


def test_the_preview_lists_items_already_there_without_a_checkbox(client: TestClient) -> None:
    html = _preview(client, "booth-mif")
    assert [v for k, v in form_fields(html, FORM) if k == "add"] == ["step:fee"]
    assert "Already on this programme" in html and "GMAT score report" in html
    assert 'value="document:gmat"' not in html


def test_nothing_to_add_says_so(client: TestClient) -> None:
    html = _preview(client, "princeton-mfin")
    assert "Nothing to add" in html
    assert 'id="standard-form"' not in html  # no Save without a row


def test_save_writes_the_ticked_rows_only_and_keeps_the_file(client: TestClient, config, probe: Path) -> None:
    fields = [(k, v) for k, v in form_fields(_preview(client), FORM) if v != "document:gre"]
    res = client.post(f"/programmes/{PID}/standard", data=fields)
    assert res.status_code == 303
    assert res.headers["location"] == f"/programmes/{PID}?added_steps=5&added_documents=2"
    p = _load(config)
    assert [(s.order, s.label, s.requirement_ids) for s in p.steps] == [
        (1, "Create an account on the application portal", []),
        (2, "Request the recommendation letters", ["letters"]),
        (3, "Finalise the essays", ["essays"]),
        (4, "Pay the application fee", ["fee"]),
        (5, "Submit the application", []),
    ]
    assert [(d.id, d.label) for d in p.documents] == [
        ("transcript", "Official transcript"),
        ("gmat", "GMAT score report"),
    ]
    text = probe.read_text(encoding="utf-8")
    assert text.startswith(PROBE)  # comments, order and every existing line kept; the new lists appended
    assert "requirement_ids" not in text.split("- order: 1")[1].split("- order: 2")[0]  # no empty link key


def test_a_second_save_adds_nothing(client: TestClient, config, probe: Path) -> None:
    fields = form_fields(_preview(client), FORM)
    client.post(f"/programmes/{PID}/standard", data=fields)
    before = probe.read_bytes()
    res = client.post(f"/programmes/{PID}/standard", data=fields, follow_redirects=True)
    assert res.status_code == 200 and "Nothing was added" in res.text
    assert probe.read_bytes() == before


def test_the_page_says_what_was_added_and_shows_the_links(client: TestClient, probe: Path) -> None:
    res = client.post(f"/programmes/{PID}/standard", data=form_fields(_preview(client), FORM))
    html = client.get(res.headers["location"]).text
    assert "Added 5 steps and 3 documents." in html
    assert re.search(r"Pay the application fee</label>.*?For: Application fee", html, re.S)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ([("add", "step:evil")], "is not a standard item"),
        ([], "Tick at least one item"),
    ],
)
def test_refusals_write_nothing(
    client: TestClient, data_dir: Path, probe: Path, data: list, message: str
) -> None:
    before = tree_digest(data_dir)
    res = client.post(f"/programmes/{PID}/standard", data=data)
    assert res.status_code == 409 and message in res.text and "Nothing was saved" in res.text
    assert tree_digest(data_dir) == before


def test_unknown_programme_is_404(client: TestClient) -> None:
    assert client.get("/programmes/no-such-programme/standard").status_code == 404
    assert client.post("/programmes/no-such-programme/standard", data={"add": "step:fee"}).status_code == 404


# ---- Edit programme: a dropped link is said, never silent (user addition to Q2) ------------------------


def _edit_fields(client: TestClient) -> list[tuple[str, str]]:
    return form_fields(client.get(f"/programmes/{PID}/edit").text, "programme-form")


def test_edit_keeps_links_and_says_nothing_when_no_requirement_goes(
    client: TestClient, config, probe: Path
) -> None:
    client.post(f"/programmes/{PID}/standard", data=form_fields(_preview(client), FORM))
    res = client.post(f"/programmes/{PID}", data=_edit_fields(client))
    assert res.status_code == 303
    assert [s.requirement_ids for s in _load(config).steps][1:4] == [["letters"], ["essays"], ["fee"]]


def test_edit_removing_a_linked_requirement_says_which_step_lost_its_link(
    client: TestClient, config, probe: Path
) -> None:
    client.post(f"/programmes/{PID}/standard", data=form_fields(_preview(client), FORM))
    fields = _edit_fields(client)
    index = next(
        k.split("-")[1]
        for k, v in fields
        if k.startswith("requirements-") and k.endswith("-id") and v == "fee"
    )
    res = client.post(f"/programmes/{PID}", data=set_field(fields, f"requirements-{index}-remove", "1"))
    assert res.status_code == 303, res.text[-400:]  # post/redirect/get: a reload never re-posts (r11 F1)
    assert re.fullmatch(rf"/programmes/{PID}\?notice=[\w-]+", res.headers["location"])
    page = client.get(res.headers["location"]).text
    assert "Pay the application fee is no longer linked to Application fee" in page
    assert 'id="programme-notice"' in page
    assert "no longer linked" not in client.get(res.headers["location"]).text  # shown once
    p = _load(config)
    assert "fee" not in [r.id for r in p.requirements]
    assert next(s for s in p.steps if s.label == "Pay the application fee").requirement_ids == []


# ---- data-integrity review round 11: a stale Edit form never deletes what was added since (F1) ---------


def test_a_stale_edit_form_is_refused_and_the_added_items_stay(
    client: TestClient, config, probe: Path
) -> None:
    stale = _edit_fields(client)  # tab 1 opens Edit
    client.post(f"/programmes/{PID}/standard", data=form_fields(_preview(client), FORM))  # tab 2 adds
    before = probe.read_bytes()
    res = client.post(f"/programmes/{PID}", data=stale)
    assert res.status_code == 409
    assert "This programme changed since the form was shown" in res.text and "Nothing was saved" in res.text
    assert probe.read_bytes() == before
    p = _load(config)
    assert len(p.steps) == 5 and len(p.documents) == 3
    fresh = dict(form_fields(res.text, "programme-form"))
    assert fresh["steps-4-label"] == "Submit the application"  # the form now shows the stored version


def test_replaying_the_dropped_link_edit_after_a_change_is_refused(
    client: TestClient, config, probe: Path
) -> None:
    keys = [("add", k) for k in ("step:essays", "step:fee")]
    client.post(f"/programmes/{PID}/standard", data=keys)
    fields = _edit_fields(client)
    index = next(
        k.split("-")[1]
        for k, v in fields
        if k.startswith("requirements-") and k.endswith("-id") and v == "fee"
    )
    removal = set_field(fields, f"requirements-{index}-remove", "1")
    assert client.post(f"/programmes/{PID}", data=removal).status_code == 303
    client.post(f"/programmes/{PID}/standard", data=[("add", "step:portal"), ("add", "document:transcript")])
    before = probe.read_bytes()
    assert client.post(f"/programmes/{PID}", data=removal).status_code == 409  # the same post again
    assert probe.read_bytes() == before
    p = _load(config)
    assert len(p.steps) == 3 and [d.id for d in p.documents] == ["transcript"]


def test_an_edit_without_the_forms_digest_is_refused(client: TestClient, probe: Path) -> None:
    fields = [(k, v) for k, v in _edit_fields(client) if k != "digest"]
    before = probe.read_bytes()
    assert client.post(f"/programmes/{PID}", data=fields).status_code == 409
    assert probe.read_bytes() == before


# ---- r11 F4: requirement_ids is written only when set (docs/01 §3) ------------------------------------


def test_a_step_added_on_edit_writes_no_empty_link(client: TestClient, probe: Path) -> None:
    fields = [*_edit_fields(client), ("steps-0-label", "My new step"), ("steps-0-order", "")]
    assert client.post(f"/programmes/{PID}", data=fields).status_code == 303
    text = probe.read_text(encoding="utf-8")
    assert "My new step" in text and "requirement_ids" not in text


def test_a_step_whose_last_link_is_dropped_loses_the_key(client: TestClient, probe: Path) -> None:
    client.post(f"/programmes/{PID}/standard", data=[("add", "step:fee")])
    assert "requirement_ids" in probe.read_text(encoding="utf-8")
    fields = _edit_fields(client)
    index = next(
        k.split("-")[1]
        for k, v in fields
        if k.startswith("requirements-") and k.endswith("-id") and v == "fee"
    )
    client.post(f"/programmes/{PID}", data=set_field(fields, f"requirements-{index}-remove", "1"))
    assert "requirement_ids" not in probe.read_text(encoding="utf-8")


def test_the_link_is_locked_while_re_research_runs(client: TestClient, probe: Path) -> None:
    """ui r7 F5 (user decision): the link leads to a page that writes, so it carries ``data-writes`` and the
    shared busy state locks it like Edit."""
    html = client.get(f"/programmes/{PID}").text
    link = re.search(r'<a [^>]*href="/programmes/probe-std/standard"[^>]*>', html).group(0)
    assert "data-writes" in link
