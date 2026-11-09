"""The programme page header and the shared busy state of long AI calls (DECISIONS #63; user UI fix in 2c).

- Header: the subtitle (unit · school · portal) sits directly under the title; the actions (Edit,
  Re-research, Mark verified) are in their own row below it, at 1280, 760 and 390 px.
- Busy state (``data-working`` forms: new research, re-research, later course import): the working button
  shows a spinner and the muted busy styling and stays ``aria-disabled``; every other action on the page that
  could change the same file is ``aria-disabled`` and does nothing; a notice below the actions says what is
  happening, with an elapsed timer and the limit, and what leaving the page does; the live region announces
  only the start (never a timer tick), then the finish or the failure; when the call ends, focus moves to
  the result's heading (or the error). axe finds nothing serious mid-request.
The fake Claude Code answers after ``FAKE_CLAUDE_DELAY`` seconds, so the busy state can be inspected.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import Page, expect

from hub.store import Store

from helpers import mirror_output, research_output, write_recording

pytestmark = pytest.mark.e2e

PID = "princeton-mfin"
BCF = "https://bcf.princeton.edu/academic-programs/master-in-finance/apply-for-the-master-in-finance-program"
AXE = Axe()
TIMER = re.compile(r"^(\d+ s|\d+ min \d+ s)$")
START = "Researching the official pages."


def _serious(page: Page) -> list[str]:
    results = AXE.run(page)
    return [
        f"{v['id']} ({v['impact']}): {[', '.join(n['target']) for n in v['nodes']][:5]}"
        for v in results.response["violations"]
        if v.get("impact") in {"serious", "critical"}
    ]


def _box(page: Page, selector: str) -> dict:
    box = page.locator(selector).first.bounding_box()
    assert box is not None, f"{selector} is not visible"
    return box


@pytest.fixture
def slow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config):
    """The fake answers re-research (a mirror of Princeton) after a delay."""
    programme = next(p for p in Store.from_config(config).load_programmes()[0] if p.id == PID)
    monkeypatch.setenv("FAKE_CLAUDE_RECORDING", str(write_recording(tmp_path, mirror_output(programme, BCF))))
    monkeypatch.setenv("FAKE_CLAUDE_DELAY", "6")


# ---- header layout ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("width", [1280, 760, 390])
def test_subtitle_is_directly_under_the_title_and_actions_below_it(
    page: Page, live_server: str, width: int
) -> None:
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{live_server}/programmes/{PID}", wait_until="load")
    page.evaluate("document.fonts.ready.then(() => true)")
    # measured in one go, so a late font swap can't shift one box between two measurements
    h1, subtitle, actions = page.evaluate(
        """() => ['main h1', 'main .subtitle', 'main .page-actions'].map(s => {
            const r = document.querySelector(s).getBoundingClientRect();
            return {y: r.top, height: r.height};
        })"""
    )
    # DOM order: title, subtitle, actions
    order = page.evaluate(
        """() => {
            const h = document.querySelector('main h1');
            const next = h.nextElementSibling;
            const after = next && next.nextElementSibling;
            return [Boolean(next && next.classList.contains('subtitle')),
                    Boolean(after && after.classList.contains('page-actions'))];
        }"""
    )
    assert order == [True, True], order
    gap = subtitle["y"] - (h1["y"] + h1["height"])
    assert 0 <= gap <= 16, f"subtitle {gap:.0f} px below the title at {width} px"
    assert actions["y"] >= subtitle["y"] + subtitle["height"], f"actions overlap the subtitle at {width} px"
    assert actions["y"] - (subtitle["y"] + subtitle["height"]) <= 32
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (
        f"sideways scroll at {width}"
    )


# ---- busy state ------------------------------------------------------------------------------------------


def test_reresearch_busy_state_locks_the_page_and_shows_a_timer(
    page: Page, live_server: str, data_dir: Path, slow
) -> None:
    path = data_dir / "programmes" / f"{PID}.yaml"
    original = path.read_bytes()
    page.goto(f"{live_server}/programmes/{PID}", wait_until="domcontentloaded")
    page.locator("#reresearch-form button[type=submit]").click(no_wait_after=True)
    form = page.locator('#reresearch-form[data-busy="true"]')
    form.wait_for()
    button = page.locator("#reresearch-form button[type=submit]")
    expect(button).to_have_attribute("aria-disabled", "true")
    assert page.evaluate(
        "getComputedStyle(document.querySelector('#reresearch-form button[type=submit]'), '::before').content"
    ) not in ("none", "normal", ""), "no spinner on the working button"
    assert page.evaluate("getComputedStyle(document.querySelector('#reresearch-form button')).opacity") == "1"
    # Every other action that could change programmes/princeton-mfin.yaml is aria-disabled.
    for selector in (
        "main a[data-writes]",  # Edit
        "#verify-form button",  # Mark verified
        "#requirements input[type=checkbox]",
        "#steps input[type=checkbox]",
        "#documents select",
    ):
        locator = page.locator(selector)
        assert locator.count() > 0, selector
        for i in range(locator.count()):
            expect(locator.nth(i)).to_have_attribute("aria-disabled", "true")
    page.locator("#requirements input[type=checkbox]").first.click(force=True, no_wait_after=True)
    links = page.locator("main a[data-writes]")  # Edit, Add standard steps and documents (#97, #100)
    for i in range(links.count()):  # each locked link is inert: the page stays (checked by the notice below)
        links.nth(i).click(force=True, no_wait_after=True)
    # The notice: below the actions, with the limit, what leaving does, and a timer that advances.
    notice = page.locator("#reresearch-notice")
    expect(notice).to_be_visible()
    expect(notice).to_contain_text(START)
    expect(notice).to_contain_text("Stops after 5 minutes")
    expect(notice).to_contain_text("If you leave this page")
    assert _box(page, "#reresearch-notice")["y"] >= _box(page, "main .page-actions")["y"]
    timer = page.locator("#reresearch-notice .working-timer")
    first = timer.inner_text()
    assert TIMER.match(first), first
    live_at_start = page.locator("#live-status").inner_text()
    assert live_at_start.startswith(START), live_at_start
    violations = _serious(page)  # axe mid-request
    assert not violations, "re-research while working:\n" + "\n".join(violations)
    time.sleep(2.2)
    second = timer.inner_text()
    assert second != first and TIMER.match(second), (first, second)
    assert page.locator("#live-status").inner_text() == live_at_start  # no timer ticks announced
    assert path.read_bytes() == original  # the blocked toggle wrote nothing
    # Finished: the comparison, focus on its heading, the finish announced.
    page.locator("#research-apply").wait_for(timeout=20000)
    expect(page.locator("#compare-title")).to_be_focused()
    expect(page.locator("#live-status")).to_contain_text("Research finished")
    assert path.read_bytes() == original


def test_new_research_busy_state_and_focus_on_the_domains_step(
    page: Page, live_server: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_RECORDING", str(write_recording(tmp_path, research_output())))
    monkeypatch.setenv("FAKE_CLAUDE_DELAY", "4")
    page.goto(live_server + "/", wait_until="domcontentloaded")
    page.get_by_label("Programme to research").fill("Example MSQF")
    page.locator("#research-run button[type=submit]").click(no_wait_after=True)
    page.locator('#research-run[data-busy="true"]').wait_for()
    expect(page.locator("#research-run button[type=submit]")).to_have_attribute("aria-disabled", "true")
    notice = page.locator("#research-run-notice")
    expect(notice).to_be_visible()
    expect(notice).to_contain_text("Nothing is saved until you press Save")
    assert not _serious(page)
    page.locator("#research-domains").wait_for(timeout=20000)
    expect(page.locator("#domains-title")).to_be_focused()


def test_a_failed_reresearch_moves_focus_to_the_error_and_announces_it(
    page: Page, live_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "exit")
    page.goto(f"{live_server}/programmes/{PID}", wait_until="domcontentloaded")
    page.locator("#reresearch-form button[type=submit]").click()
    error = page.locator("#research-error")
    expect(error).to_be_focused()
    expect(page.locator("#live-status")).to_contain_text("Research failed")
