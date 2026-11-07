"""The AI usage page, GET /usage (docs/03 §4.9; DECISIONS #60), without a browser.

A read-only table of every logged AI call (date and time in the display zone, action, what it was for with
names resolved and linked, duration, estimated cost, result), totals for this month and all time, month and
source filters, and the empty, filtered-empty, unreadable-line and not-yet-imported states. The logs are the
invented ones in tests/fixtures/usage/.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from hub.ai import usage
from hub.store import Store

from helpers import FormClient, data_snapshot
from paths import FIXTURES

COST_NOTE = (
    "Estimated API-equivalent cost. On your subscription this draws from your monthly Agent SDK credit "
    "and isn't billed separately unless usage credits are enabled."
)
USAGE_FIXTURES = FIXTURES / "usage"


@pytest.fixture
def logs(config) -> None:
    app = usage.app_log_path(config)
    app.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(USAGE_FIXTURES / "app.jsonl", app)
    shutil.copyfile(USAGE_FIXTURES / "run.jsonl", usage.run_log_path(config))


def _rows(html: str) -> list[str]:
    """The table body rows of the calls table, as HTML snippets."""
    body = html.split('id="usage-calls"', 1)[1].split("</tbody>", 1)[0]
    return re.findall(r"<tr[^>]*>(.*?)</tr>", body, flags=re.S)


def _text(snippet: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", snippet).split())


def _clean(html: str) -> None:
    assert "None" not in html and "NaN" not in html and "Traceback" not in html


# ---- states -----------------------------------------------------------------------------------------


def test_empty_state(client: FormClient) -> None:
    res = client.get("/usage")
    assert res.status_code == 200
    html = res.text
    assert "<h1>AI usage</h1>" in html
    assert "No AI calls logged yet." in html
    assert 'id="usage-calls"' not in html
    assert 'href="/usage" aria-current="page"' in html
    _clean(html)


def test_populated_page_shows_app_calls_newest_first(client: FormClient, logs: None) -> None:
    html = client.get("/usage").text
    rows = [_text(r) for r in _rows(html)]
    assert len(rows) == 4  # default source: the app (imported, eval and live rows hidden)
    assert rows[0].startswith("29 Oct 2026, 2:00 pm ET")
    assert rows[-1].startswith("15 Sep 2026, 10:00 am ET")
    first = rows[0]
    assert "Generate email" in first and "Prof. Sam Whitfield" in first
    assert "Princeton MFin" in first and "MIT Sloan MFin" in first
    assert "120.3 s" in first and "not reported" in first and "Error: timed out" in first
    assert "Done" in rows[1] and "$0.033" in rows[1]
    _clean(html)


def test_names_are_resolved_and_linked_and_a_deleted_item_shows_its_id(
    client: FormClient, logs: None
) -> None:
    html = client.get("/usage").text
    rows = _rows(html)
    assert '<a href="/recommenders?selected=whitfield">Prof. Sam Whitfield</a>' in rows[0]
    assert '<a href="/programmes/princeton-mfin">Princeton MFin</a>' in rows[0]
    deleted = rows[1]
    assert "old-prof" in deleted and "no longer in your data" in deleted
    assert "selected=old-prof" not in deleted


def test_eval_rows_show_ids_and_are_never_linked(client: FormClient, logs: None) -> None:
    html = client.get("/usage?source=eval").text
    rows = _rows(html)
    assert len(rows) == 2
    assert all("smit" in r and "princeton-mfin" in r and "<a " not in r for r in rows)
    assert "Grade claims (eval)" in _text(rows[0])


def test_source_filter_all_and_live_and_unknown(client: FormClient, logs: None) -> None:
    assert len(_rows(client.get("/usage?source=all").text)) == 8
    live = [_text(r) for r in _rows(client.get("/usage?source=live").text)]
    assert len(live) == 1 and "Live check" in live[0] and "Skill and lockdown checks" in live[0]
    unknown = [_text(r) for r in _rows(client.get("/usage?source=unknown").text)]
    assert len(unknown) == 1 and "Unlabelled" in unknown[0] and "Not recorded" in unknown[0]


def test_month_filter_uses_the_display_zone(client: FormClient, logs: None, config) -> None:
    september = [_text(r) for r in _rows(client.get("/usage?month=2026-09").text)]
    assert len(september) == 2  # 1 Oct 02:00 UTC is 30 Sep 10:00 pm in New York
    assert september[0].startswith("30 Sep 2026, 10:00 pm ET")
    store = Store.from_config(config)
    settings = store.load_settings()
    settings.display_tz = "Europe/Amsterdam"
    store.save_settings(settings)
    september = [_text(r) for r in _rows(client.get("/usage?month=2026-09").text)]
    assert len(september) == 1
    october = [_text(r) for r in _rows(client.get("/usage?month=2026-10").text)]
    assert october[-1].startswith("1 Oct 2026, 04:00 CEST (Rotterdam)")


def test_filters_combine_and_a_filtered_empty_page_says_so(client: FormClient, logs: None) -> None:
    assert len(_rows(client.get("/usage?month=2026-10&source=all").text)) == 5
    html = client.get("/usage?month=2026-01&source=app").text
    assert "No calls match these filters." in html
    assert 'id="usage-calls"' not in html


@pytest.mark.parametrize("query", ["?month=2026-13", "?month=abc", "?source=dev", "?month=&source="])
def test_a_bad_filter_value_falls_back_to_the_default(client: FormClient, logs: None, query: str) -> None:
    res = client.get("/usage" + query)
    assert res.status_code == 200
    assert len(_rows(res.text)) == 4


def test_filter_form_is_a_labelled_get_form(client: FormClient, logs: None) -> None:
    html = client.get("/usage?month=2026-10&source=eval").text
    form = html.split('id="usage-filters"', 1)[1].split("</form>", 1)[0]
    assert 'method="get"' in html.split('id="usage-filters"', 1)[0][-200:]
    assert '<label for="usage-month">' in form and '<label for="usage-source">' in form
    assert '<option value="2026-10" selected>October 2026</option>' in form
    assert '<option value="eval" selected>' in form
    assert "Apply" in form


def test_totals_this_month_and_all_time(client: FormClient, logs: None) -> None:
    html = client.get("/usage").text
    by_source = _text(html.split('id="usage-by-source"', 1)[1].split("</table>", 1)[0])
    # three decimals, so the source rows add up to "All sources" as shown (data-integrity review W4)
    assert "App 2 calls $0.033 4 calls $0.126" in by_source
    assert "Eval 2 calls $0.040 2 calls $0.040" in by_source
    assert "Live 1 call $0.064 1 call $0.064" in by_source
    assert "Unknown (before the audit log) 0 calls $0.000 1 call $0.020" in by_source
    assert "All sources 5 calls $0.137 8 calls $0.250" in by_source
    by_action = _text(html.split('id="usage-by-action"', 1)[1].split("</table>", 1)[0])
    assert "By action · App" in by_action
    assert "Generate email 2 calls $0.033 4 calls $0.126" in by_action  # follows the source filter (app)
    assert "Grade claims" not in by_action
    every = _text(
        client.get("/usage?source=all").text.split('id="usage-by-action"', 1)[1].split("</table>")[0]
    )
    assert "Generate email 3 calls $0.059 5 calls $0.152" in every
    assert "1 error" in by_source and "1 call reported no cost" in html


def test_the_cost_note_is_shown_verbatim(client: FormClient, logs: None) -> None:
    assert COST_NOTE in client.get("/usage").text.replace("&#39;", "'")


def test_an_unreadable_line_shows_a_banner_and_is_left_out(client: FormClient, logs: None, config) -> None:
    path = usage.app_log_path(config)
    path.write_text(path.read_text(encoding="utf-8") + "not json\n", encoding="utf-8")
    html = client.get("/usage").text
    banner = html.split('class="banner" role="alert"', 1)[1].split("</div>", 1)[0]
    assert "logs/ai_usage.jsonl" in banner and "line 6" in banner
    assert "left out of the list and the totals" in banner
    assert len(_rows(html)) == 4
    _clean(html)


def test_an_unreadable_file_shows_a_banner(client: FormClient, logs: None, config) -> None:
    path = usage.app_log_path(config)
    path.unlink()
    path.mkdir()
    res = client.get("/usage?source=all")
    assert res.status_code == 200
    assert "could not be read" in res.text
    assert len(_rows(res.text)) == 3


def test_old_lines_not_yet_imported_show_a_hint(client: FormClient, config) -> None:
    shutil.copyfile(USAGE_FIXTURES / "run.jsonl", usage.run_log_path(config))
    html = client.get("/usage").text
    assert "1 earlier call in the old run log" in html
    assert "uv run hub import-usage" in html
    usage.import_legacy(config)
    assert "uv run hub import-usage" not in client.get("/usage").text


def test_the_page_writes_nothing_and_has_no_write_route(
    client: FormClient, logs: None, data_dir: Path
) -> None:
    before = data_snapshot(data_dir)
    for query in ("", "?source=all", "?month=2026-10"):
        client.get("/usage" + query)
    assert data_snapshot(data_dir) == before
    assert client.post("/usage", data={"x": "1"}).status_code == 405


# ---- states found by the ui and data-integrity reviews (DECISIONS #60) --------------------------------


def test_by_source_lists_every_source_even_without_calls(client: FormClient, config) -> None:
    """Only eval/live lines (likely before the first app click): App still has a row, with zeros."""
    shutil.copyfile(USAGE_FIXTURES / "run.jsonl", usage.run_log_path(config))
    html = client.get("/usage").text
    by_source = _text(html.split('id="usage-by-source"', 1)[1].split("</table>", 1)[0])
    for label in ("App 0 calls $0.000 0 calls $0.000 none", "Eval 2 calls", "Live 1 call", "Unknown (before"):
        assert label in by_source, label
    by_action = _text(html.split('id="usage-by-action"', 1)[1].split("</table>", 1)[0])
    assert "No calls from this source yet." in by_action
    assert "No app calls yet. Choose Eval, Live or All sources under Source to see the others." in html
    assert "No calls match these filters." not in html


def test_a_log_that_is_all_unreadable_does_not_claim_the_rest_loaded(client: FormClient, config) -> None:
    path = usage.app_log_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json\n[1]\n", encoding="utf-8")
    html = client.get("/usage").text
    assert "2 lines of the AI usage log could not be read" in html
    assert "The rest of the log loaded normally." not in html
    assert "No readable AI calls." in html and "No AI calls logged yet." not in html


def test_essay_and_cv_subjects_show_ids_and_the_deleted_marker(client: FormClient, config) -> None:
    """Not emitted until Phase 4b; the page already marks IDs and deleted programmes (ui review WARN 5)."""
    path = usage.app_log_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    base = {
        "at": "2026-10-20T15:00:00+00:00",
        "skill": "evaluate",
        "backend": "cli",
        "source": "app",
        "duration_s": 30.0,
        "total_cost_usd": 0.05,
        "ok": True,
        "error": None,
    }
    essay = {"programme_id": "princeton-mfin", "essay_id": "why-us"}
    lines = [
        {**base, "action": "evaluate_cv", "subject": {"cv_id": "gone-programme"}},
        {**base, "action": "evaluate_cv", "subject": {"cv_id": "princeton-mfin"}},
        {**base, "action": "evaluate_essay", "subject": essay},
    ]
    path.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    rows = _rows(client.get("/usage").text)
    gone = (
        'CV for <span class="mono">gone-programme</span> <span class="note">(no longer in your data)</span>'
    )
    assert any(gone in r for r in rows)
    assert any('CV for <a href="/programmes/princeton-mfin">Princeton MFin</a>' in r for r in rows)
    assert any('essay <span class="mono">why-us</span>' in r for r in rows)


def test_a_newline_in_the_month_falls_back(client: FormClient, logs: None) -> None:
    html = client.get("/usage", params={"month": "2026-10\n"}).text
    assert len(_rows(html)) == 4
    assert "2026-10\n" not in html
