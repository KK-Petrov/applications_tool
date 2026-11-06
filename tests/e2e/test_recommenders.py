"""C-2a-01 (US-6 to US-9, docs/03 §2), US-10 (2b) and C-2a-03 (sheet speed), end to end in a browser.

The app runs against a temporary copy of tests/fixtures/data (professors jansen and whitfield) with the
clock fixed at Friday 30 October 2026, 12:00 New York. US-8 adds the letters fixture's professors.
"""

from __future__ import annotations

import json
import shutil
import statistics
import time
from datetime import date
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from ruamel.yaml import YAML

from hub.app import create_app
from hub.config import load_config
from hub.models.professor import Letter, LetterStatus, Professor
from hub.store import Store

from helpers import BASE, FIXED_NOW, RECORDINGS, FormClient, long_description
from paths import FIXTURES, REPO_ROOT

pytestmark = pytest.mark.e2e


def _professor(data_dir: Path, pid: str) -> dict:
    return YAML(typ="safe").load((data_dir / "professors" / f"{pid}.yaml").read_text(encoding="utf-8"))


def _cell(page: Page, prof: str, programme: str):
    return page.locator(f'td[data-cell="{prof}:{programme}"]')


def _shows(page: Page, prof: str, programme: str, status: str, label: str) -> None:
    """The cell holds ``status`` and its trigger shows ``label`` (the open menu's buttons don't count)."""
    cell = _cell(page, prof, programme)
    expect(cell).to_have_attribute("data-status", status)
    expect(cell.locator("summary")).to_contain_text(label)


def _row(page: Page, prof: str):
    return page.locator(f"#row-{prof}")


# ---- US-6 ----------------------------------------------------------------------------------------


def test_us6_add_professor_with_a_pasted_description(page: Page, live_server: str, data_dir: Path) -> None:
    text = long_description()
    page.goto(live_server + "/recommenders", wait_until="domcontentloaded")
    page.get_by_role("link", name="Add professor").click()
    page.get_by_label("Name", exact=True).fill("Prof. Rosa Lindqvist")
    page.get_by_label("Email").fill("r.lindqvist@example.com")
    page.get_by_label("University").select_option("RSM")
    page.get_by_label("Course", exact=True).fill("Behavioural Finance (FIN-330)")
    page.get_by_label("Course description").fill(text)
    page.get_by_label("Princeton MFin").check()
    page.get_by_label("Booth MiF").check()
    page.get_by_role("button", name="Save", exact=True).click()
    page.wait_for_url("**/recommenders?selected=lindqvist")
    _shows(page, "lindqvist", "princeton-mfin", "not_asked", "To ask")
    _shows(page, "lindqvist", "booth-mif", "not_asked", "To ask")
    _shows(page, "lindqvist", "mit-sloan-mfin", "none", "—")
    _shows(page, "lindqvist", "columbia-msfe", "none", "—")
    assert _professor(data_dir, "lindqvist")["course_description"] == text  # verbatim, 3,000 characters


# ---- US-7 ----------------------------------------------------------------------------------------


def test_us7_status_cell_sets_requested_and_updates_next_action(
    page: Page, live_server: str, data_dir: Path
) -> None:
    page.goto(live_server + "/recommenders", wait_until="domcontentloaded")
    expect(_row(page, "jansen")).to_contain_text("Send request by 3 Nov 2026")
    page.get_by_label("Change Princeton MFin status for Prof. Dr. Eva Jansen").click()
    page.get_by_role("button", name="Requested", exact=True).click()
    _shows(page, "jansen", "princeton-mfin", "requested", "Requested")
    expect(_row(page, "jansen").locator("td.next-action")).to_have_text("Waiting for reply")
    letter = _professor(data_dir, "jansen")["letters"]["princeton-mfin"]
    assert letter["status"] == "requested" and str(letter["requested_on"]) == "2026-10-30"
    page.reload(wait_until="domcontentloaded")
    _shows(page, "jansen", "princeton-mfin", "requested", "Requested")


