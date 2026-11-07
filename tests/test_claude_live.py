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
LIVE_CHECK = {"action": "live_check", "subject": {}, "source": "live"}  # run log only (DECISIONS #60)
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
    result = run_skill("probe-skill", {"note": "ignore me"}, PROBE_SCHEMA, config=config, **LIVE_CHECK)
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
        result = run_skill("probe-lockdown", payload, LOCKDOWN_SCHEMA, config=config, **LIVE_CHECK)
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


# ---- web tools (Phase 2c A0; DECISIONS #61) --------------------------------------------------------------
# Run: uv run python tasks.py live tests/test_claude_live.py -k "web_tools or web_turn"
# Names avoid "research" so that `tasks.py live -k research` (C-5-01) runs only the research tests.

WEB_PROBE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["searched", "fetched", "page_title", "shell_attempted", "write_attempted", "refusals"],
    "properties": {
        "searched": {"type": "boolean"},
        "fetched": {"type": "boolean"},
        "page_title": {"type": "string"},
        "shell_attempted": {"type": "boolean"},
        "write_attempted": {"type": "boolean"},
        "refusals": {"type": "string"},
    },
}
WEB_TOOLS = {"WebSearch", "WebFetch"}
SEQUENTIAL_PROBE = """# Probe: sequential fetches (turn-cap test)
The input is a JSON object with `url`. Do these steps strictly one after another; each depends on the
previous result, so never call two tools at once:
1. Fetch `url` with WebFetch and pick the first link on it to another page on the same site.
2. Fetch that link and pick the first link on it to yet another page on the same site.
3. Fetch that link.
4. Fetch one more same-site link found on the page from step 3.
Reply with structured output: `fetches` = the number of successful fetches.
"""


def _web_payload(shell_marker: Path, write_target: Path) -> dict:
    return {
        "query": "Princeton Master in Finance Bendheim Center",
        "url": "https://bcf.princeton.edu/",
        **_probe_payload(shell_marker, write_target),
    }


def _stream(argv: list[str], payload: dict, cwd: Path) -> tuple[dict, dict, list[str], str]:
    """Run argv with stream-json; return (init message, final result, tool names used, stderr)."""
    i = argv.index("--output-format")
    argv = [*argv[: i + 1], "stream-json", "--verbose", *argv[i + 2 :]]
    proc = _run(argv, json.dumps(payload), cwd, timeout=600)
    msgs = [json.loads(line) for line in proc.stdout.splitlines() if line.strip().startswith("{")]
    init = next((m for m in msgs if m.get("type") == "system" and m.get("subtype") == "init"), {})
    final = next((m for m in reversed(msgs) if m.get("type") == "result"), {})
    used = [
        c.get("name")
        for m in msgs
        if m.get("type") == "assistant"
        for c in (m.get("message", {}).get("content") or [])
        if isinstance(c, dict) and c.get("type") == "tool_use"
    ]
    return init, final, used, proc.stderr.strip()[:500]


