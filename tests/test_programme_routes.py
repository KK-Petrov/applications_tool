"""Programme page write routes (docs/03 §1, §4.2; docs/02 §6): toggles, documents, interviews, verify.

Each HTMX endpoint returns a partial when ``HX-Request`` is set and otherwise redirects back (no-JS).
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hub.store import Store

from helpers import tree_digest

PID = "princeton-mfin"
PAGE = f"/programmes/{PID}"
HX = {"HX-Request": "true"}


def _load(config, pid: str = PID):
    return Store.from_config(config).load_programme(config.storage.data_dir / "programmes" / f"{pid}.yaml")


# ---- pages -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("pid", ["princeton-mfin", "mit-sloan-mfin", "booth-mif", "columbia-msfe"])
def test_programme_page_renders(client: TestClient, pid: str) -> None:
    res = client.get(f"/programmes/{pid}")
    assert res.status_code == 200
    html = res.text
    for heading in (
        "Requirements",
        "Application steps",
        "Documents and score reports",
        "Interviews and assessments",
    ):
        assert heading in html
    assert "None" not in html and "NaN" not in html


def test_unknown_programme_is_404(client: TestClient) -> None:
    assert client.get("/programmes/no-such-programme").status_code == 404
    assert client.post("/programmes/no-such-programme/verify").status_code == 404


def test_dashboard_lists_programmes_in_deadline_order(client: TestClient) -> None:
    html = client.get("/").text
    positions = [
        html.index(name) for name in ("Princeton MFin", "MIT Sloan MFin", "Booth MiF", "Columbia MSFE")
    ]
    assert positions == sorted(positions)
    assert "46 days" in html
    assert "Request Princeton MFin recommendations by 3 Nov 2026" in html
    assert "3 letters needed, 0 confirmed" in html
    # Phase 3 replaced the placeholder with the engine's reminders (DECISIONS #106): the first is Princeton's
    upcoming = html[html.index('id="upcoming-title"') :]
    assert "3 Nov 2026</span> · Princeton MFin: request letters (3 needed)" in upcoming


# ---- requirements and steps ------------------------------------------------------------------------


def test_requirement_htmx_returns_partial_and_saves(client: TestClient, config) -> None:
    res = client.post(f"{PAGE}/requirements/cv", data={"done": "1"}, headers=HX)
    assert res.status_code == 200 and "<html" not in res.text
    assert "1 / 9" in res.text
    assert next(r for r in _load(config).requirements if r.id == "cv").done is True
    client.post(f"{PAGE}/requirements/cv", data={"done": "0"}, headers=HX)
    assert next(r for r in _load(config).requirements if r.id == "cv").done is False


def test_requirement_checkbox_with_hidden_default_takes_the_last_value(client: TestClient, config) -> None:
    client.post(f"{PAGE}/requirements/cv", data=[("done", "0"), ("done", "1")], headers=HX)
    assert next(r for r in _load(config).requirements if r.id == "cv").done is True


def test_requirement_plain_post_redirects_back(client: TestClient, config) -> None:
    res = client.post(f"{PAGE}/requirements/cv", data={"done": "1"})
    assert res.status_code == 303 and res.headers["location"] == PAGE


def test_step_toggle(client: TestClient, config) -> None:
    res = client.post(f"{PAGE}/steps/2", data={"done": "1"}, headers=HX)
    assert res.status_code == 200
    assert next(s for s in _load(config).steps if s.order == 2).done is True


def test_unknown_requirement_or_step_is_404(client: TestClient, data_dir: Path) -> None:
    before = tree_digest(data_dir)
    assert client.post(f"{PAGE}/requirements/nope", data={"done": "1"}).status_code == 404
    assert client.post(f"{PAGE}/steps/99", data={"done": "1"}).status_code == 404
    assert tree_digest(data_dir) == before


def test_comments_survive_a_toggle(client: TestClient, data_dir: Path) -> None:
    client.post(f"{PAGE}/requirements/cv", data={"done": "1"})
    text = (data_dir / "programmes" / f"{PID}.yaml").read_text(encoding="utf-8")
    assert text.startswith("# Seed data researched 30 Oct 2026.")


# ---- documents ---------------------------------------------------------------------------------------


def test_document_status_stamps_today(client: TestClient, config) -> None:
    res = client.post(f"{PAGE}/documents/gmat", data={"status": "sent"}, headers=HX)
    assert res.status_code == 200 and "<html" not in res.text
    doc = next(d for d in _load(config).documents if d.id == "gmat")
    assert doc.status == "sent"
    assert doc.updated == date(2026, 10, 30)  # FIXED_NOW in the display zone


def test_documents_progress_in_partial(client: TestClient) -> None:
    client.post(f"{PAGE}/documents/gmat", data={"status": "received"})
    res = client.post(f"{PAGE}/documents/english", data={"status": "waived"}, headers=HX)
    assert "2 / 4 received" in res.text


def test_invalid_document_status_is_refused(client: TestClient, data_dir: Path) -> None:
    before = tree_digest(data_dir)
    assert client.post(f"{PAGE}/documents/gmat", data={"status": "lost"}).status_code == 400
    assert client.post(f"{PAGE}/documents/nope", data={"status": "sent"}).status_code == 404
    assert tree_digest(data_dir) == before


# ---- interviews --------------------------------------------------------------------------------------

INTERVIEW = {
    "kind": "interview",
    "label": "Admissions interview",
    "date": "2027-01-20",
    "time": "14:30",
    "tz": "America/Chicago",
    "status": "invited",
    "notes": "Video call",
}


def test_add_interview(client: TestClient, config) -> None:
    res = client.post("/programmes/booth-mif/interviews", data=INTERVIEW, headers=HX)
    assert res.status_code == 200 and "<html" not in res.text
    assert "20 Jan 2027, 2:30 pm CT" in res.text
    (interview,) = _load(config, "booth-mif").interviews
    assert interview.start_local == datetime(2027, 1, 20, 14, 30)
    assert interview.tz == "America/Chicago"
    assert interview.id == "admissions-interview"
    assert interview.estimated is False
    assert interview.status == "invited"


def test_edit_interview_keeps_its_id(client: TestClient, config) -> None:
    data = {
        **INTERVIEW,
        "interview_id": "math-test",
        "label": "Math assessment test",
        "kind": "assessment",
        "status": "scheduled",
        "estimated": "1",
        "date": "2026-12-20",
        "time": "09:00",
        "tz": "America/New_York",
    }
    res = client.post(f"{PAGE}/interviews", data=data)
    assert res.status_code == 303
    (interview,) = _load(config).interviews
    assert interview.id == "math-test"
    assert interview.start_local == datetime(2026, 12, 20, 9, 0) and interview.estimated is True


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("tz", "Mars/Olympus", "time zone"),
        ("date", "", "date"),
        ("time", "25:00", "time"),
        ("label", "", "label"),
    ],
)
def test_invalid_interview_shows_an_inline_error_and_writes_nothing(
    client: TestClient, data_dir: Path, field: str, value: str, message: str
) -> None:
    before = tree_digest(data_dir)
    res = client.post("/programmes/booth-mif/interviews", data={**INTERVIEW, field: value}, headers=HX)
    assert res.status_code == 200
    assert message in res.text.lower()
    assert tree_digest(data_dir) == before


def test_interview_ids_stay_unique(client: TestClient, config) -> None:
    client.post("/programmes/booth-mif/interviews", data=INTERVIEW)
    client.post("/programmes/booth-mif/interviews", data=INTERVIEW)
    assert [i.id for i in _load(config, "booth-mif").interviews] == [
        "admissions-interview",
        "admissions-interview-2",
    ]


# ---- verify (docs/02 §6) -----------------------------------------------------------------------------


def test_verify_sets_programme_and_every_deadline(client: TestClient, config) -> None:
    res = client.post(f"{PAGE}/verify")
    assert res.status_code == 303 and res.headers["location"] == PAGE
    p = _load(config)
    assert p.verification == "verified"
    assert p.verified_on == date(2026, 10, 30)
    assert p.verification_note is None
    assert all(d.verification == "verified" for d in p.deadlines)
    page = client.get(PAGE).text
    assert "Verified" in page and "Last cycle's date" not in page


def test_verify_without_sources_is_refused_inline(client: TestClient, config, data_dir: Path) -> None:
    store = Store.from_config(config)
    p = _load(config, "columbia-msfe")
    store.save_programme(p.model_copy(update={"sources": []}))
    before = tree_digest(data_dir)
    res = client.post("/programmes/columbia-msfe/verify")
    assert res.status_code == 200
    assert "Add a source before marking verified" in res.text
    assert tree_digest(data_dir) == before


def test_verify_ignores_any_submitted_fields(client: TestClient, config) -> None:
    client.post(f"{PAGE}/verify", data={"verified_on": "2020-01-01", "verification_note": "x"})
    p = _load(config)
    assert p.verified_on == date(2026, 10, 30) and p.verification_note is None


def test_failed_htmx_action_returns_an_inline_message(client: TestClient, data_dir: Path) -> None:
    (data_dir / "programmes" / "booth-mif.yaml").write_text("id: booth-mif\nname: [broken", encoding="utf-8")
    res = client.post("/programmes/booth-mif/requirements/cv", data={"done": "1"}, headers=HX)
    # the file cannot be loaded, so there is no card to show: the page-level toast names the file
    assert res.status_code == 409
    assert 'role="alert"' in res.text and "Nothing was saved" in res.text and "<html" not in res.text
    assert "programmes/booth-mif.yaml could not be loaded" in res.text
    # a refused change re-renders the card from what is stored, with the message inside it (docs/03 §5)
    res = client.post(f"{PAGE}/documents/gmat", data={"status": "lost"}, headers=HX)
    assert res.status_code == 200 and 'id="documents"' in res.text
    assert "Nothing was saved." in res.text and res.text.count("Nothing was saved") == 1


def test_pages_have_the_action_error_region_and_htmx_config(client: TestClient) -> None:
    html = client.get(PAGE).text
    assert 'id="action-error"' in html and 'name="htmx-config"' in html and "#action-error" in html


# ---- DECISIONS #32: back to "not_requested" clears `updated` --------------------------------------------


def _gmat_row(html: str) -> str:
    start = html.index("GMAT official score report")
    return html[start : html.index("</tr>", start)]


@pytest.mark.parametrize("htmx", [True, False], ids=["htmx", "no-js"])
def test_document_back_to_not_requested_clears_updated(
    client: TestClient, config, data_dir: Path, htmx: bool
) -> None:
    headers = HX if htmx else {}
    res = client.post(f"{PAGE}/documents/gmat", data={"status": "sent"}, headers=headers)
    assert res.status_code == (200 if htmx else 303)
    doc = next(d for d in _load(config).documents if d.id == "gmat")
    assert (doc.status, doc.updated) == ("sent", date(2026, 10, 30))
    assert "30 Oct 2026" in _gmat_row(client.get(PAGE).text)

    res = client.post(f"{PAGE}/documents/gmat", data={"status": "not_requested"}, headers=headers)
    assert res.status_code == (200 if htmx else 303)
    if htmx:
        assert "—" in _gmat_row(res.text) and "30 Oct 2026" not in _gmat_row(res.text)
    doc = next(d for d in _load(config).documents if d.id == "gmat")
    assert (doc.status, doc.updated) == ("not_requested", None)
    text = (data_dir / "programmes" / f"{PID}.yaml").read_text(encoding="utf-8")
    block = text[text.index("  - id: gmat\n    label: GMAT official") :].split("  - id:", 2)[1]
    assert "    updated: null\n" in block
    row = _gmat_row(client.get(PAGE).text)  # after reload
    assert "—" in row and "30 Oct 2026" not in row


def test_never_set_document_shows_a_dash(client: TestClient) -> None:
    assert "—" in _gmat_row(client.get(PAGE).text)


def test_other_status_changes_still_stamp_today(client: TestClient, config) -> None:
    client.post(f"{PAGE}/documents/gmat", data={"status": "sent"})
    client.post(f"{PAGE}/documents/gmat", data={"status": "not_requested"})
    client.post(f"{PAGE}/documents/gmat", data={"status": "received"})
    doc = next(d for d in _load(config).documents if d.id == "gmat")
    assert (doc.status, doc.updated) == ("received", date(2026, 10, 30))


def test_unchanged_document_status_writes_nothing(client: TestClient, data_dir: Path) -> None:
    before = tree_digest(data_dir)
    client.post(f"{PAGE}/documents/gmat", data={"status": "not_requested"})
    assert tree_digest(data_dir) == before


def test_unchanged_interview_save_keeps_a_hand_written_shape(client: TestClient, data_dir: Path) -> None:
    path = data_dir / "programmes" / "booth-mif.yaml"
    text = path.read_text(encoding="utf-8").replace(
        "interviews: []",
        "interviews:\n  - id: chat\n    kind: interview\n    label: Chat\n"
        "    start_local: 2027-01-20T14:30\n    tz: America/Chicago",
    )
    path.write_text(text, encoding="utf-8")
    before = path.read_bytes()
    form = {
        "interview_id": "chat",
        "kind": "interview",
        "label": "Chat",
        "date": "2027-01-20",
        "time": "14:30",
        "tz": "America/Chicago",
        "status": "not_invited",
        "notes": "",
    }
    assert client.post("/programmes/booth-mif/interviews", data=form).status_code == 303
    assert path.read_bytes() == before
    assert (
        client.post("/programmes/booth-mif/interviews", data={**form, "label": "Video chat"}).status_code
        == 303
    )
    assert path.read_bytes() == before.replace(
        b"label: Chat", b"label: Video chat"
    )  # one-line diff, CRLF kept


# ---- second UI review: failed saves are shown in the card, which shows what is stored (DECISIONS #33) ----


@pytest.fixture(autouse=False)
def no_retry_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Locked-file tests still retry; they just skip the real waits between attempts."""
    monkeypatch.setattr("hub.store.time.sleep", lambda _s: None)


