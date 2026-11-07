"""C-2c-03 (live): course import on the invented 28-course fixture transcript, in all three formats.

    uv run python tasks.py live -k courses

For each format (pasted text, .docx, text PDF) the app's own pipeline runs: text extraction → identifiers
stripped in Python → a real ``/extract-courses`` call → every field stripped again. ``course_graders``
then checks every course's exact name, grade and credits, 0 false grade flags (and no wrong grade let
through), the planted swapped grade flagged, and no identifier in the output. Nothing is written until the
rows are accepted; then the accepted rows are written to the test's temporary data_dir exactly as Accept all
does (never the owner's data_dir), every file there is scanned for identifiers, and the lecturers of the
stored courses are listed: each once, with all their courses. Evidence: ``reports/live_courses.json``.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import course_graders
import pytest

from hub.ai.skills import load_schema, run_skill
from hub.config import load_config
from hub.courses import extract, identifiers, importer
from hub.store import Store

from paths import REPO_ROOT

pytestmark = pytest.mark.live

HERE = Path(__file__).resolve().parent
EXPECTED = json.loads((HERE / "expected.json").read_text(encoding="utf-8"))
FORMATS = {"txt": "transcript.txt", "docx": "transcript.docx", "pdf": "transcript.pdf"}
REPORT = REPO_ROOT / "reports" / "live_courses.json"


def _record(fmt: str, entry: dict) -> None:
    REPORT.parent.mkdir(exist_ok=True)
    data = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.exists() else {}
    data[fmt] = entry
    REPORT.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


@pytest.mark.parametrize("fmt", list(FORMATS))
def test_courses_live_import(fmt: str, tmp_path: Path) -> None:
    config = load_config()
    data_dir = config.storage.data_dir
    assert tmp_path.resolve() in data_dir.resolve().parents, "live tests must use a temporary data_dir"
    path = HERE / FORMATS[fmt]
    if fmt == "txt":
        text = extract.text_from_paste(path.read_text(encoding="utf-8"))
        raw = path.read_bytes()
    else:
        raw = path.read_bytes()
        text = extract.extract_text(path.name, raw, config.courses)
    stripped = identifiers.strip(text, config.courses)
    sent_identifiers = course_graders.identifiers_in(stripped, EXPECTED["identifiers"])
    result = run_skill(
        "extract-courses",
        importer.payload(stripped),
        load_schema("extract_courses"),
        config=config,
        action="import_courses",
        subject={},
        source="live",
    )
    rows = [importer.strip_row(r, config.courses) for r in result.data["courses"]]
    report = course_graders.grade(rows, stripped, EXPECTED)
    report["identifiers_sent"] = sent_identifiers
    written_before_accept = sorted(str(p.relative_to(data_dir)) for p in data_dir.rglob("*") if p.is_file())
    # Accept all, as the app does: only rows without a problem.
    store = Store.from_config(config)
    source = importer.source_name(path.name, date.today().isoformat(), store.source_names())
    store.create_source_text(source, importer.source_header(path.name, importer.sha256(raw)) + stripped)
    taken: set[str] = set()
    accepted = [r for r in rows if not importer.row_problems(r, stripped, [])]
    store.add_courses([importer.to_course(r, importer.course_id(r, taken), source) for r in accepted])
    stored = store.load_courses().courses
    report |= course_graders.lecturers_graded(stored, EXPECTED, config.names, config.courses)
    report["accepted"] = len(accepted)
    report["identifiers_in_data_dir"] = sorted(
        {
            v
            for p in data_dir.rglob("*")
            if p.is_file()
            for v in course_graders.identifiers_in(
                p.read_text(encoding="utf-8", errors="replace"), EXPECTED["identifiers"]
            )
        }
    )
    report["written_before_accept"] = written_before_accept
    report["cost_usd"], report["duration_s"] = result.cost_usd, result.duration_s
    report["warnings"] = result.data.get("warnings", [])
    report["passed"] = course_graders.passed(report) and not written_before_accept and not sent_identifiers
    _record(fmt, report)
    assert sent_identifiers == [], sent_identifiers
    assert written_before_accept == [], written_before_accept
    assert course_graders.passed(report), {
        k: v for k, v in report.items() if v and k not in ("rows", "warnings")
    }
    assert report["accepted"] == 28
