"""Recommender sheet routes (docs/03 §1, §4.3–4.4; Phase 2a): sheet, status cells, add/edit, panel, CSV.

Fixture data: tests/fixtures/data (professors jansen and whitfield, four programmes); today is Friday
30 October 2026 in New York. Columns are ordered by deadline: Princeton, MIT, Booth, Columbia.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from helpers import FormClient, form_fields, long_description, set_field, tree_digest

HX = {"HX-Request": "true"}
JANSEN = "e.jansen@example.org"
WHITFIELD = "s.whitfield@example.org"


def _load(data_dir: Path, pid: str) -> dict:
    return YAML(typ="safe").load((data_dir / "professors" / f"{pid}.yaml").read_text(encoding="utf-8"))


def _cell(html: str, prof: str, programme: str) -> str:
    match = re.search(rf'<td[^>]*data-cell="{prof}:{programme}"[^>]*>(.*?)</td>', html, re.S)
    assert match, f"no cell {prof}:{programme}"
    return match.group(0)


def _status(html: str, prof: str, programme: str) -> str:
    return re.search(r'data-status="([a-z_]+)"', _cell(html, prof, programme)).group(1)


# ---- the sheet -----------------------------------------------------------------------------------


def test_sheet_lists_professors_with_columns_ordered_by_deadline(client: FormClient) -> None:
    html = client.get("/recommenders").text
    assert "Prof. Dr. Eva Jansen" in html and "Prof. Sam Whitfield" in html
    order = re.findall(r'<th[^>]*data-programme="([a-z0-9-]+)"', html)
    assert order == ["princeton-mfin", "mit-sloan-mfin", "booth-mif", "columbia-msfe"]
    assert "2 professors" in html


def test_cells_show_status_or_dash(client: FormClient) -> None:
    html = client.get("/recommenders").text
    assert _status(html, "jansen", "princeton-mfin") == "draft_ready"
    assert "Draft ready" in _cell(html, "jansen", "princeton-mfin")
    assert _status(html, "jansen", "booth-mif") == "requested"
    assert _status(html, "jansen", "mit-sloan-mfin") == "none"
    assert "—" in _cell(html, "whitfield", "columbia-msfe")
    assert 'aria-label="Change Princeton MFin status for Prof. Dr. Eva Jansen"' in html


def test_email_is_never_rendered_in_the_sheet(client: FormClient) -> None:
    for query in ("", "?filter=needs_action", "?sort=name", "?q=jansen"):
        html = client.get("/recommenders" + query).text
        assert JANSEN not in html and WHITFIELD not in html, query


def test_totals_strip_and_legend(client: FormClient) -> None:
    html = client.get("/recommenders").text
    strip = re.search(r'<section[^>]*id="totals".*?</section>', html, re.S).group(0)
    assert re.search(r"Princeton MFin.*?0 / 3", strip, re.S)
    assert re.search(r"Booth MiF.*?0 / 2.*?assumed", strip, re.S)
    assert "15 Dec 2026" in strip
    legend = re.search(r'<ul[^>]*id="legend".*?</ul>', html, re.S).group(0)
    for label in ("—", "To ask", "Draft ready", "Requested", "Confirmed", "Submitted", "Declined"):
        assert label in legend, label


def test_next_action_column(client: FormClient) -> None:
    html = client.get("/recommenders").text
    row = re.search(r'<tr[^>]*id="row-jansen".*?</tr>', html, re.S).group(0)
    assert "Send request by 3 Nov 2026" in row


def test_search_filter_and_sort(client: FormClient) -> None:
    html = client.get("/recommenders?q=deriv").text
    assert "Whitfield" in html and "row-jansen" not in html and "1 professor" in html
    html = client.get("/recommenders?filter=needs_action").text
    assert "row-jansen" in html and "row-whitfield" not in html
    html = client.get("/recommenders?filter=done").text
    assert "No professors match" in html
    html = client.get("/recommenders?sort=name").text
    assert html.index("row-jansen") < html.index("row-whitfield")


def test_unknown_filter_or_sort_falls_back(client: FormClient) -> None:
    assert client.get("/recommenders?filter=nope&sort=nope").status_code == 200


# ---- status cells --------------------------------------------------------------------------------

CELL = "/recommenders/jansen/letters/princeton-mfin"


def test_setting_requested_swaps_the_row_and_stamps_today(client: FormClient, data_dir: Path) -> None:
    res = client.post(CELL, data={"status": "requested"}, headers=HX)
    assert res.status_code == 200
    assert "<html" not in res.text and 'id="row-jansen"' in res.text
    assert _status(res.text, "jansen", "princeton-mfin") == "requested"
    assert "Waiting for reply" in res.text
    assert 'id="totals"' in res.text and "hx-swap-oob" in res.text  # the totals strip updates too
    letter = _load(data_dir, "jansen")["letters"]["princeton-mfin"]
    assert letter["status"] == "requested"
    assert str(letter["requested_on"]) == "2026-10-30"
    assert str(letter["last_contact"]) == "2026-10-30"


@pytest.mark.parametrize(
    ("status", "field"),
    [("confirmed", "confirmed_on"), ("submitted", "submitted_on")],
)
def test_matching_date_is_stamped(client: FormClient, data_dir: Path, status: str, field: str) -> None:
    client.post(CELL, data={"status": status}, headers=HX)
    letter = _load(data_dir, "jansen")["letters"]["princeton-mfin"]
    assert str(letter[field]) == "2026-10-30" and str(letter["last_contact"]) == "2026-10-30"


def test_earlier_dates_are_kept_when_status_moves_back(client: FormClient, data_dir: Path) -> None:
    client.post("/recommenders/jansen/letters/booth-mif", data={"status": "draft_ready"}, headers=HX)
    letter = _load(data_dir, "jansen")["letters"]["booth-mif"]
    assert letter["status"] == "draft_ready" and str(letter["requested_on"]) == "2026-11-02"


def test_no_js_post_redirects_back(client: FormClient) -> None:
    res = client.post(
        CELL,
        data={"status": "requested"},
        headers={"Referer": "http://127.0.0.1:8765/recommenders?sort=name"},
    )
    assert res.status_code == 303 and res.headers["location"] == "/recommenders?sort=name"


def test_ask_for_this_programme_creates_a_to_ask_letter(client: FormClient, data_dir: Path) -> None:
    html = client.get("/recommenders").text
    assert "Ask for this programme" in _cell(html, "whitfield", "mit-sloan-mfin")
    res = client.post(
        "/recommenders/whitfield/letters/mit-sloan-mfin", data={"status": "not_asked"}, headers=HX
    )
    assert res.status_code == 200 and "To ask" in _cell(res.text, "whitfield", "mit-sloan-mfin")
    assert _load(data_dir, "whitfield")["letters"]["mit-sloan-mfin"]["status"] == "not_asked"


def test_unchanged_status_writes_nothing(client: FormClient, data_dir: Path) -> None:
    before = tree_digest(data_dir)
    assert client.post(CELL, data={"status": "draft_ready"}, headers=HX).status_code == 200
    assert tree_digest(data_dir) == before


@pytest.mark.parametrize(
    ("url", "status", "code"),
    [
        (CELL, "thanked", 400),
        ("/recommenders/nobody/letters/princeton-mfin", "requested", 404),
        ("/recommenders/jansen/letters/no-such-programme", "requested", 404),
    ],
)
def test_bad_status_requests_write_nothing(
    client: FormClient, data_dir: Path, url: str, status: str, code: int
) -> None:
    before = tree_digest(data_dir)
    assert client.post(url, data={"status": status}, headers=HX).status_code == code
    assert tree_digest(data_dir) == before


# ---- add professor -------------------------------------------------------------------------------


def _new_form(client: FormClient) -> list[tuple[str, str]]:
    return form_fields(client.get("/recommenders/new").text, "professor-form")


def test_add_form_fields(client: FormClient) -> None:
    html = client.get("/recommenders/new").text
    assert "Used to address the email; not shown in the sheet" in html
    assert "Paste it straight from the course guide or syllabus" in html
    rows = int(re.search(r'<textarea[^>]*name="course_description"[^>]*rows="(\d+)"', html).group(1))
    assert rows >= 8
    for option in ("RSM", "UVA McIntire", "Other"):
        assert f">{option}</option>" in html
    assert "15 Dec 2026" in html  # programme checkboxes show their deadlines
    button = re.search(r"<button[^>]*>\s*Save and generate email", html).group(0)
    # Phase 2b (disclosed change): the 2a placeholder was disabled; it now submits the form with then=generate
    assert "disabled" not in button
    assert 'type="submit"' in button and 'name="then"' in button and 'value="generate"' in button
    assert "Formal" in html and "Slightly warmer" in html


def _submit_new(client: FormClient, **values: str | list[str]):
    fields = _new_form(client)
    for name, value in values.items():
        if isinstance(value, list):
            fields = [(k, v) for k, v in fields if k != name] + [(name, v) for v in value]
        else:
            fields = set_field(fields, name, value)
    return client.post("/recommenders", data=fields)


def test_add_professor_stores_the_description_verbatim(client: FormClient, data_dir: Path) -> None:
    text = long_description()
    assert len(text) == 3000
    res = _submit_new(
        client,
        name="Prof. Rosa Lindqvist",
        email="r.lindqvist@example.com",
        university="RSM",
        course="Behavioural Finance (FIN-330)",
        course_description=text.replace("\n", "\r\n"),  # browsers submit textarea line breaks as CRLF
        programmes=["princeton-mfin", "booth-mif"],
    )
    assert res.status_code == 303 and res.headers["location"] == "/recommenders?selected=lindqvist"
    stored = _load(data_dir, "lindqvist")
    assert stored["course_description"] == text
    assert stored["email"] == "r.lindqvist@example.com"
    assert {k: v["status"] for k, v in stored["letters"].items()} == {
        "princeton-mfin": "not_asked",
        "booth-mif": "not_asked",
    }
    raw = (data_dir / "professors" / "lindqvist.yaml").read_text(encoding="utf-8")
    assert "course_description: |" in raw  # a block scalar, readable in the file
    html = client.get("/recommenders").text
    assert "To ask" in _cell(html, "lindqvist", "princeton-mfin")
    assert _status(html, "lindqvist", "mit-sloan-mfin") == "none"


def test_add_professor_id_avoids_existing_files(client: FormClient, data_dir: Path) -> None:
    res = _submit_new(client, name="Prof. Bram Jansen", email="b.jansen@example.com", university="RSM")
    assert res.headers["location"] == "/recommenders?selected=jansen-2"
    assert _load(data_dir, "jansen-2")["name"] == "Prof. Bram Jansen"
    assert _load(data_dir, "jansen")["name"] == "Prof. Dr. Eva Jansen"


def test_other_university_uses_the_typed_name(client: FormClient, data_dir: Path) -> None:
    _submit_new(
        client,
        name="Prof. Tess Amari",
        email="t.amari@example.com",
        university="Other",
        university_other="Leiden",
    )
    assert _load(data_dir, "amari")["university"] == "Leiden"


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"name": "", "email": "x@example.com"}, "Name is required"),
        ({"name": "Prof. A Bee", "email": "not-an-email"}, "Email must be an email address"),
        (
            {"name": "Prof. A Bee", "email": "x@example.com", "course_description": "x" * 20_001},
            "at most 20,000",
        ),
        (
            {"name": "Prof. A Bee", "email": "x@example.com", "university": "Other"},
            "Other institution is required",
        ),
    ],
)
def test_invalid_add_shows_errors_and_writes_nothing(
    client: FormClient, data_dir: Path, values, message
) -> None:
    before = tree_digest(data_dir)
    res = _submit_new(client, university=values.pop("university", "RSM"), **values)
    assert res.status_code == 200 and message in res.text and "Nothing was saved" in res.text
    assert tree_digest(data_dir) == before


# ---- edit professor ------------------------------------------------------------------------------


def test_edit_form_is_prefilled_and_an_unchanged_save_writes_nothing(
    client: FormClient, data_dir: Path
) -> None:
    html = client.get("/recommenders/jansen/edit").text
    assert "Edit Prof. Dr. Eva Jansen" in html
    before = tree_digest(data_dir)
    res = client.post("/recommenders/jansen", data=form_fields(html, "professor-form"))
    assert res.status_code == 303
    assert tree_digest(data_dir) == before


def test_edit_changes_the_course_and_keeps_letters(client: FormClient, data_dir: Path) -> None:
    fields = form_fields(client.get("/recommenders/jansen/edit").text, "professor-form")
    client.post("/recommenders/jansen", data=set_field(fields, "course", "Asset Pricing (FIN-400)"))
    stored = _load(data_dir, "jansen")
    assert stored["course"] == "Asset Pricing (FIN-400)"
    assert str(stored["letters"]["booth-mif"]["requested_on"]) == "2026-11-02"
    assert (
        stored["course_description"]
        == "An invented course description used only in tests.\nIt spans two lines on purpose.\n"
    )


def test_edit_sets_a_letter_date(client: FormClient, data_dir: Path) -> None:
    fields = form_fields(client.get("/recommenders/jansen/edit").text, "professor-form")
    client.post("/recommenders/jansen", data=set_field(fields, "letter-booth-mif-thanked_on", "2026-11-05"))
    assert str(_load(data_dir, "jansen")["letters"]["booth-mif"]["thanked_on"]) == "2026-11-05"


def test_edit_cannot_untick_a_letter_with_history(client: FormClient, data_dir: Path) -> None:
    html = client.get("/recommenders/jansen/edit").text
    fields = [
        (k, v) for k, v in form_fields(html, "professor-form") if not (k == "programmes" and v == "booth-mif")
    ]
    client.post("/recommenders/jansen", data=fields)
    assert "booth-mif" in _load(data_dir, "jansen")["letters"]  # requested: kept


# ---- detail panel and Mark as requested ------------------------------------------------------------


def test_panel_shows_details_and_email(client: FormClient) -> None:
    res = client.get("/recommenders/jansen/panel", headers=HX)
    assert res.status_code == 200 and "<html" not in res.text
    assert JANSEN in res.text and 'href="/recommenders/jansen/edit"' in res.text
    assert "An invented course description" in res.text


def test_selected_row_shows_the_panel_without_js(client: FormClient) -> None:
    html = client.get("/recommenders?selected=jansen").text
    assert 'id="panel"' in html and JANSEN in html
    table = re.search(r'<table[^>]*id="sheet".*?</table>', html, re.S).group(0)
    assert JANSEN not in table
    assert re.search(r'<tr[^>]*id="row-jansen"[^>]*class="[^"]*selected', html)


def test_mark_as_requested_checklist_lists_askable_letters_ticked(client: FormClient) -> None:
    html = client.get("/recommenders/jansen/panel", headers=HX).text
    fields = form_fields(html, "mark-requested-jansen")
    assert ("programmes", "princeton-mfin") in fields  # draft_ready, ticked by default
    assert ("programmes", "booth-mif") not in fields  # already requested


def test_mark_as_requested_sets_only_ticked_letters(client: FormClient, data_dir: Path) -> None:
    client.post("/recommenders/whitfield/letters/mit-sloan-mfin", data={"status": "not_asked"}, headers=HX)
    client.post("/recommenders/whitfield/letters/booth-mif", data={"status": "draft_ready"}, headers=HX)
    res = client.post("/recommenders/whitfield/mark-requested", data=[("programmes", "booth-mif")])
    assert res.status_code == 303
    letters = _load(data_dir, "whitfield")["letters"]
    assert (
        letters["booth-mif"]["status"] == "requested"
        and str(letters["booth-mif"]["requested_on"]) == "2026-10-30"
    )
    assert letters["mit-sloan-mfin"]["status"] == "not_asked"


def test_mark_as_requested_ignores_letters_that_are_not_askable(client: FormClient, data_dir: Path) -> None:
    before = tree_digest(data_dir)
    client.post("/recommenders/jansen/mark-requested", data=[("programmes", "booth-mif")])
    assert tree_digest(data_dir) == before


# ---- CSV import ----------------------------------------------------------------------------------

CSV_HEADER = "name,email,university,course,course_description,grade,term,interaction\r\n"


def _preview(client: FormClient, text: str, bom: bool = True):
    raw = ("﻿" if bom else "") + text
    return client.post("/recommenders/import", files={"file": ("profs.csv", raw.encode("utf-8"), "text/csv")})


def test_csv_preview_lists_valid_and_error_rows_and_writes_nothing(
    client: FormClient, data_dir: Path
) -> None:
    before = tree_digest(data_dir)
    text = CSV_HEADER + (
        'Prof. Nina Ortiz,n.ortiz@example.com,RSM,Ethics (ET-100),"Line one\nLine two, with comma",,,\r\n'
        ",nobody@example.com,RSM,Course,,,,\r\n"
        "Prof. Bad Mail,not-an-email,RSM,Course,,,,\r\n"
        f"Prof. Copy Jansen,{JANSEN},RSM,Course,,,,\r\n"
    )
    res = _preview(client, text)
    assert res.status_code == 200
    assert "Prof. Nina Ortiz" in res.text
    assert "Row 3" in res.text and "Name is required" in res.text
    assert "Row 4" in res.text and "Email must be an email address" in res.text
    assert "Row 5" in res.text and "already exists" in res.text
    assert "1 professor will be imported" in res.text
    assert tree_digest(data_dir) == before


def test_csv_save_imports_only_valid_rows(client: FormClient, data_dir: Path) -> None:
    text = CSV_HEADER + (
        "Prof. Nina Ortiz,n.ortiz@example.com,RSM,Ethics (ET-100),"
        '"Line one\nLine two, with comma",8 / 10,Fall 2025,\r\n'
        "Prof. Bad Mail,not-an-email,RSM,Course,,,,\r\n"
    )
    preview = _preview(client, text).text
    res = client.post("/recommenders/import/save", data=form_fields(preview, "import-form"))
    assert res.status_code == 303
    stored = _load(data_dir, "ortiz")
    assert stored["course_description"] == "Line one\nLine two, with comma"
    assert stored["grade"] == "8 / 10" and stored["letters"] == {}
    assert not any("mail" in p.stem for p in (data_dir / "professors").glob("*.yaml"))


def test_csv_missing_required_column_is_refused(client: FormClient, data_dir: Path) -> None:
    before = tree_digest(data_dir)
    res = _preview(client, "name,university\r\nProf. X Y,RSM\r\n", bom=False)
    assert res.status_code == 200 and "missing the column" in res.text and "email" in res.text
    assert tree_digest(data_dir) == before


# ---- data-integrity review (Phase 2a) regressions --------------------------------------------------

HAND_LEE = (
    "# hand-written\nid: Lee\nname: Prof. Hand Lee\nemail: hand.lee@example.org\nuniversity: RSM\ncourse: X\n"
)


def test_add_never_overwrites_a_file_that_differs_only_in_case(client: FormClient, data_dir: Path) -> None:
    (data_dir / "professors" / "Lee.yaml").write_text(HAND_LEE, encoding="utf-8")
    res = _submit_new(client, name="Prof. Kim Lee", email="kim.lee@example.com", university="RSM")
    assert res.headers["location"] == "/recommenders?selected=lee-2"
    names = sorted(p.name for p in (data_dir / "professors").glob("*.yaml"))
    assert "Lee.yaml" in names and "lee-2.yaml" in names
    assert (data_dir / "professors" / "Lee.yaml").read_text(encoding="utf-8") == HAND_LEE


def test_csv_import_never_overwrites_a_file_that_differs_only_in_case(
    client: FormClient, data_dir: Path
) -> None:
    (data_dir / "professors" / "Lee.yaml").write_text(HAND_LEE, encoding="utf-8")
    preview = _preview(client, CSV_HEADER + "Prof. Kim Lee,kim.lee@example.com,RSM,C,,,,\r\n").text
    client.post("/recommenders/import/save", data=form_fields(preview, "import-form"))
    assert (data_dir / "professors" / "Lee.yaml").read_text(encoding="utf-8") == HAND_LEE
    assert _load(data_dir, "lee-2")["name"] == "Prof. Kim Lee"


def test_store_create_refuses_an_existing_file(config, data_dir: Path) -> None:
    from hub.models import Professor
    from hub.store import DataError, Store

    (data_dir / "professors" / "Lee.yaml").write_text(HAND_LEE, encoding="utf-8")
    store = Store.from_config(config)
    prof = Professor(
        id="lee", name="Prof. Kim Lee", email="kim.lee@example.com", university="RSM", course="C"
    )
    with pytest.raises(DataError, match="already exists"):
        store.create_professor(prof)
    assert (data_dir / "professors" / "Lee.yaml").read_text(encoding="utf-8") == HAND_LEE


def test_description_textarea_never_trims_in_the_browser(client: FormClient) -> None:
    html = client.get("/recommenders/new").text
    area = re.search(r'<textarea[^>]*name="course_description"[^>]*>', html).group(0)
    assert "maxlength" not in area  # the server checks the length and keeps the text (review H2)


def test_too_long_description_is_kept_in_the_form(client: FormClient, data_dir: Path) -> None:
    text = "y" * 20_001
    res = _submit_new(client, name="Prof. A Bee", email="a.bee@example.com", course_description=text)
    assert "must be at most 20,000 characters (it has 20,001)" in res.text
    assert ("course_description", text) in form_fields(res.text, "professor-form")


def test_a_20000_character_description_is_stored_verbatim(client: FormClient, data_dir: Path) -> None:
    text = long_description(20_000)
    _submit_new(client, name="Prof. Max Long", email="m.long@example.com", course_description=text)
    assert _load(data_dir, "long")["course_description"] == text


def test_unticking_a_letter_with_stamped_dates_keeps_it(client: FormClient, data_dir: Path) -> None:
    client.post("/recommenders/jansen/letters/booth-mif", data={"status": "draft_ready"}, headers=HX)
    html = client.get("/recommenders/jansen/edit").text
    assert re.search(r'<input[^>]*id="f-prog-booth-mif"[^>]*disabled', html)  # requested_on is set
    fields = [(k, v) for k, v in form_fields(html, "professor-form") if (k, v) != ("programmes", "booth-mif")]
    client.post("/recommenders/jansen", data=fields)
    letter = _load(data_dir, "jansen")["letters"]["booth-mif"]
    assert letter["status"] == "draft_ready" and str(letter["requested_on"]) == "2026-11-02"


def test_unticking_a_fresh_to_ask_letter_removes_it(client: FormClient, data_dir: Path) -> None:
    client.post("/recommenders/whitfield/letters/mit-sloan-mfin", data={"status": "not_asked"}, headers=HX)
    html = client.get("/recommenders/whitfield/edit").text
    fields = [
        (k, v) for k, v in form_fields(html, "professor-form") if (k, v) != ("programmes", "mit-sloan-mfin")
    ]
    client.post("/recommenders/whitfield", data=fields)
    assert _load(data_dir, "whitfield")["letters"] == {}


HAND_WRITTEN = (
    "id: hand\n"
    "name: 'Prof. Hand Written '\n"
    "email: hand@example.org\n"
    "university: 'RSM '\n"
    "course: 'Course (C-1) '\n"
    "course_description: |\n  Line one\n\n  Line three\n"
    "grade: ''\n"
    "interaction: |\n  Asked a question.\n"
)


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_unchanged_edit_of_a_hand_written_file_writes_nothing(
    client: FormClient, data_dir: Path, newline: str
) -> None:
    path = data_dir / "professors" / "hand.yaml"
    path.write_bytes(HAND_WRITTEN.replace("\n", newline).encode("utf-8"))
    before = path.read_bytes()
    fields = form_fields(client.get("/recommenders/hand/edit").text, "professor-form")
    assert client.post("/recommenders/hand", data=fields).status_code == 303
    assert path.read_bytes() == before


def test_csv_multiline_interaction_is_stored_with_lf(client: FormClient, data_dir: Path) -> None:
    text = CSV_HEADER + 'Prof. Ines Vale,i.vale@example.com,RSM,C,D,,,"Line one\r\nLine two"\r\n'
    preview = _preview(client, text).text
    client.post("/recommenders/import/save", data=form_fields(preview, "import-form"))
    assert _load(data_dir, "vale")["interaction"] == "Line one\nLine two"


def test_semicolon_csv_from_dutch_excel_is_read(client: FormClient) -> None:
    text = (
        CSV_HEADER.replace(",", ";")
        + "Prof. Nina Ortiz;n.ortiz@example.com;RSM;Ethics;Desc, with comma;;;\r\n"
    )
    res = _preview(client, text)
    assert "1 professor will be imported" in res.text and "Prof. Nina Ortiz" in res.text


def test_partly_failed_import_says_what_was_imported(
    client: FormClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hub.store import DataError, Store

    real = Store.create_professor
    calls = {"n": 0}

    def flaky(self, professor):  # the second write fails, as if OneDrive held the file
        calls["n"] += 1
        if calls["n"] == 2:
            raise DataError(f"professors/{professor.id}.yaml", None, "could not be written: it is locked")
        return real(self, professor)

    monkeypatch.setattr(Store, "create_professor", flaky)
    text = CSV_HEADER + "".join(
        f"Prof. A {n},{n}@example.com,RSM,C,,,,\r\n" for n in ("alpha", "beta", "gamma")
    )
    preview = _preview(client, text).text
    res = client.post("/recommenders/import/save", data=form_fields(preview, "import-form"))
    assert "Nothing was imported" not in res.text
    assert "1 of 3 professors were imported" in res.text and "professors/beta.yaml" in res.text
    assert (data_dir / "professors" / "alpha.yaml").exists()


def test_add_refuses_an_email_already_used(client: FormClient, data_dir: Path) -> None:
    before = tree_digest(data_dir)
    res = _submit_new(client, name="Prof. Eve Twin", email="E.Jansen@example.org", university="RSM")
    assert res.status_code == 200 and "already used by Prof. Dr. Eva Jansen" in res.text
    assert tree_digest(data_dir) == before


# ---- UI review (Phase 2a) regressions --------------------------------------------------------------


def test_totals_strip_marks_unverified_deadlines_with_their_reason(client: FormClient) -> None:
    html = client.get("/recommenders").text
    strip = re.search(r'<section[^>]*id="totals".*?</section>', html, re.S).group(0)
    assert strip.count("badge-unverified") == 4  # all four fixture programmes are unverified
    assert "Researched, not checked" in strip


def test_add_form_marks_unverified_deadlines(client: FormClient) -> None:
    assert client.get("/recommenders/new").text.count("badge-unverified") == 4


def test_status_change_keeps_selection_and_refreshes_the_panel(client: FormClient) -> None:
    data = {"status": "requested", "q": "", "filter": "all", "sort": "name", "selected": "jansen"}
    res = client.post(CELL, data=data, headers=HX)
    row = re.search(r'<tr[^>]*id="row-jansen"[^>]*>', res.text).group(0)
    assert 'aria-current="true"' in row and "selected" in row
    assert "sort=name" in res.text  # the row's links keep the sheet's sort
    panel = re.search(r'<section[^>]*id="panel".*?</section>', res.text, re.S).group(0)
    assert 'hx-swap-oob="true"' in panel and "Waiting for reply" in panel


def test_status_change_on_another_row_leaves_the_panel_alone(client: FormClient) -> None:
    data = {"status": "not_asked", "selected": "jansen"}
    res = client.post("/recommenders/whitfield/letters/mit-sloan-mfin", data=data, headers=HX)
    assert 'id="panel"' not in res.text


def test_no_js_failed_status_save_shows_the_sheet_with_the_message(
    client: FormClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hub.store import DataError, Store

    def locked(self, professor):
        raise DataError("professors/jansen.yaml", None, "could not be written: it is locked")

    monkeypatch.setattr(Store, "save_professor", locked)
    res = client.post(CELL, data={"status": "requested", "sort": "name"})
    assert res.status_code == 409
    assert "Nothing was saved." in res.text and "professors/jansen.yaml" in res.text
    assert 'id="sheet"' in res.text  # still the sheet, not a dead end
    assert _status(res.text, "jansen", "princeton-mfin") == "draft_ready"


# ---- data-integrity re-review (Phase 2a) regressions -----------------------------------------------


def test_professors_already_sharing_an_email_can_still_be_edited(client: FormClient, data_dir: Path) -> None:
    (data_dir / "professors" / "twin.yaml").write_text(
        "id: twin\nname: Prof. Twin\nemail: e.jansen@example.org\nuniversity: RSM\ncourse: X\n",
        encoding="utf-8",
    )
    fields = form_fields(client.get("/recommenders/twin/edit").text, "professor-form")
    assert client.post("/recommenders/twin", data=set_field(fields, "course", "Y")).status_code == 303
    assert _load(data_dir, "twin")["course"] == "Y"


def test_unticked_letter_with_a_typed_date_is_kept(client: FormClient, data_dir: Path) -> None:
    client.post("/recommenders/whitfield/letters/mit-sloan-mfin", data={"status": "not_asked"}, headers=HX)
    html = client.get("/recommenders/whitfield/edit").text
    fields = [
        (k, v) for k, v in form_fields(html, "professor-form") if (k, v) != ("programmes", "mit-sloan-mfin")
    ]
    client.post(
        "/recommenders/whitfield", data=set_field(fields, "letter-mit-sloan-mfin-requested_on", "2026-10-20")
    )
    assert str(_load(data_dir, "whitfield")["letters"]["mit-sloan-mfin"]["requested_on"]) == "2026-10-20"


def test_import_stopped_by_an_existing_file_reads_cleanly(
    client: FormClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hub.store import DataError, Store

    def exists(self, professor):
        raise DataError(f"professors/{professor.id}.yaml", None, "already exists; nothing was written")

    monkeypatch.setattr(Store, "create_professor", exists)
    preview = _preview(client, CSV_HEADER + "Prof. A Moss,moss@example.com,RSM,C,,,,\r\n").text
    res = client.post("/recommenders/import/save", data=form_fields(preview, "import-form"))
    assert "already exists; nothing was written. Preview" in res.text and "written.." not in res.text
    assert "Save again" not in res.text