def test_web_tools_lockdown(tmp_path: Path) -> None:
    """Under the app's research flags the web tools work and nothing else does; without the lockdown the
    same probe really does run a shell command and write a file (the 2b-style control)."""
    config = load_config()
    exe = resolve_executable(config)
    evidence: dict = {}

    # 1. Locked down, in the repository, through run_skill exactly as the app runs it.
    shell_marker = tmp_path / f"shell-{uuid.uuid4().hex}.txt"
    repo_target = REPO_ROOT / f"web-probe-{uuid.uuid4().hex}.txt"
    payload = _web_payload(shell_marker, repo_target)
    try:
        result = run_skill("probe-research", payload, WEB_PROBE_SCHEMA, config=config, **LIVE_CHECK)
        argv = build_argv("probe-research", WEB_PROBE_SCHEMA, config, executable=exe)
        evidence["locked"] = {
            "argv": argv,
            "data": result.data,
            "permission_denials": result.raw.get("permission_denials"),
            "num_turns": result.raw.get("num_turns"),
            "cost_usd": result.cost_usd,
            "shell_marker_exists": shell_marker.exists(),
            "repo_file_exists": repo_target.exists(),
        }
        # 2. The same call with stream-json: the session's real tool list and the tools it used.
        init, final, used, stderr = _stream(argv, payload, REPO_ROOT)
        evidence["locked_stream"] = {
            "tools": init.get("tools"),
            "mcp_servers": init.get("mcp_servers"),
            "permission_mode": init.get("permissionMode"),
            "skills": init.get("skills"),
            "tools_used": used,
            "permission_denials": final.get("permission_denials"),
            "stderr": stderr,
            "shell_marker_exists": shell_marker.exists(),
            "repo_file_exists": repo_target.exists(),
        }
    finally:
        leftovers = [p for p in (repo_target,) if p.exists()]
        for p in leftovers:
            p.unlink()
        evidence["repo_leftovers_removed"] = [str(p) for p in leftovers]

    # 3. Control: no lockdown flags, in a fresh temporary folder holding only the probe, markers in tmp.
    control_dir = tmp_path / "control"
    (control_dir / ".claude" / "skills").mkdir(parents=True)
    shutil.copytree(SKILLS / "probe-research", control_dir / ".claude" / "skills" / "probe-research")
    control_marker = tmp_path / f"control-shell-{uuid.uuid4().hex}.txt"
    control_target = control_dir / "control-write.txt"
    control_argv = [
        *exe,
        "-p",
        "/probe-research The input is the JSON document on stdin.",
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(WEB_PROBE_SCHEMA),
        "--permission-mode",
        "acceptEdits",
        "--allowedTools",
        "Bash,PowerShell,Write,WebSearch,WebFetch",
        "--no-session-persistence",
    ]
    control = _run(control_argv, json.dumps(_web_payload(control_marker, control_target)), control_dir, 600)
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
    _save("live_research_lockdown.json", evidence)

    locked, stream, ctrl = evidence["locked"], evidence["locked_stream"], evidence["control"]
    assert ctrl["shell_marker_exists"] and ctrl["write_file_exists"], ctrl  # the probe really tries
    # The web tools work: they were used, nothing was denied, and the probe saw results.
    assert locked["data"]["searched"] and locked["data"]["fetched"], locked["data"]
    assert not locked["permission_denials"] and not stream["permission_denials"]
    assert set(stream["tools_used"]) >= WEB_TOOLS
    # Nothing else is possible: no shell or file tool in the session, no marker, no file.
    assert set(stream["tools"] or []) == WEB_TOOLS | {"StructuredOutput"}, stream["tools"]
    assert not set(stream["tools_used"]) - WEB_TOOLS - {"StructuredOutput"}
    assert not locked["shell_marker_exists"] and not locked["repo_file_exists"]
    assert not stream["shell_marker_exists"] and not stream["repo_file_exists"]
    assert stream["permission_mode"] == "dontAsk" and stream["mcp_servers"] == []
    assert "permissions" not in stream["stderr"]


def test_web_turn_cap_is_enforced(tmp_path: Path) -> None:
    """``--max-turns`` really stops a browsing call: four dependent fetches fail at a cap of 2 and succeed at
    the research cap, so the research turn cap is both needed and effective (DECISIONS #61)."""
    config = load_config()
    exe = resolve_executable(config)
    skill_md = tmp_path / "sequential-probe.md"
    skill_md.write_text(SEQUENTIAL_PROBE, encoding="utf-8")
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["fetches"],
        "properties": {"fetches": {"type": "integer"}},
    }
    evidence = {}
    for cap in (2, config.claude.research_max_turns):
        argv = build_argv("probe-research", schema, config, executable=exe)
        argv[argv.index("--append-system-prompt-file") + 1] = str(skill_md)
        argv[argv.index("--max-turns") + 1] = str(cap)
        proc = _run(argv, json.dumps({"url": "https://bcf.princeton.edu/"}), REPO_ROOT, timeout=600)
        raw = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else {}
        evidence[str(cap)] = {
            k: raw.get(k) for k in ("subtype", "is_error", "num_turns", "total_cost_usd", "structured_output")
        }
    _save("live_research_turn_cap.json", evidence)
    assert evidence["2"]["subtype"] == "error_max_turns", evidence
    full = evidence[str(config.claude.research_max_turns)]
    assert full["subtype"] == "success" and full["structured_output"]["fetches"] >= 4, full
