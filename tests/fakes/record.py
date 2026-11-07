"""Re-record the fake Claude Code's replies from real calls (live; invented eval data only).

    uv run python tests/fakes/record.py

Runs ``/rec-email`` on eval case 2 (a sparse record, so the reply carries warnings) and ``/grade-claims`` on
calibration email 1, and writes the CLI's JSON results to ``tests/fakes/recordings/<skill>.json``. Run it
after changing a skill so the fast tests replay a reply of the current skill.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "evals" / "email"))

from evalset import build_payload, load_calibration, load_cases  # noqa: E402

from hub.ai.skills import load_schema, run_skill  # noqa: E402
from hub.config import load_config  # noqa: E402

# Only what the wrapper reads is kept; account details (e.g. credential expiry) never enter the repository.
KEEP = ("type", "subtype", "is_error", "num_turns", "duration_ms", "result", "total_cost_usd",
        "permission_denials", "structured_output")  # fmt: skip


def _clean(raw: dict) -> dict:
    out = {k: raw[k] for k in KEEP if k in raw}
    out["session_id"] = "00000000-0000-4000-8000-000000000000"
    return out


def main() -> int:
    config = load_config()
    out = HERE / "recordings"
    out.mkdir(exist_ok=True)
    case = next(c for c in load_cases() if c.number == 2)
    email = run_skill(
        "rec-email",
        build_payload(case, config).payload,
        load_schema("rec_email"),
        config=config,
        action="generate_email",
        subject={"professor_id": case.professor.id, "programme_ids": list(case.programme_ids)},
        source="live",
    )
    item = load_calibration()[0]
    facts = build_payload(item.case, config).payload
    claims = run_skill(
        "grade-claims",
        {"text": item.body, "facts": facts},
        load_schema("grade_claims"),
        config=config,
        action="grade_claims",
        subject={"professor_id": item.case.professor.id, "programme_ids": list(item.case.programme_ids)},
        source="live",
    )
    for skill, result in (("rec-email", email), ("grade-claims", claims)):
        (out / f"{skill}.json").write_text(
            json.dumps(_clean(result.raw), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"recorded {skill}: {out / f'{skill}.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
