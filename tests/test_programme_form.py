"""Add / edit programme form (docs/03 §1, docs/01 §3, docs/02 §6; DECISIONS #13, #21).

Field names: top-level fields by name; repeatable rows as ``<group>-<i>-<field>`` with groups sources,
deadlines, requirements, steps, documents, essays, courses; ``<group>-<i>-remove=1`` drops a row;
a submit ``_add=<group>`` re-renders the form with one more row and saves nothing.
Tests post exactly what the rendered page would submit (helpers.form_fields).
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ruamel.yaml import YAML

from hub.store import Store

from helpers import form_fields, set_field, tree_digest

FORM = "programme-form"
PID = "princeton-mfin"


def _edit_fields(client: TestClient, pid: str = PID) -> list[tuple[str, str]]:
    res = client.get(f"/programmes/{pid}/edit")
    assert res.status_code == 200
    return form_fields(res.text, FORM)


def _new_fields(client: TestClient) -> list[tuple[str, str]]:
    res = client.get("/programmes/new")
    assert res.status_code == 200
    return form_fields(res.text, FORM)


def _load(config, pid: str = PID):
    return Store.from_config(config).load_programme(config.storage.data_dir / "programmes" / f"{pid}.yaml")


def _fill_new(fields: list[tuple[str, str]], **extra: str) -> list[tuple[str, str]]:
    values = {
        "name": "Example Institute Master in Quantitative Finance",
        "short_name": "Example MQF",
        "school": "Example Institute",
        "unit": "School of Finance",
        "aliases": "Example Institute\nEIQF, Quant Lab",
        "verification_note": "Entered from the school page",
        "deadlines-0-label": "Application deadline",
        "deadlines-0-date": "2027-02-01",
        "deadlines-0-time": "17:00",
        "deadlines-0-tz": "America/New_York",
        "deadlines-0-source_url": "https://example.com/apply",
        "sources-0-url": "https://example.com/apply",
        "sources-0-title": "How to apply",
        "requirements-0-label": "Resume / CV",
        "steps-0-label": "Create an account",
        "documents-0-label": "GMAT score report",
        "documents-0-method": "GMAC",
        "essays-0-title": "Why this programme",
        "essays-0-word_limit": "500",
    }
    values.update(extra)
    for name, value in values.items():
        fields = set_field(fields, name, value)
    return fields


# ---- R1: due_local round-trip through the form --------------------------------------------------------


def test_edit_form_prefills_due_local_exactly(client: TestClient) -> None:
    fields = dict(_edit_fields(client))
    assert fields["deadlines-0-date"] == "2026-12-15"
    assert fields["deadlines-0-time"] == "23:59"  # wall-clock in the school zone, never converted
    assert fields["deadlines-0-tz"] == "America/New_York"


def test_unchanged_edit_writes_nothing(client: TestClient, data_dir: Path) -> None:
    before = tree_digest(data_dir)
    res = client.post(f"/programmes/{PID}", data=_edit_fields(client))
    assert res.status_code == 303
    assert tree_digest(data_dir) == before


@pytest.mark.parametrize("pid", ["mit-sloan-mfin", "booth-mif", "columbia-msfe"])
def test_unchanged_edit_writes_nothing_for_every_seed(client: TestClient, data_dir: Path, pid: str) -> None:
    before = tree_digest(data_dir)
    assert client.post(f"/programmes/{pid}", data=_edit_fields(client, pid)).status_code == 303
    assert tree_digest(data_dir) == before


def test_editing_another_field_keeps_due_local_tz_and_verification(
    client: TestClient, data_dir: Path, config
) -> None:
    store = Store.from_config(config)
    p = _load(config)
    p.deadlines[0].verification = "verified"
    store.save_programme(p)
    fields = set_field(_edit_fields(client), "letters_note", "Changed note")
    assert client.post(f"/programmes/{PID}", data=fields).status_code == 303
    text = (data_dir / "programmes" / f"{PID}.yaml").read_text(encoding="utf-8")
    assert "    due_local: 2026-12-15T23:59\n" in text
    raw = YAML(typ="safe").load(text)
    assert raw["deadlines"][0]["due_local"] == "2026-12-15T23:59"
    assert raw["deadlines"][0]["tz"] == "America/New_York"
    assert raw["deadlines"][0]["verification"] == "verified"
    assert raw["letters_note"] == "Changed note"


# ---- R2: DECISIONS #13 (at least one deadline, each with tz and source_url) ----------------------------


def _assert_refused(res, data_dir: Path, before: dict[str, str], field: str) -> None:
    assert res.status_code == 200
    assert "<form" in res.text and f'id="{FORM}"' in res.text
    assert field in res.text  # the inline error names the field
    assert tree_digest(data_dir) == before
    assert not list(data_dir.rglob("*.tmp"))


def test_new_programme_with_no_deadline_rows_is_refused(client: TestClient, data_dir: Path) -> None:
    fields = [(k, v) for k, v in _fill_new(_new_fields(client)) if not k.startswith("deadlines-")]
    before = tree_digest(data_dir)
    res = client.post("/programmes", data=fields)
    _assert_refused(res, data_dir, before, "deadlines")
    assert "at least one deadline" in res.text
    assert "Example MQF" in res.text  # what was typed is kept


def test_new_programme_with_only_blank_deadline_rows_is_refused(client: TestClient, data_dir: Path) -> None:
    fields = _fill_new(_new_fields(client))
    for name in ("label", "date", "time", "tz", "source_url"):
        fields = set_field(fields, f"deadlines-0-{name}", "")
    before = tree_digest(data_dir)
    _assert_refused(client.post("/programmes", data=fields), data_dir, before, "deadlines")


def test_deadline_without_tz_is_refused(client: TestClient, data_dir: Path) -> None:
    fields = _fill_new(_new_fields(client), **{"deadlines-0-tz": ""})
    before = tree_digest(data_dir)
    _assert_refused(client.post("/programmes", data=fields), data_dir, before, "deadlines[0].tz")


def test_deadline_without_source_url_is_refused(client: TestClient, data_dir: Path) -> None:
    fields = _fill_new(_new_fields(client), **{"deadlines-0-source_url": ""})
    before = tree_digest(data_dir)
    _assert_refused(client.post("/programmes", data=fields), data_dir, before, "deadlines[0].source_url")


def test_edit_removing_the_only_deadline_is_refused(client: TestClient, data_dir: Path) -> None:
    fields = set_field(_edit_fields(client), "deadlines-0-remove", "1")
    before = tree_digest(data_dir)
    _assert_refused(client.post(f"/programmes/{PID}", data=fields), data_dir, before, "deadlines")


def test_edit_clearing_tz_is_refused(client: TestClient, data_dir: Path) -> None:
    fields = set_field(_edit_fields(client), "deadlines-0-tz", "")
    before = tree_digest(data_dir)
    _assert_refused(client.post(f"/programmes/{PID}", data=fields), data_dir, before, "deadlines[0].tz")


# ---- adding a programme --------------------------------------------------------------------------------


def test_new_programme_is_saved_unverified(client: TestClient, config) -> None:
    fields = _fill_new(_new_fields(client))
    res = client.post("/programmes", data=fields)
    assert res.status_code == 303 and res.headers["location"] == "/programmes/example-mqf"
    p = _load(config, "example-mqf")
    assert p.verification == "unverified" and p.verified_on is None
    assert p.verification_note == "Entered from the school page"
    assert p.aliases == ["Example Institute", "EIQF, Quant Lab"]  # one per line; commas kept
    (d,) = p.deadlines
    assert (d.due_local, d.tz, d.time_known) == (datetime(2027, 2, 1, 17, 0), "America/New_York", True)
    assert d.verification == "unverified" and d.source_url == "https://example.com/apply"
    assert d.id == "application-deadline"
    assert [r.id for r in p.requirements] == ["resume-cv"]
    assert [(s.order, s.label) for s in p.steps] == [(1, "Create an account")]
    assert p.documents[0].status == "not_requested"
    assert p.essays[0].word_limit == 500
    assert p.letters_required is None  # blank → config default, shown as "assumed"


def test_new_programme_cannot_be_created_verified(client: TestClient, config) -> None:
    fields = _fill_new(_new_fields(client))
    fields += [
        ("verification", "verified"),
        ("verified_on", "2026-10-30"),
        ("deadlines-0-verification", "verified"),
    ]
    assert client.post("/programmes", data=fields).status_code == 303
    p = _load(config, "example-mqf")
    assert p.verification == "unverified" and p.deadlines[0].verification == "unverified"


def test_blank_time_uses_the_assumed_time_and_flags_it(client: TestClient, config) -> None:
    fields = _fill_new(_new_fields(client), **{"deadlines-0-time": ""})
    assert client.post("/programmes", data=fields).status_code == 303
    (d,) = _load(config, "example-mqf").deadlines
    assert d.due_local == datetime(2027, 2, 1, 23, 59) and d.time_known is False


def test_explicit_id_and_duplicate_id(client: TestClient, data_dir: Path) -> None:
    fields = _fill_new(_new_fields(client), id="princeton-mfin")
    before = tree_digest(data_dir)
    res = client.post("/programmes", data=fields)
    _assert_refused(res, data_dir, before, "id")
    assert "already exists" in res.text
    res = client.post("/programmes", data=_fill_new(_new_fields(client), id="eiqf-2027"))
    assert res.headers["location"] == "/programmes/eiqf-2027"


def test_invalid_id_is_refused(client: TestClient, data_dir: Path) -> None:
    before = tree_digest(data_dir)
    res = client.post("/programmes", data=_fill_new(_new_fields(client), id="Bad ID!"))
    _assert_refused(res, data_dir, before, "id")


def test_missing_name_is_refused(client: TestClient, data_dir: Path) -> None:
    before = tree_digest(data_dir)
    res = client.post("/programmes", data=_fill_new(_new_fields(client), name=""))
    _assert_refused(res, data_dir, before, "name")


def test_add_row_rerenders_and_saves_nothing(client: TestClient, data_dir: Path) -> None:
    before = tree_digest(data_dir)
    fields = _edit_fields(client) + [("_add", "deadlines")]
    res = client.post(f"/programmes/{PID}", data=fields)
    assert res.status_code == 200
    rerendered = dict(form_fields(res.text, FORM))
    assert "deadlines-1-label" in rerendered and rerendered["deadlines-1-label"] == ""
    assert rerendered["deadlines-0-date"] == "2026-12-15"
    assert tree_digest(data_dir) == before


# ---- editing a programme -------------------------------------------------------------------------------


def test_edit_keeps_done_flags_and_document_status(client: TestClient, config) -> None:
    client.post(f"/programmes/{PID}/requirements/cv", data={"done": "1"})
    client.post(f"/programmes/{PID}/steps/2", data={"done": "1"})
    client.post(f"/programmes/{PID}/documents/gmat", data={"status": "sent"})
    fields = set_field(_edit_fields(client), "requirements-1-label", "Résumé / CV")
    assert client.post(f"/programmes/{PID}", data=fields).status_code == 303
    p = _load(config)
    cv = next(r for r in p.requirements if r.id == "cv")
    assert cv.label == "Résumé / CV" and cv.done is True
    assert next(s for s in p.steps if s.order == 2).done is True
    assert next(d for d in p.documents if d.id == "gmat").status == "sent"


def test_remove_row_deletes_it(client: TestClient, config) -> None:
    fields = _edit_fields(client)
    index = next(
        k.split("-")[1]
        for k, v in fields
        if k.startswith("requirements-") and k.endswith("-id") and v == "fee"
    )
    fields = set_field(fields, f"requirements-{index}-remove", "1")
    assert client.post(f"/programmes/{PID}", data=fields).status_code == 303
    assert "fee" not in [r.id for r in _load(config).requirements]


def test_editing_deadline_time_resets_only_that_deadline(client: TestClient, config) -> None:
    client.post(f"/programmes/{PID}/verify")
    fields = set_field(_edit_fields(client), "deadlines-0-time", "17:00")
    assert client.post(f"/programmes/{PID}", data=fields).status_code == 303
    p = _load(config)
    assert p.deadlines[0].due_local == datetime(2026, 12, 15, 17, 0)
    assert p.deadlines[0].verification == "unverified"
    assert p.verification == "verified"  # DECISIONS #21: programme stays verified
    page = client.get(f"/programmes/{PID}").text
    assert "Deadline changed, not checked" in page


@pytest.mark.parametrize(
    ("field", "value"), [("deadlines-0-date", "2026-12-14"), ("deadlines-0-tz", "America/Chicago")]
)
def test_editing_deadline_date_or_zone_resets_it(client: TestClient, config, field: str, value: str) -> None:
    client.post(f"/programmes/{PID}/verify")
    assert (
        client.post(f"/programmes/{PID}", data=set_field(_edit_fields(client), field, value)).status_code
        == 303
    )
    assert _load(config).deadlines[0].verification == "unverified"


def test_editing_deadline_label_keeps_verification(client: TestClient, config) -> None:
    client.post(f"/programmes/{PID}/verify")
    fields = set_field(_edit_fields(client), "deadlines-0-label", "Final deadline")
    assert client.post(f"/programmes/{PID}", data=fields).status_code == 303
    assert _load(config).deadlines[0].verification == "verified"


def test_edit_ignores_a_submitted_verification(client: TestClient, config) -> None:
    fields = _edit_fields(client) + [("verification", "verified"), ("verified_on", "2026-10-30")]
    assert client.post(f"/programmes/{PID}", data=fields).status_code == 303
    assert _load(config).verification == "unverified"


def test_edit_keeps_the_file_comment(client: TestClient, data_dir: Path) -> None:
    fields = set_field(_edit_fields(client), "letters_note", "Changed")
    client.post(f"/programmes/{PID}", data=fields)
    text = (data_dir / "programmes" / f"{PID}.yaml").read_text(encoding="utf-8")
    assert text.startswith("# Seed data researched 30 Oct 2026.")


def test_edit_page_cannot_change_the_id(client: TestClient, data_dir: Path) -> None:
    fields = _edit_fields(client) + [("id", "hijack")]
    assert client.post(f"/programmes/{PID}", data=fields).status_code == 303
    assert not (data_dir / "programmes" / "hijack.yaml").exists()


# ---- regressions from the Phase 1 data-integrity review: a hand-written file keeps its shape -----------

MINIMAL = """# hand-written by the owner
id: probe-min
name: Probe Institute Master in Finance
short_name: Probe MFin
school: Probe Institute
aliases:
  - Probe, GSAS
