"""C-1-01 support: no template prints a deadline or interview time except through the provenance macros.

Every date-time reaches the page through ``_time.html`` (``deadline``/``interview_time`` macros, which
print the zone and, for deadlines, the source link) or the ``when`` filter. A template that writes
``{{ d.due_local }}`` or formats a datetime itself would bypass the zone and the source.
"""

from __future__ import annotations

import re

from paths import REPO_ROOT

TEMPLATES = REPO_ROOT / "src" / "hub" / "templates"
PROVENANCE = TEMPLATES / "_time.html"
RAW_TIME = re.compile(r"\{\{[^}]*\b(due_local|start_local|strftime|isoformat|\.utc)\b[^}]*\}\}")
FORM_VALUE_OK = re.compile(r"\|\s*(form_date|form_time)\b")  # edit forms pre-fill raw wall-clock values


def test_provenance_macros_exist() -> None:
    text = PROVENANCE.read_text(encoding="utf-8")
    assert "{% macro deadline(" in text and "{% macro interview_time(" in text
    assert "source_url" in text


def test_no_template_prints_raw_times() -> None:
    offenders = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        if path == PROVENANCE:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in RAW_TIME.finditer(line):
                if not FORM_VALUE_OK.search(match.group(0)):
                    offenders.append(f"{path.relative_to(TEMPLATES)}:{n}: {match.group(0)}")
    assert not offenders, "times printed without the provenance macros:\n" + "\n".join(offenders)
