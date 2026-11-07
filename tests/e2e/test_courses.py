"""US-19, US-20 and US-21 (docs/03 §2, §4.4, §4.8), end to end with the fake Claude Code.

- US-19: import a transcript (.docx, text PDF or pasted text); the preview shows every course with all
  fields, flags grades not found in the document, writes nothing until rows are accepted; a scanned PDF is
  refused with a clear message.
- US-20: "Create professors from this document" lists every lecturer once with their courses and grades;
  the user ticks some, links one to an existing professor and creates the rest in one batch; the new
  professors show "Email missing".
- US-21: on Add professor, typing a lecturer's name suggests their courses; choosing one fills course,
  description, grade and term, which stay editable; nothing is saved until Save.
axe runs on every page and state (C-1-05 keeps passing).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import Page, expect
from ruamel.yaml import YAML

from hub.models.professor import Professor
from hub.store import Store

from helpers import write_recording
from paths import REPO_ROOT

pytestmark = pytest.mark.e2e

EVAL = REPO_ROOT / "tests" / "evals" / "courses"
EXPECTED = json.loads((EVAL / "expected.json").read_text(encoding="utf-8"))
AXE = Axe()


def _serious(page: Page) -> list[str]:
    results = AXE.run(page)
    return [
        f"{v['id']} ({v['impact']}): {[', '.join(n['target']) for n in v['nodes']][:5]}"
        for v in results.response["violations"]
        if v.get("impact") in {"serious", "critical"}
    ]


def _output(rows: list[dict]) -> dict:
    keys = ("name", "code", "institution", "grade", "credits", "term", "lecturers")
    return {"courses": [{**{k: r[k] for k in keys}, "description": ""} for r in rows], "warnings": []}


@pytest.fixture
def rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def use(output: dict) -> None:
        monkeypatch.setenv("FAKE_CLAUDE_RECORDING", str(write_recording(tmp_path, output, "courses.json")))

    use(_output(EXPECTED["courses"]))
    return use


def _courses(data_dir: Path) -> list[dict]:
    path = data_dir / "courses.yaml"
    return YAML(typ="safe").load(path.read_text(encoding="utf-8"))["courses"] if path.exists() else []


def _import(page: Page, live_server: str, file: str) -> None:
    page.goto(live_server + "/recommenders", wait_until="domcontentloaded")
    page.get_by_role("link", name="Import courses").click()
    page.wait_for_url("**/courses/import")
    page.get_by_label("Transcript file").set_input_files(str(EVAL / file))
    page.get_by_role("button", name="Read courses").click()
    page.get_by_role("button", name="Send to Claude").click()  # after the review step (#70)
    page.locator("#courses-preview").wait_for(timeout=20000)


# ---- US-19 -----------------------------------------------------------------------------------------------


def test_us19_import_preview_flags_and_accept(page: Page, live_server: str, data_dir: Path, rows) -> None:
    bad = [dict(r) for r in EXPECTED["courses"]]
    bad[0]["grade"] = "10,0"  # invented
    rows(_output(bad))
    _import(page, live_server, "transcript.docx")
    preview = page.locator("#courses-preview")
    expect(preview.locator("[data-row]")).to_have_count(28)
    for i, r in enumerate(EXPECTED["courses"]):  # every row and field, in editable inputs
        expect(page.locator(f"#row-{i}-code")).to_have_value(r["code"])
        expect(page.locator(f"#row-{i}-name")).to_have_value(r["name"])
        expect(page.locator(f"#row-{i}-credits")).to_have_value(f"{r['credits']:g}")
    expect(page.locator("[data-row='0']")).to_contain_text("Grade not found in the document")
    expect(page.locator("[data-row='0']").get_by_role("button", name="Accept")).to_be_disabled()
    for identifier in EXPECTED["identifiers"]:
        assert identifier not in page.content()
    assert not _serious(page), _serious(page)
    assert _courses(data_dir) == [] and not (data_dir / "sources").exists()  # nothing before accepting
    page.locator("[data-row='1']").get_by_role("button", name="Accept").click()
    expect(page.locator("[data-row='1']")).to_contain_text("Saved")
    assert [c["code"] for c in _courses(data_dir)] == ["FEB11002"]
    page.get_by_role("button", name="Accept all").click()
    expect(page.locator("[data-row='2']")).to_contain_text("Saved")
    stored = _courses(data_dir)
    assert len(stored) == 27 and "FEB11001" not in {c["code"] for c in stored}  # the flagged row stays out
    page.goto(live_server + "/courses", wait_until="domcontentloaded")
    expect(page.locator("main table tbody tr")).to_have_count(27)
    assert not _serious(page), _serious(page)


def test_us19_a_scanned_pdf_is_refused(page: Page, live_server: str, data_dir: Path) -> None:
    page.goto(live_server + "/courses/import", wait_until="domcontentloaded")
    page.get_by_label("Transcript file").set_input_files(str(EVAL / "scanned.pdf"))
    page.get_by_role("button", name="Read courses").click()
    expect(page.get_by_role("alert")).to_contain_text("This PDF has no text layer; OCR is not supported.")
    assert not _serious(page), _serious(page)
    assert not (data_dir / "courses.yaml").exists()


def test_us19_pasted_text(page: Page, live_server: str, data_dir: Path, rows) -> None:
    page.goto(live_server + "/courses/import", wait_until="domcontentloaded")
    page.get_by_label("Or paste the text").fill((EVAL / "transcript.txt").read_text(encoding="utf-8"))
    page.get_by_role("button", name="Read courses").click()
    page.get_by_role("button", name="Send to Claude").click()  # after the review step (#70)
    expect(page.locator("#courses-preview [data-row]")).to_have_count(28)


def test_us19_nothing_is_sent_before_send_to_claude(
    page: Page, live_server: str, data_dir: Path, rows, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The review step (DECISIONS #70): the stripped text with each removed value highlighted; no AI call
    and nothing written until Send to Claude; Cancel sends nothing."""
    calls = tmp_path / "fake-calls.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(calls))

    def count() -> int:
        return len(calls.read_text(encoding="utf-8").splitlines()) if calls.exists() else 0

    page.goto(live_server + "/courses/import", wait_until="domcontentloaded")
    page.get_by_label("Transcript file").set_input_files(str(EVAL / "transcript.docx"))
    page.get_by_role("button", name="Read courses").click()
    review = page.get_by_role("region", name="Text to be sent")
    expect(review).to_contain_text("FEB11001")
    expect(page.locator("#review-title")).to_be_focused()
    sent = page.locator("#courses-send input[name=text]").input_value()
    assert sent.count("[removed]") > 0
    expect(review.locator("mark.removed")).to_have_count(sent.count("[removed]"))
    for identifier in EXPECTED["identifiers"]:
        assert identifier not in page.content()
    assert count() == 0  # no AI call before Send
    assert not _serious(page), _serious(page)
    page.set_viewport_size({"width": 390, "height": 800})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert not _serious(page), _serious(page)
    page.get_by_role("link", name="Cancel").click()
    page.wait_for_url("**/courses/import")
    expect(page.get_by_label("Transcript file")).to_be_visible()
    assert count() == 0 and not (data_dir / "courses.yaml").exists() and not (data_dir / "sources").exists()
    page.get_by_label("Transcript file").set_input_files(str(EVAL / "transcript.docx"))
    page.get_by_role("button", name="Read courses").click()
    page.get_by_role("button", name="Send to Claude").click()
    expect(page.locator("#courses-preview [data-row]")).to_have_count(28)
    assert count() == 1


