"""US-16 and US-18 (docs/03 §2), end to end in a real browser, with the fake Claude Code.

- US-16: "Research a programme" fills a new programme marked unverified with official sources.
- US-18: "Re-research" shows each field as new / changed / unchanged with its source; the user accepts or
  rejects each change; nothing is written until Save; accepted deadline changes reset those deadlines to
  unverified (C-2c-02 browser part: ``-k us18``).
Every research step is also checked with axe (C-1-05 keeps passing on the new pages).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import Page, expect
from ruamel.yaml import YAML

from hub.store import Store

from helpers import (
    EXAMPLE_DEADLINES_SRC,
    NO_OFFICIAL_SOURCE,
    mirror_output,
    research_output,
    sourced,
    write_recording,
)

pytestmark = pytest.mark.e2e

PID = "princeton-mfin"
BCF = "https://bcf.princeton.edu/academic-programs/master-in-finance/apply-for-the-master-in-finance-program"
AXE = Axe()


def _serious(page: Page) -> list[str]:
    results = AXE.run(page)
    return [
        f"{v['id']} ({v['impact']}): {[', '.join(n['target']) for n in v['nodes']][:5]}"
        for v in results.response["violations"]
        if v.get("impact") in {"serious", "critical"}
    ]


def _load(data_dir: Path, pid: str) -> dict:
    return YAML(typ="safe").load((data_dir / "programmes" / f"{pid}.yaml").read_text(encoding="utf-8"))


@pytest.fixture
def use_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def use(output: dict) -> None:
        monkeypatch.setenv("FAKE_CLAUDE_RECORDING", str(write_recording(tmp_path, output)))

    return use


# ---- US-16 ---------------------------------------------------------------------------------------------


def test_us16_research_a_new_programme(page: Page, live_server: str, data_dir: Path, use_output) -> None:
    use_output(research_output(ai_policy={"value": None, "source_url": None, "note": NO_OFFICIAL_SOURCE}))
    page.goto(live_server + "/", wait_until="domcontentloaded")
    page.get_by_label("Programme to research").fill("Example MSQF")
    page.get_by_role("button", name="Research a programme").click()
    # Step 1: the proposed official domains, for the user to confirm.
    domains = page.get_by_label("Official domains")
    expect(domains).to_have_value("example.edu")
    assert not _serious(page), _serious(page)
    assert not (data_dir / "programmes" / "example-msqf.yaml").exists()
    page.get_by_role("button", name="Check sources").click()
    # Step 2: the preview, with sources and the field without an official source highlighted.
    preview = page.locator("main")
    expect(preview).to_contain_text(EXAMPLE_DEADLINES_SRC)
    expect(preview.locator(".not-found").first).to_contain_text(NO_OFFICIAL_SOURCE)
    assert not _serious(page), _serious(page)
    assert not (data_dir / "programmes" / "example-msqf.yaml").exists()  # nothing written before Save
    page.get_by_role("button", name="Save programme").click()
    page.wait_for_url("**/programmes/example-msqf")
    expect(page.locator("main .badge-unverified").first).to_contain_text("Researched, not checked")
    raw = _load(data_dir, "example-msqf")
    assert raw["verification"] == "unverified" and raw["deadlines"][0]["verification"] == "unverified"
    page.goto(live_server + "/", wait_until="domcontentloaded")
    expect(page.locator("main")).to_contain_text("Example MSQF")


def test_us16_a_third_party_source_blocks_save(
    page: Page, live_server: str, data_dir: Path, use_output
) -> None:
    out = research_output()
    out["deadlines"][0]["source_url"] = "https://example-aggregator.com/example-msqf"
    use_output(out)
    page.goto(live_server + "/programmes/new", wait_until="domcontentloaded")
    page.get_by_label("Programme to research").fill("Example MSQF")
    page.get_by_role("button", name="Research a programme").click()
    page.get_by_role("button", name="Check sources").click()
    expect(page.get_by_role("alert")).to_contain_text(
        "Deadline 1 · Source URL: example-aggregator.com is not an official source"
    )
    expect(page.get_by_role("button", name="Save programme")).to_be_disabled()
    assert not _serious(page), _serious(page)
    assert not (data_dir / "programmes" / "example-msqf.yaml").exists()


# ---- US-18 ---------------------------------------------------------------------------------------------


def _changed_output(config) -> dict:
    """Research that finds an earlier Princeton deadline and two letters instead of three."""
    programme = next(p for p in Store.from_config(config).load_programmes()[0] if p.id == PID)
    out = mirror_output(programme, BCF)
    out["deadlines"][0]["due_local"] = "2026-12-01T23:59"
    out["letters_required"] = sourced(2, BCF)
    return out


def test_us18_reresearch_accept_one_reject_another(
    page: Page, live_server: str, config, data_dir: Path, use_output
) -> None:
    use_output(_changed_output(config))
    path = data_dir / "programmes" / f"{PID}.yaml"
    original = path.read_bytes()
    page.goto(f"{live_server}/programmes/{PID}", wait_until="domcontentloaded")
    page.get_by_role("button", name="Re-research").click()
    rows = page.locator("[data-change]")
    expect(page.locator('[data-change="deadline:main"]')).to_have_attribute("data-kind", "changed")
    expect(page.locator('[data-change="letters_required"]')).to_have_attribute("data-kind", "changed")
    expect(page.locator('[data-change="name"]')).to_have_attribute("data-kind", "unchanged")
    assert rows.count() > 5
    expect(page.locator('[data-change="letters_required"]')).to_contain_text(BCF)
    assert not _serious(page), _serious(page)
    assert path.read_bytes() == original  # nothing written before Save
    # Accept the letters change, reject (leave unticked) the deadline change.
    page.locator('[data-change="letters_required"]').get_by_role("checkbox").check()
    expect(page.locator('[data-change="deadline:main"]').get_by_role("checkbox")).not_to_be_checked()
    page.get_by_role("button", name="Save accepted changes").click()
    page.wait_for_url(f"**/programmes/{PID}")
    raw = _load(data_dir, PID)
    assert raw["letters_required"] == 2
    assert raw["deadlines"][0]["due_local"] == "2026-12-15T23:59"  # rejected: unchanged
    expect(page.locator("main .badge-unverified").first).to_contain_text("Researched, not checked")


def test_us18_accepted_deadline_change_resets_it_to_unverified(
    page: Page, live_server: str, config, data_dir: Path, use_output
) -> None:
    page.goto(f"{live_server}/programmes/{PID}", wait_until="domcontentloaded")
    page.get_by_role("button", name="Mark verified").click()
    expect(page.locator("main .badge-verified").first).to_contain_text("Verified")
    use_output(_changed_output(config))
    page.get_by_role("button", name="Re-research").click()
    page.locator('[data-change="deadline:main"]').get_by_role("checkbox").check()
    page.get_by_role("button", name="Save accepted changes").click()
    page.wait_for_url(f"**/programmes/{PID}")
    raw = _load(data_dir, PID)
    assert raw["deadlines"][0]["due_local"] == "2026-12-01T23:59"
    assert raw["deadlines"][0]["verification"] == "unverified"
    expect(page.locator("main .badge-unverified").first).to_contain_text("Deadline changed, not checked")


def test_us18_save_with_nothing_accepted_writes_nothing(
    page: Page, live_server: str, config, data_dir: Path, use_output
) -> None:
    use_output(_changed_output(config))
    path = data_dir / "programmes" / f"{PID}.yaml"
    original, mtime = path.read_bytes(), path.stat().st_mtime_ns
    page.goto(f"{live_server}/programmes/{PID}", wait_until="domcontentloaded")
    page.get_by_role("button", name="Re-research").click()
    page.get_by_role("button", name="Save accepted changes").click()
    expect(page.locator("main")).to_contain_text("No change was accepted, so nothing was saved.")
    assert path.read_bytes() == original and path.stat().st_mtime_ns == mtime


def test_us18_without_domains_confirms_them_first(
    page: Page, live_server: str, config, data_dir: Path, use_output
) -> None:
    path = data_dir / "programmes" / f"{PID}.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("official_domains:\n  - princeton.edu\n", ""),
        encoding="utf-8",
    )
    use_output(_changed_output(config))
    page.goto(f"{live_server}/programmes/{PID}", wait_until="domcontentloaded")
    page.get_by_role("button", name="Re-research").click()
    domains = page.get_by_label("Official domains")
    expect(domains).to_have_value("bcf.princeton.edu\ngradschool.princeton.edu")
    expect(page.locator("[data-change]")).to_have_count(0)
    assert not _serious(page), _serious(page)
    domains.fill("princeton.edu")
    page.get_by_role("button", name="Check sources").click()
    expect(page.locator('[data-change="official_domains"]')).to_have_attribute("data-kind", "new")
