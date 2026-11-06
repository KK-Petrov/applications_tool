"""The eval runner (``tasks.py eval-email``) end to end against the fake Claude Code: it writes both reports,
grades every case, and never touches data_dir. The real run is live; this only checks the plumbing.
"""

from __future__ import annotations

import json
from pathlib import Path

from run import main

from helpers import tree_digest


def test_runner_writes_json_and_markdown_reports(config, data_dir: Path, tmp_path: Path) -> None:
    before = tree_digest(data_dir)
    out = tmp_path / "reports"
    code = main(["--out", str(out)], config=config)
    assert code in (0, 1)  # 1 = a gate failed (the fake replays one email for every case)
    data = json.loads((out / "eval_email.json").read_text(encoding="utf-8"))
    for key in (
        "schema_failures", "fact_failures", "form_failures", "unsupported_claims",
        "grader_calibration_passed", "subject_mismatches", "tuning", "held_out", "cases", "calibration",
        "skill_sha256", "generated_at",
    ):  # fmt: skip
        assert key in data, key
    assert len(data["cases"]) == 10 and len(data["calibration"]) == 3
    assert data["tuning"]["cases"] == 7 and data["held_out"]["cases"] == 3
    md = (out / "eval_email.md").read_text(encoding="utf-8")
    for n in range(1, 11):
        assert f"Case {n}" in md
    assert "Held-out cases (8–10)" in md
    assert tree_digest(data_dir) == before