@pytest.fixture
def locked(data_dir: Path, no_retry_wait: None):
    """Princeton's file made read-only, as when OneDrive or another program holds it."""
    import os
    import stat

    path = data_dir / "programmes" / f"{PID}.yaml"
    os.chmod(path, stat.S_IREAD)
    yield path
    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)


def test_locked_file_htmx_shows_message_in_card_with_stored_state(client: TestClient, locked: Path) -> None:
    before = locked.read_bytes()
    res = client.post(f"{PAGE}/requirements/cv", data={"done": "1"}, headers=HX)
    assert res.status_code == 200 and 'id="requirements"' in res.text
    assert "Nothing was saved." in res.text and "locked" in res.text
    assert 'id="req-cv" name="done" value="1">' in res.text  # unticked: the stored state
    assert "Internal Server Error" not in res.text
    assert locked.read_bytes() == before


def test_locked_file_no_js_shows_the_page_with_the_message(client: TestClient, locked: Path) -> None:
    res = client.post(f"{PAGE}/documents/gmat", data={"status": "sent"})
    assert res.status_code == 409
    assert "<html" in res.text and "Nothing was saved." in res.text and "locked" in res.text


def test_locked_file_verify_and_form_say_nothing_was_saved(client: TestClient, locked: Path) -> None:
    res = client.post(f"{PAGE}/verify")
    assert res.status_code == 409 and "Nothing was saved." in res.text  # 409 like every failed action (#34)
    from helpers import form_fields, set_field

    fields = set_field(form_fields(client.get(f"{PAGE}/edit").text, "programme-form"), "letters_note", "x")
    res = client.post(PAGE, data=fields)
    assert res.status_code == 200 and "nothing was saved" in res.text.lower() and "locked" in res.text
    assert res.text.lower().count("nothing was saved") == 1 and "Data file" in res.text