def test_us7_status_menu_is_keyboard_operable(page: Page, live_server: str, data_dir: Path) -> None:
    page.goto(live_server + "/recommenders", wait_until="domcontentloaded")
    trigger = page.get_by_label("Change Booth MiF status for Prof. Dr. Eva Jansen")
    trigger.focus()
    page.keyboard.press("Enter")
    confirmed = page.get_by_role("button", name="Confirmed", exact=True)
    expect(confirmed).to_be_visible()
    confirmed.focus()
    page.keyboard.press("Enter")
    _shows(page, "jansen", "booth-mif", "confirmed", "Confirmed")
    assert str(_professor(data_dir, "jansen")["letters"]["booth-mif"]["confirmed_on"]) == "2026-10-30"


# ---- US-8 ----------------------------------------------------------------------------------------


@pytest.fixture
def with_letters(data_dir: Path) -> Path:
    """Add the letters fixture (13 invented professors and the programme early-msf) to the data folder."""
    for path in (FIXTURES / "letters" / "professors").glob("*.yaml"):
        shutil.copy(path, data_dir / "professors")
    shutil.copy(FIXTURES / "letters" / "programmes" / "early-msf.yaml", data_dir / "programmes")
    return data_dir


def _visible_rows(page: Page) -> list[str]:
    ids = page.locator("#sheet tbody tr[id^='row-']").evaluate_all("rows => rows.map(r => r.id.slice(4))")
    return list(ids)


def test_us8_search_filter_and_sort_work_together(page: Page, with_letters: Path, live_server: str) -> None:
    page.goto(live_server + "/recommenders", wait_until="domcontentloaded")
    assert len(_visible_rows(page)) == 15
    page.get_by_label("Filter").select_option("waiting")
    page.wait_for_url("**filter=waiting**")
    assert sorted(_visible_rows(page)) == ["moreau", "novak"]
    page.get_by_label("Filter").select_option("needs_action")
    page.wait_for_url("**filter=needs_action**")
    page.get_by_label("Sort").select_option("name")
    page.wait_for_url("**sort=name**")
    # course codes MA-…: Ferreira, Kowalski, Quinn, Santos; of these only the first two need action
    page.get_by_label("Search").fill("ma-")
    page.get_by_label("Search").press("Enter")
    page.wait_for_url("**q=ma-**")
    assert _visible_rows(page) == ["ferreira", "kowalski"]  # Prof. Fay Ferreira, Prof. Hana Kowalski
    expect(page.get_by_text("2 professors")).to_be_visible()
    page.get_by_label("Sort").select_option("next_action")
    page.wait_for_url("**sort=next_action**")
    assert _visible_rows(page) == ["kowalski", "ferreira"]  # thank-you since 25 Oct before request by 24 Nov
    expect(page.get_by_label("Search")).to_have_value("ma-")  # the search survives a sort change
    page.get_by_label("Filter").select_option("done")
    page.wait_for_url("**filter=done**")
    assert _visible_rows(page) == ["quinn", "santos"]  # only declined; all submitted and thanked or declined
    page.get_by_label("Search").fill("no such course")
    page.get_by_label("Search").press("Enter")
    page.wait_for_url("**q=no+such+course**")
    expect(page.get_by_text("No professors match")).to_be_visible()


def test_us8_sort_by_last_contact(page: Page, with_letters: Path, live_server: str) -> None:
    page.goto(live_server + "/recommenders?sort=last_contact", wait_until="domcontentloaded")
    rows = _visible_rows(page)
    # most recent first; professors with no contact at the end
    assert rows.index("moreau") < rows.index("brandt") < rows.index("rossi")


# ---- US-9 ----------------------------------------------------------------------------------------


