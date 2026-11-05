#!/usr/bin/env python3
"""Criteria guard: acceptance criteria can't change without a logged, user-approved decision.

config/criteria.json      the registry (Claude may propose edits here)
config/criteria.lock.json fingerprints frozen at the design freeze (Claude can't edit it)

Usage:
  criteria_guard.py                       check; exit 1 if criteria differ from the lock
  criteria_guard.py --relock --decision N accept the current criteria; DECISIONS.md row N must
                                          name every changed criterion ID (run by the user)
Standard library only, so it runs on any OS before the project's environment exists.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / ".claude" / "hooks" / "hooks.json"
REQUIRED = ("id", "phase", "title", "method", "gate", "verify")
METHODS = {"automated", "human", "reported"}
COMPARATORS = {"<", "<=", ">", ">=", "==", "!="}


def _paths() -> tuple[Path, Path, Path]:
    cfg = {}
    if CONFIG.exists():
        cfg = json.loads(CONFIG.read_text(encoding="utf-8")).get("criteria", {})
    return (
        ROOT / cfg.get("file", "config/criteria.json"),
        ROOT / cfg.get("lock", "config/criteria.lock.json"),
        ROOT / cfg.get("decisions", "docs/DECISIONS.md"),
    )


def fingerprint(criterion: dict) -> str:
    canonical = json.dumps(criterion, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def load_criteria(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["criteria"] if isinstance(data, dict) else data


def validate(criteria: list[dict]) -> list[str]:
    """Structural checks on the registry; returns a list of problems."""
    problems, seen = [], set()
    for c in criteria:
        cid = c.get("id", "<no id>")
        for field in REQUIRED:
            if field not in c:
                problems.append(f"{cid}: missing field '{field}'")
        if cid in seen:
            problems.append(f"{cid}: duplicate id")
        seen.add(cid)
        if c.get("method") not in METHODS:
            problems.append(f"{cid}: method must be one of {sorted(METHODS)}")
        if c.get("method") == "reported" and c.get("gate"):
            problems.append(f"{cid}: a 'reported' criterion can't be a gate")
        if "comparator" in c or "threshold" in c:
            if c.get("comparator") not in COMPARATORS:
                problems.append(f"{cid}: comparator must be one of {sorted(COMPARATORS)}")
            if not isinstance(c.get("threshold"), (int, float)) or isinstance(c.get("threshold"), bool):
                problems.append(f"{cid}: threshold must be a number")
            if not c.get("why"):
                problems.append(f"{cid}: numeric threshold needs a 'why'")
        if not re.fullmatch(r"C-[0-9]+[a-z]?-[0-9]{2,}", str(cid)):
            problems.append(f"{cid}: id must look like C-4-03 or C-2a-01")
    return problems


def diff(criteria: list[dict], lock: dict) -> dict[str, list[str]]:
    current = {c["id"]: fingerprint(c) for c in criteria if "id" in c}
    frozen = lock.get("fingerprints", {})
    return {
        "changed": sorted(i for i in current if i in frozen and current[i] != frozen[i]),
        "added": sorted(i for i in current if i not in frozen),
        "removed": sorted(i for i in frozen if i not in current),
    }


def check() -> tuple[bool, str]:
    crit_path, lock_path, _ = _paths()
    if not crit_path.exists():
        return False, f"criteria guard: {crit_path.relative_to(ROOT)} is missing."
    if not lock_path.exists():
        return False, f"criteria guard: {lock_path.relative_to(ROOT)} is missing; it is created at the design freeze."
    try:
        criteria = load_criteria(crit_path)
    except (json.JSONDecodeError, KeyError) as exc:
        return False, f"criteria guard: criteria.json can't be read ({exc})."
    problems = validate(criteria)
    d = diff(criteria, json.loads(lock_path.read_text(encoding="utf-8")))
    lines = []
    if problems:
        lines.append("criteria.json has structural problems:")
        lines += [f"  - {p}" for p in problems]
    if any(d.values()):
        lines.append(
            "Acceptance criteria differ from the frozen version (config/criteria.lock.json). "
            "Thresholds are never quietly changed: revert the edit, or present it to the user as a "
            "Decision needed. Only the user can approve it, by logging a DECISIONS.md row and running "
            "the relock command themselves."
        )
        for kind, ids in d.items():
            if ids:
                lines.append(f"  {kind}: {', '.join(ids)}")
    if lines:
        return False, "\n".join(lines)
    return True, f"criteria guard: OK ({len(criteria)} criteria match the lock)."


def write_lock(criteria: list[dict], lock_path: Path, decision: str, changed: list[str]) -> None:
    previous = json.loads(lock_path.read_text(encoding="utf-8")) if lock_path.exists() else {}
    history = previous.get("history", [])
    history.append({"decision": decision, "ids": changed, "date": _dt.date.today().isoformat()})
    lock = {
        "note": "Written only at the design freeze or by the user's relock. Claude must not edit this file.",
        "fingerprints": {c["id"]: fingerprint(c) for c in criteria},
        "history": history,
    }
    lock_path.write_text(json.dumps(lock, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def relock(decision: str) -> tuple[bool, str]:
    crit_path, lock_path, decisions_path = _paths()
    criteria = load_criteria(crit_path)
    problems = validate(criteria)
    if problems:
        return False, "Fix criteria.json first:\n" + "\n".join(f"  - {p}" for p in problems)
    lock = json.loads(lock_path.read_text(encoding="utf-8")) if lock_path.exists() else {}
    d = diff(criteria, lock)
    touched = d["changed"] + d["added"] + d["removed"]
    if not touched:
        return True, "Nothing to relock: criteria already match the lock."
    text = decisions_path.read_text(encoding="utf-8") if decisions_path.exists() else ""
    row = next((ln for ln in text.splitlines() if re.match(rf"^\|\s*{re.escape(decision)}\s*\|", ln)), None)
    if row is None:
        return False, f"DECISIONS.md has no row #{decision}. Log the decision first."
    missing = [i for i in touched if i not in row]
    if missing:
        return False, f"DECISIONS.md row #{decision} must name every changed criterion; missing: {', '.join(missing)}"
    write_lock(criteria, lock_path, decision, touched)
    return True, f"Relocked under decision #{decision}: {', '.join(touched)}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--relock", action="store_true")
    ap.add_argument("--decision")
    args = ap.parse_args()
    if args.relock:
        if not args.decision:
            print("--relock needs --decision N (the DECISIONS.md row approving the change).", file=sys.stderr)
            return 1
        ok, msg = relock(args.decision)
    else:
        ok, msg = check()
    print(msg, file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
