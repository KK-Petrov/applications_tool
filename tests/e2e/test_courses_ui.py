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
from playwright.sync_api import Browser, Page, Route, expect

from helpers import long_description, write_recording
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
    page.get_by_role("button", name="Send to Claude").click()  # after the review step (#70)
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
    page.get_by_role("button", name="Read courses").click()
    page.route("**/courses/import/send", handler)  # the call happens at Send (#70)
    page.get_by_role("button", name="Send to Claude").click()


def _assert_failed_visibly(page: Page) -> None:
    notice = page.locator("#courses-notice")
    expect(notice).to_contain_text("Nothing was saved")
    expect(notice.locator(".spinner")).to_be_hidden()
    expect(page.locator("main h1")).to_be_visible()  # the page is still there
    expect(page.get_by_role("button", name="Send to Claude")).not_to_have_attribute("aria-disabled", "true")
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
    page.get_by_role("button", name="Send to Claude").click()  # after the review step (#70)
    page.get_by_role("button", name="Accept all").click()
    page.get_by_role("button", name="Create professors from this document").click()
    page.locator("[data-lecturer^='odegaard-l-']").get_by_role("checkbox").check()
    page.locator("[data-lecturer^='bakker-e-']").get_by_role("checkbox").check()
    page.get_by_role("button", name="Create ticked professors").click()
    page.wait_for_url("**/recommenders?created=*", wait_until="domcontentloaded")
    # Scoped to main: the page has two role=status elements, the visible notice and the global #live-status
    # (outside main), and app.js copies the notice into #live-status 50 ms after load. Unscoped, the locator
    # matched both once those 50 ms had passed (a strict-mode violation, never retried): the 1-in-20 failure
    # under parallel load, reproduced every time with a 300 ms pause (DECISIONS #71).
    expect(page.locator("main").get_by_role("status").filter(has_text="Created 2 professors")).to_be_visible()
    expect(page.locator("#live-status")).to_contain_text("Created 2 professors")


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
    page.get_by_role("button", name="Send to Claude").click()
    notice = page.locator("#courses-notice")
    # W3 (session 3, disclosed, DECISIONS #75): the title was "Reading the courses."
    expect(notice.locator(".working-title")).to_contain_text(
        "Sending the text to Claude Code and reading the courses."
    )
    expect(notice.locator(".spinner")).to_be_visible()
    expect(notice.locator(".working-error")).to_have_count(0)
    assert "working-failed" not in (notice.get_attribute("class") or "")
    page.locator("#courses-preview").wait_for(timeout=20000)


def test_check_again_keeps_the_user_at_the_course_with_a_problem(page: Page, live_server: str, rows) -> None:
    _preview(page, live_server)
    page.locator("#row-0-grade").fill("8,0")
    page.get_by_role("button", name="Check again").click()
    # changed with disclosure (DECISIONS #85, user decision on ui round 5 W1): with the only problem corrected
    # the success notice gets focus (it was Course 1, which hid the notice); Course 1 is still in view
    expect(page.locator("#import-notice")).to_be_focused()
    expect(page.locator("#row-0 > legend")).to_be_in_viewport()
    expect(page.locator("[data-row='0']")).not_to_contain_text("Grade not found")


def test_text_pasted_into_a_locked_field_is_refused(page: Page, live_server: str, monkeypatch) -> None:
    """Moved to the research form (disclosed, DECISIONS #70): since the review step the course import's
    long call starts from a page without text fields; the lock is the same shared app.js code."""
    monkeypatch.setenv("FAKE_CLAUDE_DELAY", "4")
    page.goto(live_server + "/programmes/new", wait_until="domcontentloaded")
    page.get_by_label("Programme to research").fill("Example MSQF")
    page.get_by_role("button", name="Research a programme").click()
    page.locator('#research-run[data-busy="true"]').wait_for()
    page.locator("#research-url").focus()
    page.keyboard.insert_text("INSERTED")
    assert "INSERTED" not in page.locator("#research-url").input_value()


# ---- WCAG 2.4.11 Focus not obscured: the actions bar never covers the focused element (session 2) ---------

