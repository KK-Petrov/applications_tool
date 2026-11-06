"""Live checks of the Claude Code wrapper (docs/04 §1.1; Phase 2b tasks 1–2). Marker ``live``: real calls on
the owner's subscription, never part of test-fast or test. Run:

    uv run python tasks.py live -k "skill_expansion or lockdown"

- ``test_skill_expansion``: the skill's instructions reach the model (the probe returns a token found only in
  its SKILL.md) and ``structured_output`` comes back. The app passes SKILL.md with
  ``--append-system-prompt-file`` because the lockdown loads no settings source, and so no project skills
  (DECISIONS #47; naming ``/skill`` in the prompt works only with project settings loaded).
- ``test_lockdown``: with the app's flags, a headless call cannot run a command or write a file: the markers
  never appear, the CLI records the refusal (``permission_denials``) or shows the tools were never there
  (the session's tool list from the ``stream-json`` init message), and the skill still returns its output.
  A control run of the same probe **without** the lockdown flags, in a fresh temporary folder (never the
  repository or data_dir), does create its marker, so the probe really tries.
Evidence goes to ``reports/live_lockdown.json`` for the DECISIONS row.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from hub.ai.claude_cli import LOCKED_OUT_TOOLS, build_argv, resolve_executable
from hub.ai.skills import run_skill
from hub.config import load_config

from paths import REPO_ROOT

pytestmark = pytest.mark.live

SKILLS = REPO_ROOT / ".claude" / "skills"
REPORTS = REPO_ROOT / "reports"
PROBE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["token"],
    "properties": {"token": {"type": "string"}},
}
LOCKDOWN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["shell_attempted", "write_attempted", "refusals"],
    "properties": {
        "shell_attempted": {"type": "boolean"},
        "write_attempted": {"type": "boolean"},
        "refusals": {"type": "string"},
    },
}
SHELL_TOOLS = {"Bash", "PowerShell"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def _probe_payload(shell_marker: Path, write_target: Path) -> dict:
    return {
        "command": f"echo lockdown-probe > '{shell_marker.as_posix()}'",
        "file_path": str(write_target),
        "file_text": "lockdown probe\n",
    }


def _run(argv: list[str], stdin: str, cwd: Path, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        input=stdin,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        check=False,
    )


def _save(name: str, evidence: dict) -> None:
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / name).write_text(json.dumps(evidence, indent=2, default=str), encoding="utf-8")


def test_skill_expansion() -> None:
    token = re.search(
        r"probe-[0-9a-f]{16}", (SKILLS / "probe-skill" / "SKILL.md").read_text(encoding="utf-8")
    )
    assert token, "probe-skill/SKILL.md has no token"
    config = load_config()
    result = run_skill("probe-skill", {"note": "ignore me"}, PROBE_SCHEMA, config=config)
    _save(
        "live_probe.json",
        {
            "data": result.data,
            "duration_s": result.duration_s,
            "cost_usd": result.cost_usd,
            "raw": result.raw,
        },
    )
    assert result.data == {"token": token.group(0)}


def test_lockdown(tmp_path: Path) -> None:
    config = load_config()
    exe = resolve_executable(config)
    evidence: dict = {}

    # 1. Locked down, in the repository (exactly as the app runs it).
    shell_marker = tmp_path / f"shell-{uuid.uuid4().hex}.txt"
    repo_target = REPO_ROOT / f"lockdown-probe-{uuid.uuid4().hex}.txt"
    payload = _probe_payload(shell_marker, repo_target)
    try:
        result = run_skill("probe-lockdown", payload, LOCKDOWN_SCHEMA, config=config)
        evidence["locked"] = {
            "argv": build_argv("probe-lockdown", LOCKDOWN_SCHEMA, config, executable=exe),
            "data": result.data,
            "permission_denials": result.raw.get("permission_denials"),
            "num_turns": result.raw.get("num_turns"),
            "shell_marker_exists": shell_marker.exists(),
            "repo_file_exists": repo_target.exists(),
        }
        # 2. The same call with stream-json: the init message lists the tools the session really had.
        stream_argv = build_argv("probe-lockdown", LOCKDOWN_SCHEMA, config, executable=exe)
        i = stream_argv.index("--output-format")
        stream_argv[i + 1 : i + 2] = ["stream-json", "--verbose"]
        streamed = _run(stream_argv, json.dumps(payload), REPO_ROOT)
        init = next(
            (
                m
                for m in (
                    json.loads(line) for line in streamed.stdout.splitlines() if line.strip().startswith("{")
                )
                if m.get("type") == "system" and m.get("subtype") == "init"
            ),
            {},
        )
        evidence["locked_stream"] = {
            "tools": init.get("tools"),
            "mcp_servers": init.get("mcp_servers"),
            "permission_mode": init.get("permissionMode"),
            "skills": init.get("skills"),
            "memory_paths": init.get("memory_paths"),
            "stderr": streamed.stderr.strip()[:500],
            "shell_marker_exists": shell_marker.exists(),
            "repo_file_exists": repo_target.exists(),
        }
    finally:
        leftovers = [p for p in (repo_target,) if p.exists()]
        for p in leftovers:
            p.unlink()
        evidence["repo_leftovers_removed"] = [str(p) for p in leftovers]

    # 3. Control: no lockdown flags, in a fresh temporary copy of the probe only, marker in tmp.
    control_dir = tmp_path / "control"
    (control_dir / ".claude" / "skills").mkdir(parents=True)
    shutil.copytree(SKILLS / "probe-lockdown", control_dir / ".claude" / "skills" / "probe-lockdown")
    control_marker = tmp_path / f"control-shell-{uuid.uuid4().hex}.txt"
    control_target = control_dir / "control-write.txt"
    control_argv = [
        *exe,
        "-p",
        "/probe-lockdown The input is the JSON document on stdin.",
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(LOCKDOWN_SCHEMA),
        "--permission-mode",
        "acceptEdits",
        "--allowedTools",
        "Bash,PowerShell,Write",
        "--no-session-persistence",
    ]
    control = _run(control_argv, json.dumps(_probe_payload(control_marker, control_target)), control_dir)
    control_raw = json.loads(control.stdout) if control.stdout.strip().startswith("{") else {}
    evidence["control"] = {
        "argv": control_argv,
        "cwd": str(control_dir),
        "exit": control.returncode,
        "data": control_raw.get("structured_output"),
        "permission_denials": control_raw.get("permission_denials"),
        "shell_marker_exists": control_marker.exists(),
        "write_file_exists": control_target.exists(),
    }
    _save("live_lockdown.json", evidence)

    locked, stream, ctrl = evidence["locked"], evidence["locked_stream"], evidence["control"]
    # The control proves the probe really tries both actions when nothing stops it.
    assert ctrl["shell_marker_exists"] and ctrl["write_file_exists"], ctrl
    # Locked down: nothing happened, and the skill still answered.
    assert not locked["shell_marker_exists"] and not locked["repo_file_exists"]
    assert not stream["shell_marker_exists"] and not stream["repo_file_exists"]
    assert set(locked["data"]) == {"shell_attempted", "write_attempted", "refusals"}
    # The refusal is real: either the CLI recorded denied attempts for both kinds of tool, or the session
    # never had those tools (its init tool list holds none of the locked-out ones).
    denied = {d.get("tool_name") for d in (locked["permission_denials"] or [])}
    tools = set(stream["tools"] or [])
    assert stream["tools"] is not None, "no init message in stream-json output"
    assert not tools & set(LOCKED_OUT_TOOLS), f"locked-out tools present: {tools & set(LOCKED_OUT_TOOLS)}"
    if denied:
        assert denied & SHELL_TOOLS and denied & WRITE_TOOLS, denied
    # No settings file applied: the project's skills (and so its .claude/settings.json) were not loaded,
    # and the CLI printed nothing about project permission rules.
    assert stream["permission_mode"] == "dontAsk"
    assert not {s for s in (stream["skills"] or []) if s.startswith(("probe-", "rec-email", "grade-claims"))}
    assert "permissions" not in stream["stderr"]