def test_locked_settings_display_zone_gives_a_readable_error(
    client: TestClient, data_dir: Path, no_retry_wait: None
) -> None:
    import os
    import stat

    path = data_dir / "settings.yaml"
    os.chmod(path, stat.S_IREAD)
    try:
        res = client.post("/display-tz", data={"display_tz": "Europe/Amsterdam"}, headers=HX)
        assert res.status_code == 409 and "Nothing was saved" in res.text and "settings.yaml" in res.text
    finally:
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)


def test_malformed_request_is_readable_not_json(client: TestClient) -> None:
    res = client.post(f"{PAGE}/steps/not-a-number", data={"done": "1"}, headers=HX)
    assert res.status_code == 400 and "The request was not understood." in res.text
    assert not res.text.lstrip().startswith("{")


def test_broken_programme_page_says_the_file_could_not_be_loaded(client: TestClient, data_dir: Path) -> None:
    (data_dir / "programmes" / "booth-mif.yaml").write_text("id: booth-mif\nname: [broken", encoding="utf-8")
    res = client.get("/programmes/booth-mif")
    assert res.status_code == 409
    assert "could not be loaded" in res.text and "There is no page at this address" not in res.text


def test_interview_swaps_set_focus_targets(client: TestClient) -> None:
    opened = client.get(f"{PAGE}/interviews/form", headers=HX).text
    assert 'id="iv-kind" name="kind" autofocus' in opened
    bad = client.post(f"{PAGE}/interviews", data={**INTERVIEW, "label": ""}, headers=HX).text
    assert 'id="iv-error"' in bad and "autofocus" in bad.split('id="iv-error"')[1].split(">")[0]
    saved = client.post("/programmes/booth-mif/interviews", data=INTERVIEW, headers=HX).text
    assert 'id="iv-add"' in saved and "autofocus" in saved.split('id="iv-add"')[1].split(">")[0]


# ---- third data-integrity review: a failed write never displays the unsaved state ----------------------


def test_locked_verify_still_shows_unverified(client: TestClient, locked: Path) -> None:
    res = client.post(f"{PAGE}/verify")
    assert res.status_code == 409 and "Nothing was saved." in res.text
    assert "badge-unverified" in res.text and "Mark verified</button>" in res.text
    assert "badge-verified" not in res.text and "You checked this programme" not in res.text


def test_locked_interview_add_does_not_list_the_unsaved_interview(client: TestClient, locked: Path) -> None:
    res = client.post(f"{PAGE}/interviews", data={**INTERVIEW, "label": "Unsaved interview"}, headers=HX)
    assert res.status_code == 200 and "Nothing was saved." in res.text
    assert "interview_id=unsaved-interview" not in res.text  # not in the stored list
    assert 'value="Unsaved interview"' in res.text  # still in the form, so nothing typed is lost


def test_locked_messages_name_the_file(client: TestClient, locked: Path) -> None:
    res = client.post(f"{PAGE}/requirements/cv", data={"done": "1"}, headers=HX)
    assert "programmes/princeton-mfin.yaml could not be written" in res.text