VIEWPORTS = {1280: 800, 760: 1024, 390: 844}
UNDER_BAR = """() => {
    const bar = document.querySelector('#import-actions').getBoundingClientRect();
    const el = document.activeElement;
    if (!el || el === document.body || el.closest('#import-actions')) return null;
    const r = el.getBoundingClientRect();
    const overlap = r.bottom > bar.top + 0.5 && r.top < bar.bottom - 0.5
        && r.right > bar.left && r.left < bar.right;
    return overlap
        ? `${el.id || el.tagName}: ${Math.round(r.top)}-${Math.round(r.bottom)} under the bar at `
          + `${Math.round(bar.top)}-${Math.round(bar.bottom)}`
        : null;
}"""


def _at(page: Page, live_server: str, width: int) -> None:
    page.set_viewport_size({"width": width, "height": VIEWPORTS[width]})
    _preview(page, live_server)


@pytest.mark.parametrize("width", list(VIEWPORTS))
def test_the_actions_bar_never_covers_the_course_focused_after_check_again(
    page: Page, live_server: str, rows, width: int
) -> None:
    _at(page, live_server, width)
    page.locator("#row-0-grade").fill("8,0")  # the planted bad grade, corrected
    page.locator("#row-20-grade").fill("1,0")  # a new problem low on the page
    page.get_by_role("button", name="Check again").click()
    expect(page.locator("#row-20")).to_be_focused()
    assert page.evaluate(UNDER_BAR) is None, page.evaluate(UNDER_BAR)


@pytest.mark.parametrize("width", list(VIEWPORTS))
def test_the_actions_bar_never_covers_the_course_focused_after_accept(
    page: Page, live_server: str, rows, width: int
) -> None:
    _at(page, live_server, width)
    page.locator("[data-row='22']").get_by_role("button", name="Accept").click()
    expect(page.locator("[data-row='22']")).to_contain_text("Saved")
    expect(page.locator("#row-22")).to_be_focused()
    assert page.evaluate(UNDER_BAR) is None, page.evaluate(UNDER_BAR)


@pytest.mark.parametrize("width", list(VIEWPORTS))
def test_the_actions_bar_never_covers_a_field_while_tabbing(
    page: Page, live_server: str, rows, width: int
) -> None:
    _at(page, live_server, width)
    page.locator("#row-0-name").focus()
    covered, stops = [], 0
    while stops < 400:
        problem = page.evaluate(UNDER_BAR)
        if problem:
            covered.append(problem)
        if page.evaluate("!!document.activeElement.closest('#import-actions')"):
            break  # reached the bar itself: every field before it was checked
        page.keyboard.press("Tab")
        stops += 1
    assert stops > 28 * 7, stops  # every card's fields were visited
    assert covered == [], covered[:5]


# ---- ui review round 3, F1: after Check again the course's name and its problem are in view (session 3) ----

IN_VIEW = """(sel) => {
    const bar = document.querySelector('#import-actions').getBoundingClientRect();
    const sticky = getComputedStyle(document.querySelector('#import-actions')).position === 'sticky';
    const bottom = sticky ? Math.min(bar.top, innerHeight) : innerHeight;
    return [...document.querySelectorAll(sel)].map(el => {
        const r = el.getBoundingClientRect();
        return r.top >= -0.5 && r.bottom <= bottom + 0.5
            ? null : `${sel}: ${Math.round(r.top)}-${Math.round(r.bottom)} outside 0-${Math.round(bottom)}`;
    }).filter(Boolean);
}"""


@pytest.fixture
def tall_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """As ``rows``, with a 1,600-character description on course 21 (a tall card, as in the round-3 probe)."""
    bad = [dict(r) for r in EXPECTED["courses"]]
    bad[0]["grade"] = "10,0"
    out = _output(bad)
    out["courses"][20]["description"] = long_description(1600)
    monkeypatch.setenv("FAKE_CLAUDE_RECORDING", str(write_recording(tmp_path, out, "courses.json")))