def test_us9_row_opens_panel_and_course_edit_saves(page: Page, live_server: str, data_dir: Path) -> None:
    page.goto(live_server + "/recommenders", wait_until="domcontentloaded")
    expect(page.locator("#sheet")).not_to_contain_text("e.jansen@example.org")
    expect(page.locator("body")).not_to_contain_text("@example.org")
    page.get_by_role("link", name="Prof. Dr. Eva Jansen").click()
    panel = page.locator("#panel")
    expect(panel).to_contain_text("e.jansen@example.org")
    expect(page.locator("#sheet")).not_to_contain_text("e.jansen@example.org")
    panel.get_by_role("link", name="Edit").click()
    page.get_by_label("Course", exact=True).fill("Asset Pricing (FIN-400)")
    page.get_by_role("button", name="Save", exact=True).click()
    page.wait_for_url("**/recommenders?selected=jansen")
    expect(_row(page, "jansen")).to_contain_text("Asset Pricing (FIN-400)")
    expect(page.locator("#sheet")).not_to_contain_text("e.jansen@example.org")
    assert _professor(data_dir, "jansen")["course"] == "Asset Pricing (FIN-400)"


def test_us9_selected_row_is_highlighted(page: Page, live_server: str) -> None:
    page.goto(live_server + "/recommenders", wait_until="domcontentloaded")
    page.get_by_role("link", name="Prof. Sam Whitfield").click()
    expect(page.locator("#panel")).to_contain_text("s.whitfield@example.org")
    expect(_row(page, "whitfield")).to_have_attribute("aria-current", "true")
    expect(_row(page, "jansen")).not_to_have_attribute("aria-current", "true")


def test_mark_as_requested_from_the_panel(page: Page, live_server: str, data_dir: Path) -> None:
    page.goto(live_server + "/recommenders?selected=jansen", wait_until="domcontentloaded")
    panel = page.locator("#panel")
    panel.get_by_text("Mark as requested").click()
    # Phase 2b (disclosed): scoped to the checklist, as the panel now also has a Generate email checklist
    expect(panel.locator("details.mark-requested").get_by_label("Princeton MFin")).to_be_checked()
    panel.get_by_role("button", name="Mark ticked as requested").click()
    page.wait_for_url("**/recommenders?selected=jansen")
    _shows(page, "jansen", "princeton-mfin", "requested", "Requested")
    assert str(_professor(data_dir, "jansen")["letters"]["princeton-mfin"]["requested_on"]) == "2026-10-30"


# ---- US-10 (Phase 2b) -------------------------------------------------------------------------------


def test_us10_generate_copy_and_mark_requested(page: Page, live_server: str, data_dir: Path) -> None:
    """Generate shows subject, body and warnings; Copy copies; Mark as requested sets the letters."""
    recorded = json.loads((RECORDINGS / "rec-email.json").read_text(encoding="utf-8"))["structured_output"]
    page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=live_server)
    page.goto(live_server + "/recommenders?selected=jansen", wait_until="domcontentloaded")
    panel = page.locator("#panel")
    panel.get_by_role("button", name="Generate email").click()
    result = panel.locator("#email-result")
    expect(result).to_contain_text(recorded["subject"])
    expect(result).to_contain_text(recorded["body"].splitlines()[0])
    expect(result.get_by_role("alert")).to_contain_text("Princeton MFin's deadline is unverified")
    _shows(page, "jansen", "princeton-mfin", "draft_ready", "Draft ready")  # the row follows the draft
    for button, expected in (
        ("Copy body", recorded["body"]),
        ("Copy subject", recorded["subject"]),
        ("Refine in Claude Code", 'claude "/rec-email jansen"'),
    ):
        result.get_by_role("button", name=button).click()
        copied = page.evaluate("navigator.clipboard.readText()")
        assert copied.replace("\r\n", "\n") == expected, button  # the Windows clipboard stores CRLF
    panel.locator("details.mark-requested summary").click()
    # scoped: the generate checklist above also has a "Princeton MFin" box (disclosed, written this phase)
    expect(panel.locator("details.mark-requested").get_by_label("Princeton MFin", exact=True)).to_be_checked()
    panel.get_by_role("button", name="Mark ticked as requested").click()
    _shows(page, "jansen", "princeton-mfin", "requested", "Requested")
    stored = _professor(data_dir, "jansen")
    assert str(stored["letters"]["princeton-mfin"]["requested_on"]) == "2026-10-30"
    assert str(stored["emails"][-1]["marked_sent_on"]) == "2026-10-30"


