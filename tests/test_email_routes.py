"""US-10 (docs/03 §2, §4.3): Generate email on the detail panel, without a browser.

A generated draft is appended to ``professors/<id>.yaml → emails`` with its warnings; ticked To ask letters
become Draft ready (other statuses are never changed); Mark as requested stamps the email's
``marked_sent_on``. The fake Claude Code replays tests/fakes/recordings/rec-email.json.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest
from markupsafe import escape
from ruamel.yaml import YAML

from hub.models.professor import GeneratedEmail, Letter, LetterStatus, Professor
from hub.store import Store

from helpers import RECORDINGS, FormClient, form_fields, set_field, tree_digest

HX = {"HX-Request": "true"}
RECORDED = json.loads((RECORDINGS / "rec-email.json").read_text(encoding="utf-8"))["structured_output"]
SUBJECT_HTML = str(escape(RECORDED["subject"]))  # as the page shows it ("master&#39;s")


def _load(data_dir: Path, pid: str) -> dict:
    return YAML(typ="safe").load((data_dir / "professors" / f"{pid}.yaml").read_text(encoding="utf-8"))


@pytest.fixture
def calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "fake-calls.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(path))
    return path


def _payloads(calls: Path) -> list[dict]:
    if not calls.exists():
        return []
    return [json.loads(json.loads(line)["stdin"]) for line in calls.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def jansen_with_mit(config) -> None:
    """Jansen: Princeton Draft ready, Booth Requested (fixture) plus MIT To ask."""
    store = Store.from_config(config)
    prof = next(p for p in store.load_professors()[0] if p.id == "jansen")
    prof.letters["mit-sloan-mfin"] = Letter(status=LetterStatus.NOT_ASKED)
    store.save_professor(prof)


def test_generate_saves_the_draft_and_moves_to_ask_letters_to_draft_ready(
    client: FormClient, data_dir: Path, calls: Path, jansen_with_mit: None
) -> None:
    res = client.post(
        "/recommenders/jansen/email",
        data=[
            ("programmes", "princeton-mfin"),
            ("programmes", "mit-sloan-mfin"),
            ("programmes", "booth-mif"),
        ],
        headers=HX,
    )
    assert res.status_code == 200
    (payload,) = _payloads(calls)
    assert [p["short_name"] for p in payload["programmes"]] == [
        "Princeton MFin",
        "MIT Sloan MFin",
    ]  # Booth asked
    stored = _load(data_dir, "jansen")
    email = stored["emails"][-1]
    assert email["programmes"] == ["princeton-mfin", "mit-sloan-mfin"]
    assert email["subject"] == RECORDED["subject"] and email["body"] == RECORDED["body"]
    assert email["warnings"][0] == "Princeton MFin's deadline is unverified — check it before sending"
    assert isinstance(email["generated_at"], datetime) and email["generated_at"].utcoffset() is not None
    assert email["marked_sent_on"] is None
    letters = stored["letters"]
    assert letters["princeton-mfin"]["status"] == "draft_ready"
    assert letters["mit-sloan-mfin"]["status"] == "draft_ready"
    assert letters["mit-sloan-mfin"].get("requested_on") is None  # nothing is stamped by a draft
    assert letters["booth-mif"]["status"] == "requested" and letters["booth-mif"]["requested_on"] == date(
        2026, 11, 2
    )


def test_result_shows_subject_body_warnings_and_actions(
    client: FormClient, data_dir: Path, jansen_with_mit: None
) -> None:
    html = client.post("/recommenders/jansen/email", data=[("programmes", "princeton-mfin")], headers=HX).text
    assert SUBJECT_HTML in html
    first_line = RECORDED["body"].splitlines()[0]
    assert str(escape(first_line)) in html
    unverified = html.index("Princeton MFin&#39;s deadline is unverified — check it before sending")
    for later in RECORDED["warnings"]:
        assert html.index(later.replace("'", "&#39;")) > unverified  # the unverified warning comes first
    for label in ("Copy subject", "Copy body", "Regenerate", "Refine in Claude Code", "Mark as requested"):
        assert label in html, label
    assert "claude &#34;/rec-email jansen&#34;" in html or 'claude "/rec-email jansen"' in html
    fields = form_fields(html, "mark-requested-jansen")
    assert ("programmes", "princeton-mfin") in fields  # pre-filled from the email's programmes
    assert ("programmes", "mit-sloan-mfin") not in fields


def test_nothing_ticked_writes_nothing_and_calls_nothing(
    client: FormClient, data_dir: Path, calls: Path
) -> None:
    before = tree_digest(data_dir)
    res = client.post("/recommenders/jansen/email", data=[], headers=HX)
    assert "Tick at least one programme" in res.text
    assert tree_digest(data_dir) == before
    assert _payloads(calls) == []


def test_programmes_not_to_ask_are_ignored(client: FormClient, data_dir: Path, calls: Path) -> None:
    """Booth is Requested and Columbia has no letter: neither can be generated for, so nothing is ticked."""
    before = tree_digest(data_dir)
    res = client.post(
        "/recommenders/jansen/email",
        data=[("programmes", "booth-mif"), ("programmes", "columbia-msfe")],
        headers=HX,
    )
    assert "Tick at least one programme" in res.text
    assert tree_digest(data_dir) == before and _payloads(calls) == []


def test_no_js_generate_redirects_to_the_panel(client: FormClient, data_dir: Path) -> None:
    res = client.post("/recommenders/jansen/email", data=[("programmes", "princeton-mfin")])
    assert res.status_code == 303
    assert (
        res.headers["location"].startswith("/recommenders?") and "selected=jansen" in res.headers["location"]
    )
    page = client.get(res.headers["location"]).text
    assert SUBJECT_HTML in page


def test_regenerate_appends_a_new_draft(client: FormClient, data_dir: Path) -> None:
    for _ in range(2):
        client.post("/recommenders/jansen/email", data=[("programmes", "princeton-mfin")], headers=HX)
    assert len(_load(data_dir, "jansen")["emails"]) == 2


def test_panel_shows_the_latest_stored_email(client: FormClient) -> None:
    html = client.get("/recommenders/whitfield/panel").text
    assert "Request for a letter of recommendation" in html
    assert "Invented body." in html
    assert "Generating the request email arrives in Phase 2b" not in html


def test_mark_as_requested_stamps_the_email(client: FormClient, data_dir: Path) -> None:
    client.post("/recommenders/jansen/email", data=[("programmes", "princeton-mfin")], headers=HX)
    html = client.get("/recommenders/jansen/panel").text
    fields = form_fields(html, "mark-requested-jansen")
    res = client.post("/recommenders/jansen/mark-requested", data=fields)
    assert res.status_code == 303
    stored = _load(data_dir, "jansen")
    assert stored["letters"]["princeton-mfin"]["status"] == "requested"
    assert stored["letters"]["princeton-mfin"]["requested_on"] == date(2026, 10, 30)
    assert stored["emails"][-1]["marked_sent_on"] == date(2026, 10, 30)


def _mark_requested(client: FormClient, programmes: list[str]) -> None:
    """Submit the panel's Mark as requested form with exactly ``programmes`` ticked."""
    fields = form_fields(client.get("/recommenders/jansen/panel").text, "mark-requested-jansen")
    fields = [(k, v) for k, v in fields if k != "programmes"] + [("programmes", p) for p in programmes]
    assert client.post("/recommenders/jansen/mark-requested", data=fields).status_code == 303


