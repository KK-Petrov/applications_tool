"""C-1-01 (provenance) and C-1-04 (US-1 to US-5, docs/03 §2), end to end in a real browser.

The app runs against a temporary copy of tests/fixtures/data with the clock fixed at
Friday 30 October 2026, 12:00 New York (tests/e2e/conftest.py).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from ruamel.yaml import YAML

from hub.rules.programmes import NOT_CHECKED_REASON

pytestmark = pytest.mark.e2e

PROGRAMMES = ["princeton-mfin", "mit-sloan-mfin", "booth-mif", "columbia-msfe"]
ZONE = r"(ET|CT|MT|PT|CET|CEST|UTC)\b"
TIME = re.compile(r"\b\d{1,2}:\d{2}(?: ?[ap]m)?")
TIME_WITH_ZONE = re.compile(r"\b\d{1,2}:\d{2}(?: ?[ap]m)? " + ZONE)


def _set_zone(page: Page, base: str, label: str) -> None:
    page.goto(base + "/", wait_until="domcontentloaded")
    with page.expect_navigation(wait_until="domcontentloaded"):  # HTMX answers with HX-Refresh: a reload
        page.get_by_label("Times in:").select_option(label=label)
    expect(page.get_by_label("Times in:")).to_have_value(
        "Europe/Amsterdam" if "Rotterdam" in label else "America/New_York"
    )


def _pages(base: str) -> list[str]:
    """Pages that print deadlines or times (recommender panels added at the 2b ui review, round 4)."""
    panels = [base + "/recommenders?selected=jansen", base + "/recommenders?selected=whitfield"]
    return [base + "/", base + "/programmes"] + [f"{base}/programmes/{pid}" for pid in PROGRAMMES] + panels


def _source_urls(data_dir: Path) -> dict[str, str]:
    out = {}
    for pid in PROGRAMMES:
        raw = YAML(typ="safe").load((data_dir / "programmes" / f"{pid}.yaml").read_text(encoding="utf-8"))
        for d in raw["deadlines"]:
            out[f"{pid}:{d['id']}"] = d["source_url"]
    return out


# ---- C-1-01 provenance -------------------------------------------------------------------------------


@pytest.mark.parametrize("zone", ["Eastern Time (Charlottesville)", "Central European Time (Rotterdam)"])
def test_provenance_every_time_has_a_zone(page: Page, live_server: str, zone: str) -> None:
    _set_zone(page, live_server, zone)
    for url in _pages(live_server):
        page.goto(url, wait_until="domcontentloaded")
        text = page.locator("main").inner_text()
        times = TIME.findall(text)
        zoned = TIME_WITH_ZONE.findall(text)
        bare = [m.group(0) for m in TIME.finditer(text) if not TIME_WITH_ZONE.match(text, m.start())]
        assert times, f"no times found on {url}"
        assert not bare, f"{url}: times without a zone: {bare}"
        assert len(zoned) == len(times)


@pytest.mark.parametrize("zone", ["Eastern Time (Charlottesville)", "Central European Time (Rotterdam)"])
def test_provenance_every_deadline_has_zone_and_source(
    page: Page, live_server: str, data_dir: Path, zone: str
) -> None:
    sources = _source_urls(data_dir)
    _set_zone(page, live_server, zone)
    for url in _pages(live_server):
        page.goto(url, wait_until="domcontentloaded")
        deadlines = page.locator("[data-deadline]")
        assert deadlines.count() >= 1, f"no deadline component on {url}"
        for i in range(deadlines.count()):
            el = deadlines.nth(i)
            key = el.get_attribute("data-deadline")
            assert re.search(ZONE, el.inner_text()), f"{url}: deadline {key} without zone"
            link = el.locator("a[href]")
            assert link.count() >= 1, f"{url}: deadline {key} without source link"
            assert link.first.get_attribute("href") == sources[key]


def test_provenance_unverified_is_marked_with_reason(page: Page, live_server: str) -> None:
    reasons = {
        "princeton-mfin": "Last cycle's date",
        "mit-sloan-mfin": "Researched, not checked",
        "booth-mif": "Researched, not checked",
        "columbia-msfe": "Researched, not checked",
    }
    page.goto(live_server + "/", wait_until="domcontentloaded")
    for pid, reason in reasons.items():
        row = page.locator(f"tr[data-programme='{pid}']")
        expect(row.locator(".badge-unverified")).to_contain_text("Unverified")
        expect(row.locator(".badge-unverified")).to_contain_text(reason)
    for pid, reason in reasons.items():
        page.goto(f"{live_server}/programmes/{pid}", wait_until="domcontentloaded")
        badge = page.locator("main .badge-unverified").first
        expect(badge).to_be_visible()
        expect(badge).to_contain_text("Unverified")
        expect(badge).to_contain_text(reason)


# The smallest element around a deadline that also holds an Unverified badge must not hold another
# programme's deadline: otherwise the badge belongs to something else (e.g. a table row lower down).
_BADGE_SCOPE_JS = """
(el) => {
  const pid = el.dataset.deadline.split(":")[0];
  for (let node = el.parentElement; node; node = node.parentElement) {
    if (node.querySelector(".badge-unverified")) {
      const others = [...node.querySelectorAll("[data-deadline]")]
        .filter((d) => d.dataset.deadline.split(":")[0] !== pid);
      if (others.length) return null;
      return [...node.querySelectorAll(".badge-unverified")].map((b) => b.textContent);
    }
  }
  return null;
}
"""


def test_provenance_every_unverified_deadline_has_its_badge(
    page: Page, live_server: str, data_dir: Path
) -> None:
    """Rule 11 / C-1-01: next to every unverified programme's deadline sits a badge with that programme's
    reason (2b ui review rounds 4-6: the generate checklist, then the dashboard alert strip and Next deadline
    tile, printed one with no badge beside it). Pages: _pages() plus the Add and Edit professor forms."""
    reasons = {}
    for pid in PROGRAMMES:
        raw = YAML(typ="safe").load((data_dir / "programmes" / f"{pid}.yaml").read_text(encoding="utf-8"))
        if raw["verification"] != "verified":
            reasons[pid] = raw.get("verification_note") or NOT_CHECKED_REASON
    assert reasons, "the fixture should have unverified programmes"
    pages = _pages(live_server) + [
        live_server + "/recommenders/new",
        live_server + "/recommenders/jansen/edit",
    ]
    missing = []
    for url in pages:
        page.goto(url, wait_until="domcontentloaded")
        deadlines = page.locator("main [data-deadline]")
        for i in range(deadlines.count()):
            el = deadlines.nth(i)
            key = el.get_attribute("data-deadline") or ""
            pid = key.split(":")[0]
            if pid not in reasons:
                continue
            badges = el.evaluate(_BADGE_SCOPE_JS)
            if not badges or not any(reasons[pid] in text for text in badges):
                missing.append(f"{url.removeprefix(live_server)}: {key} (badges: {badges})")
    assert not missing, "unverified deadlines without their badge beside them: " + "; ".join(missing)


LONG_NOTE = (
    "Taken from a forum post about last year's cycle; the admissions office has not replied to my email"
)

URL_NOTE = "https://example.com/admissions/faq/deadlines-for-the-2027-entry-cycle-not-yet-confirmed"

# The smallest box around each badge that it must stay inside: the tile or the alert on the dashboard.
_BADGE_OVERFLOW_JS = """
() => [...document.querySelectorAll(".tile .badge-unverified, .alert .badge-unverified")].map((b) => {
  const box = b.closest(".tile, .alert");
  const style = getComputedStyle(box);
  const inner = box.getBoundingClientRect().right - parseFloat(style.paddingRight);
  return b.getBoundingClientRect().right - inner;
})
"""


@pytest.mark.parametrize("viewport", [(1280, 720), (1100, 720), (760, 900), (390, 844)])
@pytest.mark.parametrize(
    "note", ["Researched, not checked", "Deadline changed, not checked", LONG_NOTE, URL_NOTE]
)
def test_unverified_badge_wraps_inside_its_tile_and_alert(
    page: Page, live_server: str, data_dir: Path, note: str, viewport: tuple[int, int]
) -> None:
    """2b ui review round 6: the chip could not wrap, so longer reasons (docs/02 §3; free-text notes) spilled
    out of the Next deadline tile and the alert, and a long note scrolled the whole page at 390 px."""
    path = data_dir / "programmes" / "princeton-mfin.yaml"
    text = path.read_text(encoding="utf-8")
    assert "verification_note: Last cycle's date" in text
    path.write_text(
        text.replace("verification_note: Last cycle's date", f'verification_note: "{note}"'), "utf-8"
    )
    page.set_viewport_size({"width": viewport[0], "height": viewport[1]})
    for url in ("/", "/recommenders?selected=jansen", "/recommenders/new"):
        page.goto(live_server + url, wait_until="domcontentloaded")
        expect(page.locator("main .badge-unverified").first).to_contain_text(note)
        if url == "/":
            overflow = page.evaluate(_BADGE_OVERFLOW_JS)
            assert len(overflow) >= 2, "expected the badge in the alert and the Next deadline tile"
            assert max(overflow) <= 0.5, f"badge sticks out of its box by {max(overflow):.0f} px"
        widths = page.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
        assert widths[0] <= widths[1], f"{url}: page scrolls sideways ({widths[0]} > {widths[1]})"


# Lines taken by the word "Unverified" (the badge's first text node) in each badge on the page.
_UNVERIFIED_WORD_LINES_JS = """
() => [...document.querySelectorAll("main .badge-unverified")].map((b) => {
  const range = document.createRange();
  range.selectNodeContents(b.firstChild);
  return range.getClientRects().length;
})
"""


@pytest.mark.parametrize("viewport", [(1280, 720), (1100, 720), (760, 900), (390, 844)])
@pytest.mark.parametrize("note", [None, LONG_NOTE, URL_NOTE])
def test_unverified_badge_never_breaks_inside_a_word_in_the_table(
    page: Page, live_server: str, data_dir: Path, note: str | None, viewport: tuple[int, int]
) -> None:
    """2b ui review round 7: a global overflow-wrap: anywhere let the auto-layout table squeeze the column
    to one character, so the badge printed one letter per line on / and /programmes."""
    if note:
        path = data_dir / "programmes" / "princeton-mfin.yaml"
        text = path.read_text(encoding="utf-8")
        assert "verification_note: Last cycle's date" in text
        path.write_text(
            text.replace("verification_note: Last cycle's date", f'verification_note: "{note}"'), "utf-8"
        )
    page.set_viewport_size({"width": viewport[0], "height": viewport[1]})
    for url in ("/", "/programmes"):
        page.goto(live_server + url, wait_until="domcontentloaded")
        lines = page.evaluate(_UNVERIFIED_WORD_LINES_JS)
        assert len(lines) >= 4, f"{url}: expected a badge per programme row"
        assert max(lines) == 1, f"{url}: the word Unverified is broken over {max(lines)} lines"


def test_provenance_time_to_verify_and_school_original(page: Page, live_server: str) -> None:
    page.goto(live_server + "/programmes/booth-mif", wait_until="domcontentloaded")
    expect(page.locator("main")).to_contain_text("(time to verify)")
    expect(page.locator("main")).to_contain_text("7 Jan 2027, 11:59 pm CT")  # school-zone original


# ---- US-1 dashboard ------------------------------------------------------------------------------------


def test_us1_dashboard_lists_programmes_sorted(page: Page, live_server: str) -> None:
    page.goto(live_server + "/", wait_until="domcontentloaded")
    rows = page.locator("table.programmes tbody tr")
    expect(rows).to_have_count(4)
    assert [rows.nth(i).get_attribute("data-programme") for i in range(4)] == [
        "princeton-mfin",
        "mit-sloan-mfin",
        "booth-mif",
        "columbia-msfe",
    ]
    princeton = rows.nth(0)
    expect(princeton).to_contain_text("Princeton MFin")
    expect(princeton).to_contain_text("Bendheim Center for Finance")
    expect(princeton).to_contain_text("15 Dec 2026, 11:59 pm ET")
    expect(princeton).to_contain_text("46 days")
    expect(princeton).to_contain_text("0 / 3")  # letters confirmed / required
    expect(princeton).to_contain_text("0 / 4 received")
    expect(princeton).to_contain_text("Not started")
    expect(rows.nth(2)).to_contain_text("assumed")  # Booth: letters_required null → 2 assumed
    expect(page.locator(".alert")).to_contain_text("Request Princeton MFin recommendations by 3 Nov 2026")
    expect(page.locator(".alert")).to_contain_text("3 letters needed, 0 confirmed")
    tiles = page.locator(".tiles")
    expect(tiles).to_contain_text("Next deadline")
    expect(tiles).to_contain_text("46 days")
    expect(tiles).to_contain_text("Programmes tracked")
    expect(tiles).to_contain_text("4 unverified")
    expect(tiles).to_contain_text("Letters confirmed")
    expect(tiles).to_contain_text("Essays evaluated")
    expect(page.locator("main")).to_contain_text("Reminders arrive in Phase 3")


def test_us1_row_links_to_programme_page(page: Page, live_server: str) -> None:
    page.goto(live_server + "/", wait_until="domcontentloaded")
    page.get_by_role("link", name="Princeton MFin").first.click()
    page.wait_for_url("**/programmes/princeton-mfin")
    expect(page.get_by_role("heading", level=1)).to_have_text("Princeton Master in Finance")


# ---- US-2 display zone ---------------------------------------------------------------------------------


def test_us2_switching_zone_changes_every_time_and_persists(page: Page, live_server: str) -> None:
    page.goto(live_server + "/", wait_until="domcontentloaded")
    expect(page.locator("main")).to_contain_text("15 Dec 2026, 11:59 pm ET")
    _set_zone(page, live_server, "Central European Time (Rotterdam)")
    main = page.locator("main")
    expect(main).to_contain_text("16 Dec 2026, 05:59 CET (Rotterdam)")
    expect(main).not_to_contain_text("11:59 pm ET")
    page.reload(wait_until="domcontentloaded")
    expect(page.get_by_label("Times in:")).to_have_value("Europe/Amsterdam")
    expect(main).to_contain_text("16 Dec 2026, 05:59 CET (Rotterdam)")
    page.goto(live_server + "/programmes/princeton-mfin", wait_until="domcontentloaded")
    expect(main).to_contain_text("16 Dec 2026, 05:59 CET (Rotterdam)")
    expect(main).to_contain_text("15 Dec 2026, 11:59 pm ET")  # school-zone original
    expect(main).to_contain_text("19 Dec 2026, 16:00 CET (Rotterdam)")  # interview converted too


# ---- US-3 requirement, step, document -----------------------------------------------------------------


def test_us3_tick_requirement_and_step_and_set_document(page: Page, live_server: str) -> None:
    page.goto(live_server + "/programmes/princeton-mfin", wait_until="domcontentloaded")
    requirements = page.locator("#requirements")
    requirements.get_by_label("Resume / CV").click()
    expect(requirements).to_contain_text("1 / 9")
    steps = page.locator("#steps")
    steps.get_by_label("Register recommenders early").click()
    expect(steps).to_contain_text("1 / 7")
    documents = page.locator("#documents")
    documents.get_by_label("Status of GMAT official score report").select_option("sent")
    expect(documents.locator("tr", has_text="GMAT official score report")).to_contain_text("30 Oct 2026")
    page.reload(wait_until="domcontentloaded")
    expect(page.locator("#requirements").get_by_label("Resume / CV")).to_be_checked()
    expect(page.locator("#steps").get_by_label("Register recommenders early")).to_be_checked()
    expect(page.locator("#documents").get_by_label("Status of GMAT official score report")).to_have_value(
        "sent"
    )


# ---- US-4 add and edit a programme ---------------------------------------------------------------------


def test_us4_add_programme_by_form_then_edit(page: Page, live_server: str, data_dir: Path) -> None:
    page.goto(live_server + "/programmes", wait_until="domcontentloaded")
    page.get_by_role("link", name="Add programme").click()
    page.wait_for_url("**/programmes/new")
    page.get_by_label("Programme name").fill("Example Institute Master in Quantitative Finance")
    page.get_by_label("Short name").fill("Example MQF")
    page.get_by_label("School", exact=True).fill("Example Institute")
    page.get_by_label("Unit").fill("School of Finance")
    deadline = page.locator("fieldset#deadlines .row").first
    deadline.get_by_label("Label").fill("Application deadline")
    deadline.get_by_label("Date").fill("2027-02-01")
    deadline.get_by_label("Time", exact=True).fill("17:00")
    deadline.get_by_label("Time zone").fill("America/New_York")
    deadline.get_by_label("Source URL").fill("https://example.com/apply")
    page.get_by_role("button", name="Add requirement").click()
    page.locator("fieldset#requirements .row").last.get_by_label("Label").fill("Resume / CV")
    page.get_by_role("button", name="Save programme").click()
    page.wait_for_url("**/programmes/example-mqf")
    expect(page.get_by_role("heading", level=1)).to_have_text(
        "Example Institute Master in Quantitative Finance"
    )
    raw = YAML(typ="safe").load((data_dir / "programmes" / "example-mqf.yaml").read_text(encoding="utf-8"))
    assert raw["verification"] == "unverified" and raw["deadlines"][0]["verification"] == "unverified"
    assert raw["requirements"][0]["label"] == "Resume / CV"

    page.goto(live_server + "/", wait_until="domcontentloaded")
    row = page.locator("tr[data-programme='example-mqf']")
    expect(row).to_contain_text("Example MQF")
    expect(row).to_contain_text("1 Feb 2027, 5:00 pm ET")
    expect(row.locator(".badge-unverified")).to_contain_text("Unverified")

    page.goto(live_server + "/programmes/example-mqf", wait_until="domcontentloaded")
    page.get_by_role("link", name="Edit").click()
    page.wait_for_url("**/programmes/example-mqf/edit")
    page.get_by_label("Unit").fill("Graduate School of Finance")
    page.get_by_role("button", name="Save programme").click()
    page.wait_for_url("**/programmes/example-mqf")
    expect(page.locator("main")).to_contain_text("Graduate School of Finance")


def test_us4_form_error_is_shown_inline(page: Page, live_server: str, data_dir: Path) -> None:
    page.goto(live_server + "/programmes/new", wait_until="domcontentloaded")
    page.get_by_label("Programme name").fill("No Deadline Programme")
    page.get_by_label("Short name").fill("No Deadline")
    page.get_by_label("School", exact=True).fill("Example Institute")
    page.get_by_role("button", name="Save programme").click()
    expect(page.locator(".form-error")).to_contain_text("at least one deadline")
    assert not (data_dir / "programmes" / "no-deadline.yaml").exists()


# ---- US-5 verify and add an interview ------------------------------------------------------------------


def test_us5_mark_verified_and_add_interview(page: Page, live_server: str, data_dir: Path) -> None:
    page.goto(live_server + "/programmes/booth-mif", wait_until="domcontentloaded")
    page.get_by_role("button", name="Mark verified").click()
    expect(page.locator("main .badge-verified").first).to_contain_text("Verified")
    expect(page.locator("main .badge-unverified")).to_have_count(0)

    interviews = page.locator("#interviews")
    interviews.get_by_role("button", name="Add interview or assessment").click()
    form = interviews.locator("form.interview-form").last
    form.get_by_label("Kind").select_option("interview")
    form.get_by_label("Label").fill("Admissions interview")
    form.get_by_label("Date", exact=True).fill("2027-01-20")
    form.get_by_label("Time", exact=True).fill("14:30")
    form.get_by_label("Time zone").fill("America/Chicago")
    form.get_by_label("Status").select_option("scheduled")
    form.get_by_role("button", name="Save interview").click()
    expect(interviews).to_contain_text("Admissions interview")
    expect(interviews).to_contain_text("20 Jan 2027, 2:30 pm CT")

    page.reload(wait_until="domcontentloaded")
    expect(page.locator("main .badge-verified").first).to_contain_text("Verified")
    expect(page.locator("#interviews")).to_contain_text("20 Jan 2027, 2:30 pm CT")
    raw = YAML(typ="safe").load((data_dir / "programmes" / "booth-mif.yaml").read_text(encoding="utf-8"))
    assert raw["verification"] == "verified" and raw["verified_on"] is not None
    assert (
        raw["interviews"][0]["start_local"] == "2027-01-20T14:30"
        and raw["interviews"][0]["tz"] == "America/Chicago"
    )


def test_failed_toggle_shows_an_inline_message(page: Page, live_server: str, data_dir: Path) -> None:
    page.goto(live_server + "/programmes/booth-mif", wait_until="domcontentloaded")
    (data_dir / "programmes" / "booth-mif.yaml").write_text("id: booth-mif\nname: [broken", encoding="utf-8")
    page.locator("#requirements").get_by_label("Resume", exact=True).click()
    alert = page.locator("#action-error [role=alert]")
    expect(alert).to_contain_text("Nothing was saved")
    expect(alert).to_be_in_viewport()  # visible where the user is, not at the top of the page
    expect(page.locator("#requirements").get_by_label("Resume", exact=True)).not_to_be_checked()  # stored
    alert.get_by_role("button", name="Dismiss").click()
    expect(page.locator("#action-error")).to_be_empty()


def test_toast_clears_after_the_next_successful_action(page: Page, live_server: str, data_dir: Path) -> None:
    page.goto(live_server + "/programmes/princeton-mfin", wait_until="domcontentloaded")
    path = data_dir / "programmes" / "princeton-mfin.yaml"
    good = path.read_bytes()
    path.write_text("id: princeton-mfin\nname: [broken", encoding="utf-8")
    page.locator("#requirements").get_by_label("Resume / CV").click()
    expect(page.locator("#action-error [role=alert]")).to_contain_text("Nothing was saved")
    path.write_bytes(good)
    page.locator("#requirements").get_by_label("Transcripts").click()
    expect(page.locator("#requirements")).to_contain_text("1 / 9")
    expect(page.locator("#action-error")).to_be_empty()


def test_failed_save_in_a_card_is_shown_there_with_the_stored_state(
    page: Page, live_server: str, data_dir: Path
) -> None:
    import os
    import stat

    path = data_dir / "programmes" / "princeton-mfin.yaml"
    page.set_viewport_size({"width": 1280, "height": 720})
    page.goto(live_server + "/programmes/princeton-mfin", wait_until="domcontentloaded")
    os.chmod(path, stat.S_IREAD)
    try:
        select = page.get_by_label("Status of GMAT official score report")
        select.scroll_into_view_if_needed()
        select.select_option("sent")
        error = page.locator("#documents .card-error")
        expect(error).to_contain_text("Nothing was saved.")
        expect(error).to_be_in_viewport()
        expect(page.get_by_label("Status of GMAT official score report")).to_have_value("not_requested")
    finally:
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)


def test_focus_stays_on_document_status_after_change(page: Page, live_server: str) -> None:
    page.goto(live_server + "/programmes/princeton-mfin", wait_until="domcontentloaded")
    select = page.get_by_label("Status of GMAT official score report")
    select.focus()
    select.select_option("requested")
    expect(page.locator("#documents")).to_contain_text("30 Oct 2026")
    expect(page.get_by_label("Status of GMAT official score report")).to_be_focused()


def test_interview_form_keeps_typed_values_on_a_toast_failure(
    page: Page, live_server: str, data_dir: Path
) -> None:
    page.goto(live_server + "/programmes/booth-mif", wait_until="domcontentloaded")
    interviews = page.locator("#interviews")
    interviews.get_by_role("button", name="Add interview or assessment").click()
    form = interviews.locator("form.interview-form").last
    form.get_by_label("Label").fill("Admissions interview")
    form.get_by_label("Date", exact=True).fill("2027-01-20")
    form.get_by_label("Time", exact=True).fill("14:30")
    form.get_by_label("Time zone").fill("America/Chicago")
    (data_dir / "programmes" / "booth-mif.yaml").write_text("id: booth-mif\nname: [broken", encoding="utf-8")
    form.get_by_role("button", name="Save interview").click()
    expect(page.locator("#action-error [role=alert]")).to_contain_text("Nothing was saved")
    expect(form.get_by_label("Label")).to_have_value("Admissions interview")  # nothing typed is lost
    expect(form.get_by_label("Time zone")).to_have_value("America/Chicago")