def test_us10_generation_failure_is_shown_in_the_panel(
    page: Page, live_server: str, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "exit")
    before = (data_dir / "professors" / "jansen.yaml").read_bytes()
    page.goto(live_server + "/recommenders?selected=jansen", wait_until="domcontentloaded")
    page.locator("#panel").get_by_role("button", name="Generate email").click()
    expect(page.locator("#panel")).to_contain_text("Claude Code stopped with an error")
    expect(page.locator("#panel")).to_contain_text("Nothing was saved.")
    assert (data_dir / "professors" / "jansen.yaml").read_bytes() == before


# ---- C-2a-03: performance ------------------------------------------------------------------------

STATUSES = ["not_asked", "draft_ready", "requested", "confirmed", "submitted", "declined", None]
RUNS = 5


def _threshold_ms() -> float:
    criteria = json.loads((REPO_ROOT / "config" / "criteria.json").read_text(encoding="utf-8"))
    return float(next(c for c in criteria["criteria"] if c["id"] == "C-2a-03")["threshold"])


@pytest.fixture
def many_professors(data_dir: Path) -> int:
    """``performance.sheet_professors_benchmark`` invented professors × the four fixture programmes.

    Saved through the store, each with a 3,000-character description and a mix of letter statuses.
    """
    config = load_config()
    store = Store.from_config(config)
    count = config.performance.sheet_professors_benchmark
    shutil.rmtree(data_dir / "professors")
    (data_dir / "professors").mkdir()
    programmes = ["princeton-mfin", "mit-sloan-mfin", "booth-mif", "columbia-msfe"]
    for i in range(count):
        letters = {}
        for j, pid in enumerate(programmes):
            status = STATUSES[(i + j) % len(STATUSES)]
            if status:
                day = date(2026, 10, 1 + (i + j) % 28)
                letters[pid] = Letter(status=LetterStatus(status), requested_on=day, last_contact=day)
        store.save_professor(
            Professor(
                id=f"bench-{i:02d}",
                name=f"Prof. Bench Person{i:02d}",
                email=f"bench{i:02d}@example.com",
                university="RSM",
                course=f"Course {i:02d} (BN-{i:03d})",
                course_description=long_description(),
                grade="8 / 10",
                letters=letters,
            )
        )
    return count


@pytest.mark.serial
def test_performance_sheet_render_with_many_professors(
    page: Page, many_professors: int, live_server: str, config
) -> None:
    """sheet_render_ms_40_professors: the worst of 5 warm browser loads (server render included)."""
    # server render, in process
    client = FormClient(create_app(config, clock=lambda: FIXED_NOW), base_url=BASE, headers={"Origin": BASE})
    assert client.get("/recommenders").text.count('id="row-bench-') == many_professors
    server_ms = []
    for _ in range(RUNS):
        start = time.perf_counter()
        assert client.get("/recommenders").status_code == 200
        server_ms.append((time.perf_counter() - start) * 1000)

    # browser: navigation start → load event, the first (cold) load reported, then 5 warm loads gated
    def load_ms() -> float:
        page.goto(live_server + "/recommenders", wait_until="load")
        return float(page.evaluate("() => performance.getEntriesByType('navigation')[0].loadEventEnd"))

    cold = load_ms()
    expect(page.locator("#sheet tbody tr[id^='row-']")).to_have_count(many_professors)
    warm = [load_ms() for _ in range(RUNS)]
    worst = max(warm)
    print(
        f"\nC-2a-03 sheet_render_ms_40_professors = {worst:.0f} ms (worst of {RUNS} warm loads; "
        f"median {statistics.median(warm):.0f}, cold {cold:.0f}); server render median "
        f"{statistics.median(server_ms):.0f} ms, max {max(server_ms):.0f} ms"
    )
    assert worst < _threshold_ms()