# ---- US-20 -----------------------------------------------------------------------------------------------


def test_us20_create_professors_from_the_document(
    page: Page, live_server: str, config, data_dir: Path, rows
) -> None:
    Store.from_config(config).create_professor(
        Professor(id="smit", name="Dr. Jan Smit", university="RSM", course="Organisation")
    )
    _import(page, live_server, "transcript.pdf")
    page.get_by_role("button", name="Accept all").click()
    page.get_by_role("button", name="Create professors from this document").click()
    lecturers = page.locator("#professors-preview [data-lecturer]")
    expect(lecturers).to_have_count(len(EXPECTED["lecturers"]))
    expect(page.locator("#professors-preview input[type=checkbox]:checked")).to_have_count(
        0
    )  # nothing ticked
    berg = page.locator("[data-lecturer='van-den-berg-a']")
    expect(berg).to_contain_text("Prof. dr. A. van den Berg")
    expect(berg).to_contain_text("Mathematics II")
    expect(berg).to_contain_text("8,5")
    smit = page.locator("[data-lecturer='smit-j']")
    expect(smit).to_contain_text("Link to Dr. Jan Smit")
    assert not _serious(page), _serious(page)
    before = sorted(p.name for p in (data_dir / "professors").glob("*.yaml"))
    berg.get_by_role("checkbox").check()
    smit.get_by_role("checkbox").check()
    page.locator("[data-lecturer='odegaard-l']").get_by_role("checkbox").check()
    assert sorted(p.name for p in (data_dir / "professors").glob("*.yaml")) == before  # nothing yet
    page.get_by_role("button", name="Create ticked professors").click()
    page.wait_for_url("**/recommenders**")
    expect(page.locator("main")).to_contain_text("Prof. dr. A. van den Berg")
    expect(page.locator("tr", has_text="Prof. dr. A. van den Berg")).to_contain_text("Email missing")
    smit_file = YAML(typ="safe").load((data_dir / "professors" / "smit.yaml").read_text(encoding="utf-8"))
    codes = {c["id"]: c["code"] for c in _courses(data_dir)}
    assert sorted(codes[i] for i in smit_file["course_ids"]) == sorted(EXPECTED["lecturers"]["Smit J"])
    assert (data_dir / "professors" / "odegaard.yaml").exists()


