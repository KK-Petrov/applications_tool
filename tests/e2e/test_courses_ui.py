"""ui-reviewer FAILs at the Phase 2c review, each with a guard (DECISIONS #65).

1. The import preview can be checked at 1280, 760 and 390 px: one card per course, every field visible (no
   value cut off, nothing past its card), and after Accept the user stays at that course.
2. A disabled button looks disabled (not like an enabled one).
3. A long call that ends without a page (network failure, a non-HTML 5xx) never blanks the page or leaves a
   spinner turning: the notice turns into a visible error and the controls unlock.
4. Creating professors from a document confirms it on the sheet (shown and announced).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from playwright.sync_api import Page, Route, expect

from helpers import write_recording
from paths import REPO_ROOT

pytestmark = pytest.mark.e2e

EVAL = REPO_ROOT / "tests" / "evals" / "courses"
EXPECTED = json.loads((EVAL / "expected.json").read_text(encoding="utf-8"))


def _output(rows: list[dict]) -> dict:
    keys = ("name", "code", "institution", "grade", "credits", "term", "lecturers")
    return {"courses": [{**{k: r[k] for k in keys}, "description": ""} for r in rows], "warnings": []}


@pytest.fixture
def rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad = [dict(r) for r in EXPECTED["courses"]]
    bad[0]["grade"] = "10,0"
    monkeypatch.setenv("FAKE_CLAUDE_RECORDING", str(write_recording(tmp_path, _output(bad), "courses.json")))


def _preview(page: Page, live_server: str) -> None:
    page.goto(live_server + "/courses/import", wait_until="domcontentloaded")
    page.get_by_label("Transcript file").set_input_files(str(EVAL / "transcript.docx"))
    page.get_by_role("button", name="Read courses").click()
    page.locator("#courses-preview").wait_for(timeout=20000)


@pytest.mark.parametrize("width", [1280, 760, 390])
def test_every_course_field_is_fully_visible_at_every_width(
    page: Page, live_server: str, rows, width: int
) -> None:
    page.set_viewport_size({"width": width, "height": 900})
    _preview(page, live_server)
    problems = page.evaluate(
        """() => {
            const out = [];
            document.querySelectorAll('#courses-preview [data-row]').forEach(card => {
                const box = card.getBoundingClientRect();
                card.querySelectorAll('input:not([type=hidden]), textarea, button, .refusal').forEach(el => {
                    const r = el.getBoundingClientRect();
                    const outside = r.right > box.right + 1 || r.left < box.left - 1;
                    if (outside) out.push(`${card.id} ${el.id || el.className} outside`);
                    const cut = el.tagName === 'INPUT' && el.scrollWidth > el.clientWidth + 1;
                    if (cut) out.push(`${el.id} cut off`);
                });
            });
            if (document.documentElement.scrollWidth > window.innerWidth) out.push('page scrolls sideways');
            return out;
        }"""
    )
    assert problems == [], problems[:10]
    expect(page.locator("#courses-preview [data-row]")).to_have_count(28)


def test_after_accept_the_user_stays_at_that_course(page: Page, live_server: str, rows) -> None:
    page.set_viewport_size({"width": 1280, "height": 900})
    _preview(page, live_server)
    page.locator("[data-row='5']").get_by_role("button", name="Accept").click()
    expect(page.locator("[data-row='5']")).to_contain_text("Saved")
    expect(page.locator("#row-5")).to_be_focused()
    assert page.evaluate("window.scrollY") > 300  # not thrown back to the top


def test_a_disabled_button_looks_disabled(page: Page, live_server: str, rows) -> None:
    _preview(page, live_server)
    styles = page.evaluate(
        """() => ['[data-row="0"] button[name=accept]', '[data-row="1"] button[name=accept]'].map(s => {
            const c = getComputedStyle(document.querySelector(s));
            return [c.color, c.backgroundColor, c.borderColor, c.cursor];
        })"""
    )
    disabled, enabled = styles
    assert disabled != enabled and disabled[3] == "not-allowed", styles


def _start_import_with(page: Page, live_server: str, handler) -> None:  # noqa: ANN001
    page.goto(live_server + "/courses/import", wait_until="domcontentloaded")
    page.get_by_label("Or paste the text").fill("FEB1\tCourse one\t8,0\t6\n")
    page.route("**/courses/import", handler)
    page.get_by_role("button", name="Read courses").click()


def _assert_failed_visibly(page: Page) -> None:
    notice = page.locator("#courses-notice")
    expect(notice).to_contain_text("Nothing was saved")
    expect(notice.locator(".spinner")).to_be_hidden()
    expect(page.locator("main h1")).to_be_visible()  # the page is still there
    expect(page.get_by_role("button", name="Read courses")).not_to_have_attribute("aria-disabled", "true")
    expect(page.locator("[data-locked]")).to_have_count(0)


def test_a_network_failure_mid_call_shows_an_error_and_unlocks(page: Page, live_server: str) -> None:
    _start_import_with(page, live_server, lambda route: route.abort())
    _assert_failed_visibly(page)
    expect(page.locator("#courses-notice")).to_contain_text("could not be reached")
    expect(page.locator("#live-status")).not_to_contain_text("Research")


def test_a_plain_text_server_error_never_blanks_the_page(page: Page, live_server: str) -> None:
    def fail(route: Route) -> None:
        route.fulfill(status=500, body="Internal Server Error", content_type="text/plain")

    _start_import_with(page, live_server, fail)
    _assert_failed_visibly(page)
    expect(page.locator("#courses-notice")).to_contain_text("500")


def test_creating_professors_is_confirmed_on_the_sheet(
    page: Page, live_server: str, tmp_path: Path, monkeypatch
) -> None:
    rec = write_recording(tmp_path, _output(EXPECTED["courses"]), "ok.json")
    monkeypatch.setenv("FAKE_CLAUDE_RECORDING", str(rec))
    page.goto(live_server + "/courses/import", wait_until="domcontentloaded")
    page.get_by_label("Transcript file").set_input_files(str(EVAL / "transcript.pdf"))
    page.get_by_role("button", name="Read courses").click()
    page.get_by_role("button", name="Accept all").click()
    page.get_by_role("button", name="Create professors from this document").click()
    page.locator("[data-lecturer='odegaard-l']").get_by_role("checkbox").check()
    page.locator("[data-lecturer='bakker-e']").get_by_role("checkbox").check()
    page.get_by_role("button", name="Create ticked professors").click()
    page.wait_for_url("**/recommenders?created=*", wait_until="domcontentloaded")
    # 15 s: under 8 parallel workers the redirect and the sheet can take longer than the default 5 s
    expect(page.get_by_role("status").filter(has_text="Created 2 professors")).to_be_visible(timeout=15000)
    expect(page.locator("#live-status")).to_contain_text("Created 2 professors", timeout=15000)


# ---- round 2 (ui review): retry after a failure, focus on the error, Check again, pasted text -------------


def test_a_retry_after_a_failure_starts_clean(
    page: Page, live_server: str, tmp_path: Path, monkeypatch
) -> None:
    rec = write_recording(tmp_path, _output(EXPECTED["courses"][:2]), "two.json")
    monkeypatch.setenv("FAKE_CLAUDE_RECORDING", str(rec))
    monkeypatch.setenv("FAKE_CLAUDE_DELAY", "4")
    attempts = {"n": 0}

    def first_fails(route: Route) -> None:
        attempts["n"] += 1
        if attempts["n"] == 1:
            route.abort()
        else:
            route.continue_()

    _start_import_with(page, live_server, first_fails)
    error = page.locator("#courses-notice .working-error")
    expect(error).to_be_visible()
    expect(error).to_be_focused()  # focus goes to the error (docs/03 §5)
    expect(page.locator("#courses-notice .working-meta")).to_be_hidden()  # no stale timer or limit line
    page.get_by_role("button", name="Read courses").click()
    notice = page.locator("#courses-notice")
    expect(notice.locator(".working-title")).to_contain_text("Reading the courses.")
    expect(notice.locator(".spinner")).to_be_visible()
    expect(notice.locator(".working-error")).to_have_count(0)
    assert "working-failed" not in (notice.get_attribute("class") or "")
    page.locator("#courses-preview").wait_for(timeout=20000)


def test_check_again_keeps_the_user_at_the_course_with_a_problem(page: Page, live_server: str, rows) -> None:
    _preview(page, live_server)
    page.locator("#row-0-grade").fill("8,0")
    page.get_by_role("button", name="Check again").click()
    expect(page.locator("#row-0")).to_be_focused()
    expect(page.locator("[data-row='0']")).not_to_contain_text("Grade not found")


def test_text_pasted_into_a_locked_field_is_refused(page: Page, live_server: str, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_DELAY", "4")
    page.goto(live_server + "/courses/import", wait_until="domcontentloaded")
    page.get_by_label("Or paste the text").fill("FEB1\tCourse one\t8,0\t6\n")
    page.get_by_role("button", name="Read courses").click()
    page.locator('#courses-upload[data-busy="true"]').wait_for()
    page.locator("#import-paste").focus()
    page.keyboard.insert_text("INSERTED")
    assert "INSERTED" not in page.locator("#import-paste").input_value()