@pytest.mark.parametrize(("width", "height"), [(1280, 720), (960, 540), (1280, 800), (390, 844)])
def test_after_check_again_the_course_name_and_its_problem_are_in_view(
    page: Page, live_server: str, tall_rows, width: int, height: int
) -> None:
    """960x540 is 1920x1080 at 200 % zoom. The legend names the course; the refusal says what is wrong."""
    page.set_viewport_size({"width": width, "height": height})
    _preview(page, live_server)
    page.locator("#row-0-grade").fill("8,0")  # the planted bad grade, corrected
    page.locator("#row-20-grade").fill("1,0")  # a new problem low on the page
    page.get_by_role("button", name="Check again").click()
    expect(page.locator("#row-20")).to_be_focused()
    page.wait_for_timeout(150)  # app.js scrolls after focus
    for sel in ("#row-20 > legend", "#row-20 .refusal"):
        assert page.evaluate(IN_VIEW, sel) == [], page.evaluate(IN_VIEW, sel)
    expect(page.locator("#live-status")).to_contain_text("Grade not found in the document")


def test_without_js_check_again_names_the_course_and_links_to_it(
    browser: Browser, live_server: str, rows
) -> None:
    context = browser.new_context(java_script_enabled=False, viewport={"width": 1280, "height": 720})
    page = context.new_page()
    try:
        _preview_without_js(page, live_server)
        page.locator("#row-0-grade").fill("8,0")
        page.locator("#row-20-grade").fill("1,0")
        page.get_by_role("button", name="Check again").click()
        notice = page.locator("main .notice-problem")
        expect(notice).to_contain_text("Course 21")
        expect(notice).to_contain_text("Grade not found in the document")
        assert notice.bounding_box()["y"] < 720  # seen on arrival, at the top of the page
        notice.get_by_role("link").click()
        assert page.evaluate(IN_VIEW, "#row-20 > legend") == []
    finally:
        context.close()


def _preview_without_js(page: Page, live_server: str) -> None:
    page.goto(live_server + "/courses/import", wait_until="domcontentloaded")
    page.get_by_label("Transcript file").set_input_files(str(EVAL / "transcript.docx"))
    page.get_by_role("button", name="Read courses").click()
    page.get_by_role("button", name="Send to Claude").click()
    page.locator("#courses-preview").wait_for(timeout=20000)


@pytest.mark.parametrize(("width", "height"), [(800, 480), (768, 432), (1280, 720)])
def test_without_js_the_scroll_padding_covers_the_actions_bar(
    browser: Browser, live_server: str, rows, width: int, height: int
) -> None:
    """ui review round 3, W1: the no-JS fallback for the page's scroll padding must cover the actions bar
    even where it wraps to two rows (97 px at 768-800 px wide), plus the 8 px app.js adds."""
    context = browser.new_context(java_script_enabled=False, viewport={"width": width, "height": height})
    page = context.new_page()
    try:
        _preview_without_js(page, live_server)
        bar, padding = page.evaluate(
            """() => [document.querySelector('#import-actions').offsetHeight,
                      parseFloat(getComputedStyle(document.documentElement).scrollPaddingBottom)]"""
        )
        assert padding >= bar + 8, (bar, padding)
        assert padding >= 105
    finally:
        context.close()


def test_a_clean_check_again_announces_success_not_a_problem(page: Page, live_server: str, rows) -> None:
    """ui review round 4, N1."""
    _preview(page, live_server)
    page.locator("#row-0-grade").fill("8,0")  # the only problem, corrected
    page.get_by_role("button", name="Check again").click()
    expect(page.locator("main .notice").first).to_have_text("Checked again: every course can be accepted.")
    expect(page.locator(".notice-problem")).to_have_count(0)
    expect(page.locator("#live-status")).to_have_text("Checked again: every course can be accepted.")


@pytest.mark.parametrize(("width", "height"), [(390, 844), (1280, 800)])
def test_a_clean_check_again_shows_its_notice(
    page: Page, live_server: str, rows, width: int, height: int
) -> None:
    """ui review round 5, W1: with JS the success notice was above the viewport (focus on Course 1)."""
    page.set_viewport_size({"width": width, "height": height})
    _preview(page, live_server)
    page.locator("#row-0-grade").fill("8,0")
    page.get_by_role("button", name="Check again").click()
    notice = page.locator("#import-notice")
    expect(notice).to_be_focused()
    box = notice.bounding_box()
    assert box is not None and box["y"] >= 0 and box["y"] + box["height"] <= height, box