deadlines:
  - id: main
    label: Application deadline
    due_local: 2027-02-01T17:00
    tz: America/New_York
    source_url: https://example.com/apply
sources:
  - url: https://example.com/apply
    title: Apply
steps:
  - order: 2
    label: Second written first
  - order: 5
    label: Fifth
requirements:
  - id: cv
    label: CV
"""


@pytest.fixture
def minimal(data_dir: Path) -> Path:
    path = data_dir / "programmes" / "probe-min.yaml"
    path.write_text(MINIMAL, encoding="utf-8")
    return path


def test_unchanged_edit_of_a_hand_written_file_writes_nothing(client: TestClient, minimal: Path) -> None:
    before = minimal.read_bytes()
    assert client.post("/programmes/probe-min", data=_edit_fields(client, "probe-min")).status_code == 303
    assert minimal.read_bytes() == before


def test_one_change_to_a_hand_written_file_is_a_one_line_diff(client: TestClient, minimal: Path) -> None:
    fields = set_field(_edit_fields(client, "probe-min"), "school", "Probe University")
    assert client.post("/programmes/probe-min", data=fields).status_code == 303
    after = minimal.read_text(encoding="utf-8")
    assert after == MINIMAL.replace("school: Probe Institute", "school: Probe University")


def test_alias_with_a_comma_round_trips(client: TestClient, config, minimal: Path) -> None:
    assert dict(_edit_fields(client, "probe-min"))["aliases"] == "Probe, GSAS"
    assert _load(config, "probe-min").aliases == ["Probe, GSAS"]


def test_step_orders_are_never_renumbered(client: TestClient, config, minimal: Path) -> None:
    fields = set_field(_edit_fields(client, "probe-min"), "steps-0-label", "Second (renamed)")
    fields = fields + [("_unused", "")]
    fields = set_field(fields, "steps-2-label", "A new step")
    assert client.post("/programmes/probe-min", data=fields).status_code == 303
    assert [(s.order, s.label) for s in _load(config, "probe-min").steps] == [
        (2, "Second (renamed)"),
        (5, "Fifth"),
        (6, "A new step"),
    ]


def test_verify_changes_only_the_verification_fields(client: TestClient, minimal: Path) -> None:
    assert client.post("/programmes/probe-min/verify").status_code == 303
    after = minimal.read_text(encoding="utf-8")
    expected = MINIMAL.replace(
        "    source_url: https://example.com/apply\n",
        "    source_url: https://example.com/apply\n    verification: verified\n",
    )
    assert after.startswith(expected.split("sources:")[0].split("deadlines:")[0])
    added = set(after.splitlines()) - set(MINIMAL.splitlines())
    assert added == {"    verification: verified", "verification: verified", "verified_on: 2026-10-30"}


def test_non_finite_fee_is_refused(client: TestClient, data_dir: Path) -> None:
    before = tree_digest(data_dir)
    fields = set_field(_edit_fields(client), "fee_amount", "inf")
    res = client.post(f"/programmes/{PID}", data=fields)
    _assert_refused(res, data_dir, before, "fee.amount")


def test_form_errors_name_fields_in_words(client: TestClient) -> None:
    fields = _fill_new(_new_fields(client), **{"deadlines-0-tz": "", "short_name": ""})
    html = client.post("/programmes", data=fields).text
    assert "Deadline 1 · Time zone" in html and "Short name" in html
    assert "<code>deadlines[0].tz</code>" not in html


def test_blank_form_does_not_blame_the_blank_id(client: TestClient) -> None:
    html = client.post("/programmes", data=_new_fields(client)).text
    assert 'data-field="name"' in html and 'data-field="id"' not in html


# ---- DECISIONS #30: a changed source URL also resets the deadline; time_known alone does not ------------


def test_editing_deadline_source_url_resets_it(client: TestClient, config) -> None:
    client.post(f"/programmes/{PID}/verify")
    fields = set_field(
        _edit_fields(client), "deadlines-0-source_url", "https://bcf.princeton.edu/new-deadline-page"
    )
    assert client.post(f"/programmes/{PID}", data=fields).status_code == 303
    p = _load(config)
    assert p.deadlines[0].source_url == "https://bcf.princeton.edu/new-deadline-page"
    assert p.deadlines[0].verification == "unverified"
    assert p.verification == "verified"
    assert "Deadline changed, not checked" in client.get(f"/programmes/{PID}").text


def test_toggling_time_not_confirmed_keeps_verification(client: TestClient, config) -> None:
    client.post(f"/programmes/{PID}/verify")
    fields = set_field(_edit_fields(client), "deadlines-0-time_unknown", "1")  # same 23:59, flag only
    assert client.post(f"/programmes/{PID}", data=fields).status_code == 303
    d = _load(config).deadlines[0]
    assert d.time_known is False and d.verification == "verified"


def test_removing_a_step_keeps_comments_with_their_step(client: TestClient, data_dir: Path) -> None:
    path = data_dir / "programmes" / "probe-min.yaml"
    path.write_text(
        MINIMAL.replace("  - order: 5\n", "  - order: 5  # keep me with Fifth\n"), encoding="utf-8"
    )
    fields = set_field(_edit_fields(client, "probe-min"), "steps-0-remove", "1")
    assert client.post("/programmes/probe-min", data=fields).status_code == 303
    text = path.read_text(encoding="utf-8")
    assert "Second written first" not in text
    assert "  - order: 5  # keep me with Fifth\n    label: Fifth\n" in text


def test_short_name_without_letters_asks_for_an_id(client: TestClient, data_dir: Path) -> None:
    before = tree_digest(data_dir)
    res = client.post("/programmes", data=_fill_new(_new_fields(client), short_name="!!!", name="!!!"))
    _assert_refused(res, data_dir, before, "id")
    assert "needs at least 2 letters or digits" in res.text
    assert "String should match pattern" not in res.text


def test_bad_url_message_is_in_words(client: TestClient, data_dir: Path) -> None:
    before = tree_digest(data_dir)
    res = client.post(
        "/programmes", data=_fill_new(_new_fields(client), **{"deadlines-0-source_url": "notaurl"})
    )
    _assert_refused(res, data_dir, before, "deadlines[0].source_url")
    assert "starting with http:// or https://" in res.text and "should match pattern" not in res.text


@pytest.mark.parametrize(
    ("field", "value", "label", "words"),
    [
        ("letters_required", "-1", "Letters required", "must be at least 0"),
        ("fee_amount", "-5", "Fee amount", "must be at least 0"),
        ("essays-0-word_limit", "0", "Essay 1 · Word limit", "must be at least 1"),
        ("ai_policy_source_url", "ftp://x", "AI-use policy source URL", "starting with http://"),
    ],
)
def test_form_errors_never_show_pydantic_text(
    client: TestClient, data_dir: Path, field: str, value: str, label: str, words: str
) -> None:
    before = tree_digest(data_dir)
    res = client.post("/programmes", data=_fill_new(_new_fields(client), **{field: value}))
    assert res.status_code == 200 and tree_digest(data_dir) == before
    assert label in res.text and words in res.text
    assert "Input should" not in res.text and "should match pattern" not in res.text
    tag = re.search(rf'<input[^>]*\bname="{re.escape(field)}"[^>]*>', res.text)
    assert tag is not None and 'aria-invalid="true"' in tag.group(0), f"{field} is not marked invalid"


def test_one_letter_short_name_message_is_accurate(client: TestClient) -> None:
    html = client.post("/programmes", data=_fill_new(_new_fields(client), short_name="a", name="a")).text
    assert "needs at least 2 letters or digits" in html and "no letters" not in html


def test_required_message_does_not_repeat_the_label(client: TestClient) -> None:
    html = client.post("/programmes", data=_fill_new(_new_fields(client), name="")).text
    assert "Programme name</strong>: is required" in html


def test_messages_have_no_python_formatting(client: TestClient) -> None:
    html = client.post(
        "/programmes",
        data=_fill_new(_new_fields(client), fee_amount="-5", **{"deadlines-0-tz": "Mars/Base"}),
    ).text
    assert "must be at least 0.0" not in html and "Mars/Base is not an IANA time zone" in html
    assert "'Mars/Base'" not in html and "&#39;Mars/Base&#39;" not in html


def test_bad_checked_date_marks_its_input(client: TestClient) -> None:
    html = client.post(
        "/programmes", data=_fill_new(_new_fields(client), **{"sources-0-checked": "31/12/2026"})
    ).text
    tag = re.search(r'<input[^>]*\bname="sources-0-checked"[^>]*>', html)
    assert tag is not None and 'aria-invalid="true"' in tag.group(0)


def test_edit_keeps_official_domains(client: TestClient, data_dir: Path) -> None:
    fields = set_field(_edit_fields(client, "booth-mif"), "letters_note", "Changed")
    assert client.post("/programmes/booth-mif", data=fields).status_code == 303
    text = (data_dir / "programmes" / "booth-mif.yaml").read_text(encoding="utf-8")
    assert "official_domains:\n  - chicagobooth.edu\n  - uchicago.edu\n" in text
