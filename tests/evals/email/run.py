"""Run the email eval (``uv run python tasks.py eval-email``): live calls on the owner's subscription.

For each of the 10 cases: build the payload with the app's code, run ``/rec-email`` once, grade schema,
facts and form in Python, then ask ``/grade-claims`` for unsupported claims. The 3 calibration emails go
to ``/grade-claims`` only. Writes ``reports/eval_email.json`` (metrics for C-2b-01 to C-2b-04, overall and
split into tuning cases 1–7 and held-out cases 8–10) and ``reports/eval_email.md`` (every email, for the
human check C-2b-06). Nothing is written to data_dir. Exit code 0 when every gate passes, else 1.

Options: ``--out DIR`` (default reports/), ``--cases 1,2,3`` (a subset, for quick tuning runs).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evalset import Built, CalibrationItem, Case, build_payload, load_calibration, load_cases
from graders import CaseGrade, calibration_flagged, gates_pass, grade_output, summarise

from hub.ai.skills import SkillError, load_schema, run_skill, skill_file
from hub.config import REPO_ROOT, Config, load_config


def _sha(skill: str) -> str:
    return hashlib.sha256(skill_file(skill).read_bytes()).hexdigest()


def _claims(
    text: str, facts: dict[str, Any], config: Config
) -> tuple[list[dict[str, str]], str | None, float]:
    try:
        result = run_skill(
            "grade-claims", {"text": text, "facts": facts}, load_schema("grade_claims"), config=config
        )
    except SkillError as err:
        return [], err.kind, 0.0
    return result.data["unsupported_claims"], None, result.cost_usd or 0.0


def _run_case(case: Case, config: Config) -> dict[str, Any]:
    built: Built = build_payload(case, config)
    output: dict[str, Any] | None = None
    error = None
    cost = 0.0
    duration = None
    try:
        result = run_skill("rec-email", built.payload, load_schema("rec_email"), config=config)
        output, cost, duration = result.data, result.cost_usd or 0.0, result.duration_s
    except SkillError as err:
        error = f"{err.kind}: {err.message}"
    grade = grade_output(
        f"{case.number:02d}",
        case.held_out,
        output,
        error,
        built.payload,
        built.unselected,
        built.anticipated,
        load_schema("rec_email"),
    )
    if grade.schema_ok and output is not None:
        claims, claims_error, claims_cost = _claims(output["body"], built.payload, config)
        grade.unsupported, grade.claims_error = claims, claims_error
        cost += claims_cost
    return {
        "case": case,
        "built": built,
        "output": output,
        "grade": grade,
        "cost": cost,
        "duration": duration,
    }


def _run_calibration(item: CalibrationItem, config: Config) -> dict[str, Any]:
    built = build_payload(item.case, config)
    claims, error, cost = _claims(item.body, built.payload, config)
    return {
        "item": item,
        "claims": claims,
        "error": error,
        "flagged": error is None and calibration_flagged(item.planted, claims),
        "cost": cost,
    }


def _md_case(r: dict[str, Any]) -> list[str]:
    case: Case = r["case"]
    grade: CaseGrade = r["grade"]
    p = r["built"].payload
    prof = p["professor"]
    course = prof["courses"][0]
    lines = [
        f"### Case {case.number}: {case.path.stem}",
        "",
        f"- Professor: {prof['name']} ({prof['title']} {prof['surname']}), {prof['university']}; "
        f"tone {prof['tone']}",
        f"- Course: {course['name']}; grade {course['grade'] or '—'}; term {course['term'] or '—'}; "
        f"interaction {'yes' if prof['interaction'] else 'no'}",
        "- Programmes: " + "; ".join(f"{x['short_name']} ({x['deadline_text']})" for x in p["programmes"]),
        f"- Biography ids: {', '.join(e['id'] for e in p['biography'])}",
        f"- Grades: schema {'ok' if grade.schema_ok else 'FAIL: ' + str(grade.schema_error)}; "
        f"words {grade.words}; facts {'ok' if not grade.fact_problems else 'FAIL'}; "
        f"form {'ok' if not grade.form_problems else 'FAIL'}; "
        f"subject {'ok' if grade.subject_ok else 'differs'}; "
        f"unsupported claims {len(grade.unsupported)}"
        + (f" (claim grader failed: {grade.claims_error})" if grade.claims_error else ""),
    ]
    for problem in [*grade.fact_problems, *grade.form_problems]:
        lines.append(f"  - {problem}")
    for claim in grade.unsupported:
        lines.append(f"  - Unsupported: “{claim['quote']}” ({claim['reason']})")
    out = r["output"]
    if out:
        lines += ["", f"**Subject:** {out['subject']}", "", "```text", out["body"], "```", ""]
        lines.append("Model warnings: " + ("; ".join(out["warnings"]) if out["warnings"] else "none"))
        lines.append(f"Used biography: {', '.join(out['used_biography_ids']) or 'none'}")
    lines.append("")
    return lines


def _markdown(summary: dict[str, Any], cases: list[dict[str, Any]], calibration: list[dict[str, Any]]) -> str:
    def row(name: str, key: str) -> str:
        tuning, held = summary["tuning"].get(key, ""), summary["held_out"].get(key, "")
        return f"| {name} | {summary[key]} | {tuning} | {held} |"

    lines = [
        "# Email eval (C-2b-01 to C-2b-04)",
        "",
        f"Generated {summary['generated_at']} · rec-email SKILL.md sha256 "
        f"{summary['skill_sha256']['rec-email'][:12]}"
        f" · total cost ${summary['total_cost_usd']:.2f}",
        "",
        "| Metric | All | Tuning (1–7) | Held out (8–10) |",
        "|---|---|---|---|",
        row("schema_failures", "schema_failures"),
        row("fact_failures", "fact_failures"),
        row("form_failures", "form_failures"),
        row("unsupported_claims", "unsupported_claims"),
        row("subject_mismatches (reported)", "subject_mismatches"),
        f"| grader_calibration_passed | {summary['grader_calibration_passed']} | | |",
        f"| grader_errors | {summary['grader_errors']} | | |",
        "",
        f"All gates pass: **{summary['gates_pass']}**",
        "",
        "## Tuning cases (1–7)",
        "",
    ]
    for r in cases:
        if not r["case"].held_out:
            lines += _md_case(r)
    lines += ["## Held-out cases (8–10)", ""]
    for r in cases:
        if r["case"].held_out:
            lines += _md_case(r)
    lines += ["## Claim grader calibration", ""]
    for c in calibration:
        item: CalibrationItem = c["item"]
        lines.append(
            f"- Calibration {item.number} (case {item.case.number}): planted “{item.planted}” → "
            f"{'flagged' if c['flagged'] else 'NOT flagged'}"
            + (f" (grader failed: {c['error']})" if c["error"] else "")
        )
        for claim in c["claims"]:
            lines.append(f"  - “{claim['quote']}” ({claim['reason']})")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None, config: Config | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "reports")
    parser.add_argument("--cases", default="", help="comma-separated case numbers (default: all)")
    args = parser.parse_args(argv)
    config = config or load_config()
    wanted = {int(x) for x in args.cases.split(",") if x.strip()}
    cases = [c for c in load_cases() if not wanted or c.number in wanted]
    calibration_items = load_calibration()
    skill_hashes = {s: _sha(s) for s in ("rec-email", "grade-claims")}  # the versions this run uses
    workers = config.claude.eval_concurrency
    with ThreadPoolExecutor(max_workers=workers) as pool:
        case_results = list(pool.map(lambda c: _run_case(c, config), cases))
        calibration = list(pool.map(lambda i: _run_calibration(i, config), calibration_items))
    summary = summarise([r["grade"] for r in case_results], [c["flagged"] for c in calibration])
    summary.update(
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        skill_sha256=skill_hashes,
        total_cost_usd=round(sum(r["cost"] for r in case_results) + sum(c["cost"] for c in calibration), 4),
        cases=[
            {
                **asdict(r["grade"]),
                "number": r["case"].number,
                "file": r["case"].path.name,
                "duration_s": r["duration"],
                "output": r["output"],
            }
            for r in case_results
        ],
        calibration=[
            {
                "number": c["item"].number,
                "case": c["item"].case.number,
                "planted": c["item"].planted,
                "flagged": c["flagged"],
                "claims": c["claims"],
                "error": c["error"],
            }
            for c in calibration
        ],
    )
    summary["gates_pass"] = gates_pass(summary)
    # The metric names config/criteria.json uses for C-2b-01 to C-2b-03 (same values).
    summary["email_schema_failures"] = summary["schema_failures"]
    summary["email_fact_failures"] = summary["fact_failures"]
    summary["email_form_failures"] = summary["form_failures"]
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "eval_email.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (args.out / "eval_email.md").write_text(_markdown(summary, case_results, calibration), encoding="utf-8")
    keys = (
        "schema_failures",
        "fact_failures",
        "form_failures",
        "unsupported_claims",
        "grader_calibration_passed",
    )
    print(" · ".join(f"{k}={summary[k]}" for k in keys), f"· gates_pass={summary['gates_pass']}")
    print(f"Reports: {args.out / 'eval_email.json'} and {args.out / 'eval_email.md'}")
    return 0 if summary["gates_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