def test_next_action_is_visible_on_a_1280_screen(page: Page, live_server: str) -> None:
    """The main message (what to do next) must be readable without scrolling sideways (ui-reviewer)."""
    page.set_viewport_size({"width": 1280, "height": 720})
    page.goto(live_server + "/recommenders", wait_until="load")
    box = page.locator("#row-jansen td.next-action").bounding_box()
    region = page.locator(".sheet-scroll").bounding_box()
    assert box is not None and region is not None
    assert box["x"] + box["width"] <= region["x"] + region["width"] + 1


def test_selection_by_click_survives_a_status_change_and_a_sort(page: Page, live_server: str) -> None:
    """Click a name, then change a status: the row stays selected and the panel updates (ui re-review)."""
    page.goto(live_server + "/recommenders?sort=name", wait_until="domcontentloaded")
    page.get_by_role("link", name="Prof. Dr. Eva Jansen").click()
    expect(page.locator("#panel")).to_contain_text("e.jansen@example.org")
    page.get_by_label("Change Booth MiF status for Prof. Dr. Eva Jansen").click()
    page.get_by_role("button", name="Confirmed", exact=True).click()
    _shows(page, "jansen", "booth-mif", "confirmed", "Confirmed")
    expect(_row(page, "jansen")).to_have_attribute("aria-current", "true")
    expect(page.locator("#panel")).to_contain_text("Booth MiF: Confirmed")
    page.get_by_role("link", name="Prof. Sam Whitfield").click()
    expect(page.locator("#panel")).to_contain_text("s.whitfield@example.org")
    page.get_by_label("Change MIT Sloan MFin status for Prof. Sam Whitfield").click()
    page.get_by_role("button", name="Ask for this programme").click()
    _shows(page, "whitfield", "mit-sloan-mfin", "not_asked", "To ask")
    expect(_row(page, "whitfield")).to_have_attribute("aria-current", "true")
    expect(page.locator("#panel")).to_contain_text("s.whitfield@example.org")
    page.get_by_label("Sort").select_option("last_contact")
    page.wait_for_url("**selected=whitfield**")
    expect(page.locator("#panel")).to_contain_text("s.whitfield@example.org")


def test_mark_as_requested_after_a_click_keeps_search_filter_and_sort(
    page: Page, with_letters: Path, live_server: str
) -> None:
    """Final ui-review WARN: the panel opened by a click must carry the sheet's view into its forms."""
    page.goto(
        live_server + "/recommenders?q=prof&filter=needs_action&sort=name", wait_until="domcontentloaded"
    )
    page.get_by_role("link", name="Prof. Ben Okafor").click()
    panel = page.locator("#panel")
    expect(panel).to_contain_text("okafor@example.org")
    panel.get_by_text("Mark as requested").click()
    panel.get_by_role("button", name="Mark ticked as requested").click()
    page.wait_for_url("**selected=okafor**")
    for part in ("q=prof", "filter=needs_action", "sort=name"):
        assert part in page.url, page.url


def test_us10_idle_indicator_is_hidden_and_focus_moves_to_the_draft(page: Page, live_server: str) -> None:
    """ui-reviewer: the idle "Generating…" text was in the accessibility tree; after a draft, focus and an
    announcement must go to it (not to <body> or the panel top)."""
    page.goto(live_server + "/recommenders?selected=jansen", wait_until="domcontentloaded")
    indicator = page.locator("#generating-jansen")
    assert indicator.evaluate("e => getComputedStyle(e).visibility") == "hidden"
    page.locator("#panel").get_by_role("button", name="Generate email").click()
    result = page.locator("#email-result")
    expect(result).to_be_focused()
    expect(page.locator("#live-status")).to_have_text("The draft email is ready below.")
    assert page.locator("#generating-jansen").evaluate("e => getComputedStyle(e).visibility") == "hidden"


def test_us10_live_region_is_in_the_page_before_any_action(page: Page, live_server: str) -> None:
    """ui re-review: a live region created and filled in the same tick is often not read."""
    page.goto(live_server + "/recommenders?selected=jansen", wait_until="domcontentloaded")
    region = page.locator("#live-status")
    expect(region).to_have_count(1)
    expect(region).to_have_attribute("aria-live", "polite")
    expect(region).to_have_text("")