def test_mark_as_requested_stamps_the_draft_when_any_of_its_programmes_is_ticked(
    client: FormClient, data_dir: Path, jansen_with_mit: None
) -> None:
    """User decision at 2b acceptance (DECISIONS #56): one overlapping programme is enough, not all."""
    client.post(
        "/recommenders/jansen/email",
        data=[("programmes", "princeton-mfin"), ("programmes", "mit-sloan-mfin")],
        headers=HX,
    )
    _mark_requested(client, ["princeton-mfin"])
    stored = _load(data_dir, "jansen")
    assert stored["emails"][-1]["marked_sent_on"] == date(2026, 10, 30)
    assert stored["letters"]["mit-sloan-mfin"]["status"] == "draft_ready"  # only the ticked letter moves


def test_mark_as_requested_leaves_a_draft_for_other_programmes_unstamped(
    client: FormClient, data_dir: Path, jansen_with_mit: None
) -> None:
    client.post("/recommenders/jansen/email", data=[("programmes", "princeton-mfin")], headers=HX)
    _mark_requested(client, ["mit-sloan-mfin"])
    stored = _load(data_dir, "jansen")
    assert stored["letters"]["mit-sloan-mfin"]["status"] == "requested"
    assert stored["emails"][-1]["marked_sent_on"] is None  # the draft did not ask for MIT


# ---- models and the Title field (DECISIONS, 2b: title moves forward from 2c) ----------------------


def test_models_default_title_and_warnings() -> None:
    prof = Professor(id="xx", name="Prof. X", email="x@example.org", university="RSM", course="C")
    assert prof.title == "Professor"
    email = GeneratedEmail(
        generated_at=datetime.fromisoformat("2026-10-30T12:00:00-04:00"), programmes=[], subject="s", body="b"
    )
    assert email.warnings == []


def test_files_without_title_or_warnings_load_and_an_unchanged_save_writes_nothing(
    config, data_dir: Path
) -> None:
    store = Store.from_config(config)
    before = tree_digest(data_dir)
    for prof in store.load_professors()[0]:
        assert store.save_professor(prof) is False
    assert tree_digest(data_dir) == before