# ---- US-21 -----------------------------------------------------------------------------------------------


def test_us21_add_professor_suggests_courses(page: Page, live_server: str, data_dir: Path, rows) -> None:
    _import(page, live_server, "transcript.docx")
    page.get_by_role("button", name="Accept all").click()
    expect(page.locator("[data-row='0']")).to_contain_text("Saved")
    page.goto(live_server + "/recommenders/new", wait_until="domcontentloaded")
    page.get_by_label("Name", exact=True).fill("Prof. dr. Anna van den Berg")
    suggestions = page.locator("#course-suggestions")
    expect(suggestions).to_contain_text("Mathematics I")
    expect(suggestions).to_contain_text("Linear Algebra")
    assert not _serious(page), _serious(page)
    suggestions.get_by_role("button", name="Use Linear Algebra").click()
    expect(page.get_by_label("Course", exact=True)).to_have_value("Linear Algebra (FEB12004)")
    expect(page.get_by_label("Grade")).to_have_value("8,5")
    expect(page.get_by_label("Term")).to_have_value("2022-2023 block 2")
    expect(page.locator("#linked-courses")).to_contain_text("Linear Algebra")
    page.get_by_label("Grade").fill("8,5 / 10")  # still editable
    assert not (data_dir / "professors" / "van-den-berg.yaml").exists()  # nothing auto-saved
    page.get_by_role("button", name="Save", exact=True).click()
    page.wait_for_url("**/recommenders**")
    saved = [
        YAML(typ="safe").load(p.read_text(encoding="utf-8")) for p in (data_dir / "professors").glob("*.yaml")
    ]
    prof = next(p for p in saved if p["name"] == "Prof. dr. Anna van den Berg")
    assert prof["grade"] == "8,5 / 10" and prof["title"] == "Professor" and len(prof["course_ids"]) == 1
    assert prof.get("email") is None
