"""Update emails (Phase 3 pre-build, user request; DECISIONS #104; docs/03 §4.3).

From the Recommenders page actions, "Update emails" takes a CSV file or pasted text with two columns, name and
email (header optional; separated by ";", "," or a tab; UTF-8 with or without a byte-order mark). Checked,
fast and through the routes:
- each row is matched to an existing professor by the names rule (docs/02 §9: surname + initials, case- and
  diacritic-insensitive); a surname-only match (a name without initials) is offered, never ticked;
- the preview fills only empty emails (ticked); a different existing email is shown with both values and
  nothing pre-selected; unmatched, ambiguous, invalid and repeated rows are listed with their reason, never
  guessed; an email used by another professor is refused (DECISIONS #41 f);
- nothing is written until Save; Save writes the ticked and "Use the new email" rows through the store (the
  file's comments kept), refuses an undecided conflict, an empty choice, a forged choice or professors changed
  since the preview, with nothing written; a failure part way says how many were updated.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hub import email_update
from hub.config import load_config
from hub.professor_form import new_id
from hub.store import DataError, Store

from helpers import FormClient, form_fields, set_field, tree_digest

URL = "/recommenders/emails"
SAVE = "/recommenders/emails/save"
FORM = "emails-form"

EXTRA = {
    "odegaard": ("Dr. L. Ødegaard", None),
    "smit": ("Dr. J. Smit", None),
    "smit-2": ("Dr. K. Smit", None),
    "lindqvist": ("Dr. R. Lindqvist", None),
    "van-den-berg": ("Prof. A. van den Berg", "a.berg@example.org"),
}


def _write_professor(data_dir: Path, pid: str, name: str, email: str | None) -> None:
    lines = [f"# Test fixture: invented professor {pid}.", f"id: {pid}", f"name: {name}"]
    lines += [f"email: {email}"] if email else []
    lines += ["university: Example School of Economics", "letters: {}", ""]
    (data_dir / "professors" / f"{pid}.yaml").write_text("\n".join(lines), encoding="utf-8", newline="\n")


@pytest.fixture
def professors(data_dir: Path) -> Path:
    for pid, (name, email) in EXTRA.items():
        _write_professor(data_dir, pid, name, email)
    return data_dir / "professors"


def _email(config, pid: str) -> str | None:
    return next(p.email for p in Store.from_config(config).load_all().professors if p.id == pid)


TEXT = """\
name;email
L. Odegaard;l.odegaard@example.org
Prof. Eva Jansen;eva.jansen@example.org
Smit;smit@example.org
Q. Nobody;q.nobody@example.org
Dr. A. van den Berg;s.whitfield@example.org
Whitfield;S.Whitfield@example.org
Lindqvist;r.lindqvist@example.org
"""


def _preview(client: FormClient, text: str = TEXT) -> str:
    res = client.post(URL, data={"text": text})
    assert res.status_code == 200, res.text[-600:]
    return res.text


def _section(html: str, start: str, end: str | None = None) -> str:
    begin = html.index(start)
    return html[begin : html.index(end, begin) if end else None]


# ---- reading the input -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "name;email\nDr. J. Smit;j.smit@example.org\n",
        "Name,Email\r\nDr. J. Smit,j.smit@example.org\r\n",
        "﻿name\temail\nDr. J. Smit\tj.smit@example.org\n",  # pasted from Excel, with a BOM
        "Dr. J. Smit,j.smit@example.org\n",  # no header
        '"Smit, J.";j.smit@example.org\n\n',  # quoted, blank line
    ],
)
def test_rows_are_read_with_or_without_header_and_any_separator(text: str) -> None:
    rows, header = email_update.read_rows(text)
    assert [(r.name, r.email, r.error) for r in rows][0][1:] == ("j.smit@example.org", None)
    assert len(rows) == 1
    assert header == text.lstrip("﻿").casefold().startswith("name")


def test_a_first_row_that_is_not_a_header_is_a_row_with_its_error() -> None:
    rows, header = email_update.read_rows("Dr. J. Smit;not-an-email\nDr. K. Smit;k@example.org\n")
    assert not header and rows[0].number == 1 and "email address" in rows[0].error
    assert rows[1].number == 2 and rows[1].error is None


def test_a_row_with_the_wrong_number_of_columns_is_listed() -> None:
    rows, _ = email_update.read_rows("name,email\nSmit,J.,j@example.org\nOnly a name\n")
    assert [r.number for r in rows] == [2, 3]
    assert all("two columns" in r.error for r in rows)


# ---- the preview -----------------------------------------------------------------------------------


def test_the_recommenders_page_links_to_it(client: FormClient) -> None:
    html = client.get("/recommenders").text
    assert f'href="{URL}"' in html and "Update emails" in html
    form = client.get(URL)
    assert form.status_code == 200 and 'name="file"' in form.text and 'name="text"' in form.text


def test_preview_classifies_every_row_and_writes_nothing(
    client: FormClient, data_dir: Path, professors: Path
) -> None:
    before = tree_digest(data_dir)
    html = _preview(client)
    fields = dict(form_fields(html, FORM))
    # row 2: Ødegaard has no email, exact match (diacritics ignored) → ticked
    assert fields.get("row-2") == "fill"
    # row 3: Jansen has another email → both shown, nothing pre-selected
    assert "row-3" not in fields
    conflict = _section(html, 'id="row-3"', "</tr>")
    assert "e.jansen@example.org" in conflict and "eva.jansen@example.org" in conflict
    assert 'name="row-3" value="keep"' in conflict and 'name="row-3" value="use"' in conflict
    # row 8: Lindqvist without initials → offered, not ticked, says so
    assert "row-8" not in fields and 'name="row-8" value="fill"' in html
    assert "same surname only" in _section(html, 'id="row-8"', "</tr>")
    unused = _section(html, 'id="not-used"')
    assert "Row 4" in unused and "Dr. J. Smit" in unused and "Dr. K. Smit" in unused  # ambiguous
    assert "Row 5" in unused and "No professor matches" in unused
    assert "Row 6" in unused and "already used by Prof. Sam Whitfield" in unused
    assert "Row 7" in unused and "already has this email" in unused  # case-insensitive
    assert tree_digest(data_dir) == before


def test_rows_for_one_professor_or_one_email_twice_are_refused(client: FormClient, professors: Path) -> None:
    text = (
        "Dr. J. Smit;j.smit@example.org\n"
        "J. Smit;js@example.org\n"  # the same professor again, another email
        "Dr. R. Lindqvist;shared@example.org\n"
        "L. Ødegaard;shared@example.org\n"  # the same email for two professors
        "K. Smit;k.smit@example.org\n"
        "Dr. K. Smit;k.smit@example.org\n"  # the same row twice: harmless, said once
    )
    html = _preview(client, text)
    fields = dict(form_fields(html, FORM))
    assert fields == {"row-5": "fill", "text": fields["text"], "digest": fields["digest"]}
    unused = _section(html, 'id="not-used"')
    assert "Rows 1 and 2 give different emails for Dr. J. Smit" in unused
    assert "Rows 3 and 4 give the same email" in unused
    assert "Row 6" in unused and "same as row 5" in unused


@pytest.mark.parametrize(
    ("data", "files", "message"),
    [
        ({}, None, "Choose a CSV file or paste the names and emails"),
        ({"text": "Smit;s@example.org"}, {"file": ("e.csv", b"Smit,s@example.org\n")}, "not both"),
        ({}, {"file": ("e.csv", "Ødegaard;l@example.org\n".encode("latin-1"))}, "not UTF-8"),
        ({"text": "name;email\n"}, None, "no rows"),
    ],
)
def test_input_refusals(
    client: FormClient, data_dir: Path, data: dict, files: dict | None, message: str
) -> None:
    before = tree_digest(data_dir)
    res = client.post(URL, data=data, files=files)
    assert res.status_code == 200 and message in res.text and 'id="emails-form"' not in res.text
    assert tree_digest(data_dir) == before


def test_an_uploaded_file_with_a_bom_is_read(client: FormClient, config, professors: Path) -> None:
    raw = "﻿name,email\r\nDr. L. Ødegaard,l.odegaard@example.org\r\n".encode()
    res = client.post(URL, files={"file": ("emails.csv", raw, "text/csv")})
    assert dict(form_fields(res.text, FORM)).get("row-2") == "fill"


# ---- saving ----------------------------------------------------------------------------------------


def test_save_writes_ticked_and_chosen_rows_through_the_store(
    client: FormClient, config, data_dir: Path, professors: Path
) -> None:
    html = _preview(client)
    fields = set_field(form_fields(html, FORM), "row-3", "use")
    res = client.post(SAVE, data=fields)
    assert res.status_code == 303 and res.headers["location"] == "/recommenders?emails=2"
    assert _email(config, "odegaard") == "l.odegaard@example.org"
    assert _email(config, "jansen") == "eva.jansen@example.org"
    assert _email(config, "lindqvist") is None  # offered, not ticked
    assert _email(config, "van-den-berg") == "a.berg@example.org"  # refused row
    assert (
        (data_dir / "professors" / "odegaard.yaml").read_text(encoding="utf-8").startswith("# Test fixture")
    )
    assert "Updated the email of 2 professors." in client.get(res.headers["location"]).text


def test_keep_leaves_the_existing_email(client: FormClient, config, professors: Path) -> None:
    fields = set_field(form_fields(_preview(client), FORM), "row-3", "keep")
    fields = set_field(fields, "row-8", "fill")  # the owner confirms the surname-only match
    assert client.post(SAVE, data=fields).headers["location"] == "/recommenders?emails=2"
    assert _email(config, "jansen") == "e.jansen@example.org"
    assert _email(config, "lindqvist") == "r.lindqvist@example.org"


def test_an_undecided_conflict_is_refused_and_the_choices_are_kept(
    client: FormClient, data_dir: Path, professors: Path
) -> None:
    before = tree_digest(data_dir)
    fields = set_field(form_fields(_preview(client), FORM), "row-8", "fill")
    res = client.post(SAVE, data=fields)
    assert res.status_code == 409 and "Nothing was saved" in res.text
    assert "Pick Keep or Use for row 3 (Prof. Eva Jansen)" in res.text
    kept = dict(form_fields(res.text, FORM))
    assert kept.get("row-8") == "fill" and kept.get("row-2") == "fill"
    assert tree_digest(data_dir) == before


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            lambda f: [(k, v) for k, v in f if not k.startswith("row-")] + [("row-3", "keep")],
            "Nothing to save",
        ),
        (lambda f: set_field(set_field(f, "row-3", "keep"), "row-4", "fill"), "not valid"),
        (lambda f: set_field(set_field(f, "row-3", "keep"), "row-2", "use"), "not valid"),
        (lambda f: set_field(set_field(f, "row-3", "keep"), "digest", ""), "changed since"),
    ],
)
def test_refused_saves_write_nothing(
    client: FormClient, data_dir: Path, professors: Path, change, message: str
) -> None:
    before = tree_digest(data_dir)
    res = client.post(SAVE, data=change(form_fields(_preview(client), FORM)))
    assert res.status_code == 409 and message in res.text and "Nothing was saved" in res.text
    assert tree_digest(data_dir) == before


def test_a_professor_changed_since_the_preview_is_refused(
    client: FormClient, config, data_dir: Path, professors: Path
) -> None:
    fields = set_field(form_fields(_preview(client), FORM), "row-3", "use")
    _write_professor(data_dir, "odegaard", "Dr. L. Ødegaard", "typed.meanwhile@example.org")
    before = tree_digest(data_dir)
    res = client.post(SAVE, data=fields)
    assert res.status_code == 409 and "changed since" in res.text
    assert tree_digest(data_dir) == before
    assert _email(config, "jansen") == "e.jansen@example.org"


def test_a_failure_part_way_reports_the_counts(
    client: FormClient, config, professors: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = Store.save_professor
    calls: list[str] = []

    def failing_second(self, professor):  # noqa: ANN001, ANN202
        calls.append(professor.id)
        if len(calls) == 2:
            raise DataError(f"professors/{professor.id}.yaml", None, "could not be written: disk full")
        return real(self, professor)

    monkeypatch.setattr(Store, "save_professor", failing_second)
    fields = set_field(form_fields(_preview(client), FORM), "row-3", "use")
    res = client.post(SAVE, data=fields)
    assert res.status_code == 409
    assert "1 of 2 professors were updated, then it stopped" in res.text


# ---- professor ids never shadow a route ---------------------------------------------------------------


@pytest.mark.parametrize("word", ["Emails", "Import", "New"])
def test_a_new_professor_id_never_takes_a_route_word(word: str) -> None:
    assert new_id(f"Prof. A. {word}", set()) == f"{word.lower()}-2"


def test_the_config_names_rule_is_used(professors: Path) -> None:
    cfg = load_config()
    store = Store.from_config(cfg)
    found = email_update.propose("Dr. Van Den Berg;x@example.org\n", store.load_all().professors, cfg.names)
    assert found.proposals[0].professor is not None and found.proposals[0].professor.id == "van-den-berg"


def test_a_different_first_name_is_offered_never_ticked(client: FormClient, data_dir: Path) -> None:
    """data-integrity Phase 3 r1 F2: "Lise Ødegaard" is not "Lars Ødegaard" just because both start with L."""
    _write_professor(data_dir, "odegaard", "Prof. Lars Ødegaard", None)
    html = _preview(client, "Lise Ødegaard;lise@example.org\n")
    assert "row-1" not in dict(form_fields(html, FORM))
    assert 'name="row-1" value="fill"' in html and "same surname only" in _section(
        html, 'id="row-1"', "</tr>"
    )


# ---- data-integrity Phase 3 r1 F3: addresses that look right but aren't (DECISIONS #105) -------------------

LOOKALIKES = [
    "x​@example.org",  # zero-width space (faculty pages hide addresses from scrapers)
    "j.de­vries@example.org",  # soft hyphen
    "mailto:x@example.org",  # "Copy link" on a mailto link
    '"a,b"@example.org',
    "<x@example.org>",
    "x@exam‍ple.org",  # zero-width joiner in the domain
    "x @example.org",  # line separator
]


@pytest.mark.parametrize("email", LOOKALIKES)
def test_the_shared_email_check_refuses_lookalike_addresses(email: str) -> None:
    from pydantic import ValidationError

    from hub.models.professor import Professor

    with pytest.raises(ValidationError):
        Professor(id="x-1", name="Dr. X", university="Example", email=email)
    rows, _ = email_update.read_rows(f"Dr. L. Ødegaard;{email}\n")
    assert rows[0].error is not None  # listed with its reason, never offered


def test_a_lookalike_never_slips_past_the_one_email_rule(client: FormClient, data_dir: Path) -> None:
    _write_professor(data_dir, "odegaard", "Dr. L. Ødegaard", None)  # Jansen has e.jansen@example.org
    html = _preview(client, "Dr. L. Ødegaard;e.jansen​@example.org\n")
    assert "row-1" not in dict(form_fields(html, FORM)) and 'name="row-1"' not in html


@pytest.mark.parametrize("email", ["e.jansen@example.org", "o'brien@example.org", "jörg.müller@example.org"])
def test_ordinary_addresses_still_pass(email: str) -> None:
    from hub.models.professor import Professor

    assert Professor(id="x-1", name="Dr. X", university="Example", email=email).email == email


def test_a_change_between_the_digest_and_loading_is_caught_on_save(
    client: FormClient, config, data_dir: Path, professors: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """data-integrity Phase 3 r1 F6 (DECISIONS #105): Save checks the digest before and after loading."""
    fields = set_field(form_fields(_preview(client), FORM), "row-3", "use")
    real = Store.load_all
    state = {"done": False}

    def load_all(self):  # noqa: ANN001, ANN202
        if not state["done"]:
            state["done"] = True
            other = data_dir / "professors" / "smit.yaml"  # not in the list: only the digest can catch it
            other.write_text(other.read_text(encoding="utf-8") + "# changed meanwhile\n", encoding="utf-8")
        return real(self)

    monkeypatch.setattr(Store, "load_all", load_all)
    res = client.post(SAVE, data=fields)
    assert res.status_code == 409 and "changed since" in res.text
    assert _email(config, "jansen") == "e.jansen@example.org"
