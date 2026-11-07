"""Re-research "Accept all changes" / "Clear all" in a real browser, with and without JS (DECISIONS #68).

With JS the buttons tick or untick the boxes in place (no request, nothing saved) and the count follows every
tick; without JS they submit to /research/select, which re-renders the comparison with those rows ticked and
writes nothing. Never ticked: a refused row, a "Not found by research, kept" row, the official domains row.
axe on the comparison after Accept all. Names avoid "us18" (C-2c-02's ``-k us18`` selection is unchanged).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import Browser, Page, expect
from ruamel.yaml import YAML

from hub.store import Store

from helpers import mirror_output, sourced, write_recording

pytestmark = pytest.mark.e2e

PID = "princeton-mfin"
BCF = "https://bcf.princeton.edu/academic-programs/master-in-finance/apply-for-the-master-in-finance-program"
AGGREGATOR = "https://example-aggregator.com/princeton"
AXE = Axe()


def _serious(page: Page) -> list[str]:
    results = AXE.run(page)
    return [
        f"{v['id']} ({v['impact']}): {[', '.join(n['target']) for n in v['nodes']][:5]}"
        for v in results.response["violations"]
        if v.get("impact") in {"serious", "critical"}
    ]


def _output(config) -> dict:
    programme = next(p for p in Store.from_config(config).load_programmes()[0] if p.id == PID)
    out = mirror_output(programme, BCF)
    out["deadlines"][0]["due_local"] = "2026-12-01T23:59"
    out["letters_required"] = sourced(2, AGGREGATOR)  # refused
    out["requirements"].append({"id": None, "label": "Video essay", "note": None, "source_url": BCF})
    out["requirements"].pop(0)  # a stored requirement research didn't find: "kept"
    return out


@pytest.fixture
def setup(config, data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Princeton without official_domains (so the comparison has a domains row), the output above, and a
    log of every fake Claude call."""
    path = data_dir / "programmes" / f"{PID}.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("official_domains:\n  - princeton.edu\n", ""),
        encoding="utf-8",
    )
    monkeypatch.setenv("FAKE_CLAUDE_RECORDING", str(write_recording(tmp_path, _output(config))))
    calls = tmp_path / "fake-calls.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(calls))
    return {"path": path, "calls": calls}


def _compare(page: Page, live_server: str) -> None:
    page.goto(f"{live_server}/programmes/{PID}", wait_until="domcontentloaded")
    page.get_by_role("button", name="Re-research").click()
    page.get_by_label("Official domains").fill("princeton.edu")
    page.get_by_role("button", name="Check sources").click()
    expect(page.locator('[data-change="official_domains"]')).to_have_attribute("data-kind", "new")


def _box(page: Page, change: str):
    return page.locator(f'[data-change="{change}"]').get_by_role("checkbox")


def _call_count(calls: Path) -> int:
    return len(calls.read_text(encoding="utf-8").splitlines()) if calls.exists() else 0


def test_accept_all_ticks_in_place_and_the_count_follows(
    page: Page, live_server: str, data_dir: Path, setup: dict
) -> None:
    _compare(page, live_server)
    original, calls = setup["path"].read_bytes(), _call_count(setup["calls"])
    boxes = page.locator("#research-apply input[name=accept]")
    total = boxes.count()
    expect(page.locator("#research-apply input[name=accept]:checked")).to_have_count(0)  # start unticked
    count = page.locator("#selection-count")
    expect(count).to_have_text(f"0 of {total + 1} changes selected · 1 refused")  # + the refused row
    page.evaluate("window.__stay = 1")
    page.get_by_role("button", name="Accept all changes").click()
    assert page.evaluate("window.__stay") == 1  # no navigation: ticked in place
    expect(_box(page, "deadline:main")).to_be_checked()
    expect(page.locator('[data-change="letters_required"] input')).to_have_count(0)  # refused: no box
    expect(_box(page, "official_domains")).not_to_be_checked()
    expect(count).to_have_text(f"{total - 1} of {total + 1} changes selected · 1 refused")
    expect(page.locator("#live-status")).to_contain_text(f"{total - 1} of {total + 1} changes selected")
    assert not _serious(page), _serious(page)
    _box(page, "deadline:main").uncheck()  # the user can untick before saving
    expect(count).to_have_text(f"{total - 2} of {total + 1} changes selected · 1 refused")
    assert setup["path"].read_bytes() == original and _call_count(setup["calls"]) == calls  # nothing yet
    page.get_by_role("button", name="Save accepted changes").click()
    page.wait_for_url(f"**/programmes/{PID}")
    stored = YAML(typ="safe").load(setup["path"].read_text(encoding="utf-8"))
    assert "Video essay" in [r["label"] for r in stored["requirements"]]
    assert stored["deadlines"][0]["due_local"] == "2026-12-15T23:59"  # unticked: unchanged
    assert stored["letters_required"] == 3  # refused
    assert "official_domains" not in stored  # never ticked by Accept all


def test_clear_all_unticks_every_box(page: Page, live_server: str, setup: dict) -> None:
    _compare(page, live_server)
    _box(page, "official_domains").check()
    page.get_by_role("button", name="Accept all changes").click()
    expect(_box(page, "official_domains")).to_be_checked()  # the user's own tick stays
    page.get_by_role("button", name="Clear all").click()
    expect(page.locator("#research-apply input[name=accept]:checked")).to_have_count(0)
    expect(page.locator("#selection-count")).to_contain_text("0 of ")


def test_without_js_accept_all_rerenders_ticked_and_writes_nothing(
    browser: Browser, live_server: str, setup: dict
) -> None:
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        _compare(page, live_server)
        original, calls = setup["path"].read_bytes(), _call_count(setup["calls"])
        page.get_by_role("button", name="Accept all changes").click()
        page.wait_for_url(f"**/programmes/{PID}/research/select")
        expect(_box(page, "deadline:main")).to_be_checked()
        expect(_box(page, "official_domains")).not_to_be_checked()
        expect(page.locator('[data-change="letters_required"] input')).to_have_count(0)
        expect(page.locator("#selection-count")).to_contain_text("changes selected · 1 refused")
        assert setup["path"].read_bytes() == original and _call_count(setup["calls"]) == calls
        page.get_by_role("button", name="Clear all").click()
        expect(page.locator("#research-apply input[name=accept]:checked")).to_have_count(0)
        assert setup["path"].read_bytes() == original
        page.get_by_role("button", name="Accept all changes").click()
        page.get_by_role("button", name="Save accepted changes").click()
        page.wait_for_url(f"**/programmes/{PID}")
        stored = YAML(typ="safe").load(setup["path"].read_text(encoding="utf-8"))
        assert stored["deadlines"][0]["due_local"] == "2026-12-01T23:59"
        assert stored["deadlines"][0]["verification"] == "unverified"
    finally:
        context.close()
