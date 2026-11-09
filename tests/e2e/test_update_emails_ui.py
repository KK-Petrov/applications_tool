"""Update emails, end to end (DECISIONS #104; docs/03 §4.3): from the Recommenders page actions, paste or
upload names and emails, read the preview, decide a different email, save; with and without JavaScript at
390 px; axe on every state."""

from __future__ import annotations

from pathlib import Path

import pytest
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import Browser, Page, expect

pytestmark = pytest.mark.e2e
AXE = Axe()
ODEGAARD = (
    "# Test fixture: invented professor.\n"
    "id: odegaard\nname: Dr. L. Ødegaard\nuniversity: Example\nletters: {}\n"
)
PASTE = (
    "name\temail\n"
    "L. Odegaard\tl.odegaard@example.org\n"  # fills an empty email (ticked)
    "Prof. Eva Jansen\teva.jansen@example.org\n"  # differs from the stored one: Keep or Use
    "Q. Nobody\tq.nobody@example.org\n"  # no match: listed
)


def _serious(page: Page) -> list[str]:
    results = AXE.run(page)
    return [
        f"{v['id']} ({v['impact']}): {[', '.join(n['target']) for n in v['nodes']][:5]}"
        for v in results.response["violations"]
        if v.get("impact") in {"serious", "critical"}
    ]


@pytest.fixture
def odegaard(data_dir: Path) -> Path:
    path = data_dir / "professors" / "odegaard.yaml"
    path.write_text(ODEGAARD, encoding="utf-8", newline="\n")
    return path


@pytest.mark.parametrize("js", [True, False])
def test_update_emails_by_pasting(
    browser: Browser, live_server: str, data_dir: Path, odegaard: Path, js: bool
) -> None:
    context = browser.new_context(java_script_enabled=js, viewport={"width": 390, "height": 844})
    try:
        page = context.new_page()
        page.goto(f"{live_server}/recommenders")
        page.get_by_role("link", name="Update emails").click()
        expect(page.get_by_role("heading", name="Update emails")).to_be_visible()
        page.get_by_label("Or paste the two columns").fill(PASTE)
        page.get_by_role("button", name="Preview").click()
        expect(page.get_by_text("1 empty email to fill, 1 different email to decide")).to_be_visible()
        expect(page.get_by_role("checkbox", name="Fill the email of Dr. L. Ødegaard")).to_be_checked()
        keep, use = page.get_by_role("radio", name="Keep"), page.get_by_role("radio", name="Use")
        expect(keep).not_to_be_checked()
        expect(use).not_to_be_checked()
        expect(page.locator("#not-used")).to_contain_text(
            "Row 4 (Q. Nobody): No professor matches this name."
        )
        assert page.evaluate("document.documentElement.scrollWidth") <= 390
        if js:
            assert _serious(page) == []
        page.get_by_role("button", name="Save emails").click()  # the conflict is undecided
        expect(page.get_by_role("alert")).to_contain_text("Pick Keep or Use for row 3 (Prof. Eva Jansen).")
        assert "l.odegaard" not in odegaard.read_text(encoding="utf-8")
        if js:
            assert _serious(page) == []
        page.get_by_role("radio", name="Use").check()
        page.get_by_role("button", name="Save emails").click()
        expect(page.get_by_text("Updated the email of 2 professors.")).to_be_visible()
        assert "email: l.odegaard@example.org" in odegaard.read_text(encoding="utf-8")
        assert "eva.jansen@example.org" in (data_dir / "professors" / "jansen.yaml").read_text(
            encoding="utf-8"
        )
    finally:
        context.close()


def test_update_emails_from_a_csv_file(
    browser: Browser, live_server: str, data_dir: Path, odegaard: Path
) -> None:
    csv = Path(data_dir.parent / "emails.csv")
    csv.write_bytes("﻿name;email\r\nDr. L. Ødegaard;l.odegaard@example.org\r\n".encode())
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    try:
        page = context.new_page()
        page.goto(f"{live_server}/recommenders/emails")
        page.get_by_label("CSV file").set_input_files(str(csv))
        page.get_by_role("button", name="Preview").click()
        expect(page.get_by_text("The first row is a header and was skipped.")).to_be_visible()
        page.get_by_role("button", name="Save emails").click()
        expect(page.get_by_text("Updated the email of 1 professor.")).to_be_visible()
        assert _serious(page) == []
    finally:
        context.close()