def test_title_field_on_add_and_edit(client: FormClient, data_dir: Path) -> None:
    html = client.get("/recommenders/new").text
    fields = form_fields(html, "professor-form")
    assert ("title", "Professor") in fields
    for name, value in {
        "name": "Dr. Ada Example",
        "title": "Dr.",
        "email": "a.example@example.org",
        "course": "Options (COMM-1)",
    }.items():
        fields = set_field(fields, name, value)
    assert client.post("/recommenders", data=fields).status_code == 303
    assert _load(data_dir, "example")["title"] == "Dr."
    edit = form_fields(client.get("/recommenders/example/edit").text, "professor-form")
    assert ("title", "Dr.") in edit


def test_blank_title_means_professor(client: FormClient, data_dir: Path) -> None:
    fields = form_fields(client.get("/recommenders/new").text, "professor-form")
    for name, value in {
        "name": "Prof. Bo Example",
        "title": "  ",
        "email": "b@example.org",
        "course": "C",
    }.items():
        fields = set_field(fields, name, value)
    assert client.post("/recommenders", data=fields).status_code == 303
    assert _load(data_dir, "example").get("title", "Professor") == "Professor"


def test_save_and_generate_creates_the_professor_and_the_draft(client: FormClient, data_dir: Path) -> None:
    fields = form_fields(client.get("/recommenders/new").text, "professor-form")
    values = {"name": "Prof. Rosa Lindqvist", "email": "r.l@example.org", "course": "C (X-1)"}
    for name, value in values.items():
        fields = set_field(fields, name, value)
    res = client.post("/recommenders", data=[*fields, ("programmes", "princeton-mfin"), ("then", "generate")])
    assert res.status_code == 303 and "selected=lindqvist" in res.headers["location"]
    stored = _load(data_dir, "lindqvist")
    assert stored["letters"]["princeton-mfin"]["status"] == "draft_ready"
    assert len(stored["emails"]) == 1 and stored["emails"][0]["programmes"] == ["princeton-mfin"]
    assert SUBJECT_HTML in client.get(res.headers["location"]).text


def test_edits_made_during_the_call_are_kept(
    client: FormClient, data_dir: Path, config, jansen_with_mit: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The professor is reloaded after the slow call: a note and a status set meanwhile survive (review)."""
    import hub.routes.recommenders as routes

    real = routes.run_skill

    def slow_call(*args, **kwargs):
        store = Store.from_config(config)
        prof = next(p for p in store.load_professors()[0] if p.id == "jansen")
        prof.notes = "Edited during the call"
        prof.letters["mit-sloan-mfin"] = Letter(
            status=LetterStatus.REQUESTED, requested_on=date(2026, 10, 30)
        )
        store.save_professor(prof)
        return real(*args, **kwargs)

    monkeypatch.setattr(routes, "run_skill", slow_call)
    data = [("programmes", "princeton-mfin"), ("programmes", "mit-sloan-mfin")]
    client.post("/recommenders/jansen/email", data=data, headers=HX)
    stored = _load(data_dir, "jansen")
    assert stored["notes"] == "Edited during the call"
    assert stored["letters"]["mit-sloan-mfin"]["status"] == "requested"  # not turned back into Draft ready
    assert stored["letters"]["princeton-mfin"]["status"] == "draft_ready"
    assert len(stored["emails"]) == 1


def test_a_model_warning_cannot_pose_as_an_unverified_alert(
    client: FormClient, data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the app's own deadline check produces the urgent unverified alert (review minor point)."""
    raw = json.loads((RECORDINGS / "rec-email.json").read_text(encoding="utf-8"))
    raw["structured_output"]["warnings"] = [
        "MIT Sloan MFin's deadline is unverified — check it before sending"
    ]
    fake = tmp_path / "spoof.json"
    fake.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setenv("FAKE_CLAUDE_RECORDING", str(fake))
    client.post("/recommenders/jansen/email", data=[("programmes", "princeton-mfin")], headers=HX)
    warnings = _load(data_dir, "jansen")["emails"][-1]["warnings"]
    assert not any(w.startswith("MIT Sloan MFin") for w in warnings)


def test_generate_checklist_marks_unverified_deadlines(client: FormClient, jansen_with_mit: None) -> None:
    """ui review round 4 (C-1-01, rule 11): each deadline offered for the email shows its Unverified badge."""
    html = client.get("/recommenders/jansen/panel").text
    reasons = {"princeton-mfin": "Last cycle's date", "mit-sloan-mfin": "Researched, not checked"}
    for pid, reason in reasons.items():
        start = html.index(f'id="gen-jansen-{pid}-dl"')
        line = html[start : html.index("</div>", start)]
        assert "badge-unverified" in line, pid
        assert str(escape(reason)) in line, pid


def test_mark_as_requested_comes_after_the_email(client: FormClient) -> None:
    """ui review round 4: guards #53(3); the owner reads the draft before marking it sent."""
    html = client.get("/recommenders/whitfield/panel").text
    assert html.index('id="email-result"') < html.index('class="mark-requested"')
