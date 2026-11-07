"""The ``cli`` AI backend: headless Claude Code (``claude -p``) on the owner's subscription (docs/04 §1.1).

The app never imports this module directly; it calls ``hub.ai.skills.run_skill``, which picks this backend
when ``claude.backend`` is ``"cli"`` and does the shared work (tool allow-list, schema validation, error
wording, usage log). Everything specific to the CLI lives here: the argv, the lockdown flags, finding the
executable, process handling and reading the CLI's JSON result. The input goes on stdin as JSON and the
answer comes back as ``structured_output``.

**Lockdown.** A headless call can't run commands, read or write files, use MCP servers or run hooks, and no
settings file applies (``--setting-sources ""``: not the project's ``.claude/settings.json``, not the user's).
It gets only the tools its skill needs (``SKILL_TOOLS``), approved by name with ``--allowedTools`` because
``dontAsk`` denies everything else, and at most ``claude.max_turns`` turns (``claude.research_max_turns``
for skills that browse; DECISIONS #61). Because no settings source is loaded, project skills aren't
either, so the skill's SKILL.md is passed with
``--append-system-prompt-file`` (the docs/04 §1.1 fallback). Chosen and proved by the Phase 2b live check
(tests/test_claude_live.py; DECISIONS #47).
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

from hub.ai.skills import (
    SKILL_TOOLS,
    WEB_SKILLS,
    BackendReply,
    SkillError,
    failed,
    invalid_output,
    not_installed,
    skill_file,
    timed_out,
)
from hub.config import REPO_ROOT, Config

PROMPT = "The input is the JSON document on stdin."
# Every tool a skill isn't given is denied by name, on top of ``--tools`` (belt and braces).
LOCKED_OUT_TOOLS: tuple[str, ...] = (
    "Agent",
    "Bash",
    "BashOutput",
    "Edit",
    "Glob",
    "Grep",
    "KillShell",
    "MultiEdit",
    "NotebookEdit",
    "PowerShell",
    "Read",
    "Skill",
    "Task",
    "WebFetch",
    "WebSearch",
    "Write",
)
NO_HOOKS = json.dumps({"disableAllHooks": True})

__all__ = ["LOCKED_OUT_TOOLS", "SKILL_TOOLS", "CliBackend", "SkillError", "build_argv", "resolve_executable"]


def resolve_executable(config: Config) -> list[str]:
    """The command that starts Claude Code. A ``.py`` path (the test fake) runs with this interpreter.

    Raises ``SkillError("not_installed")`` when nothing can be found.
    """
    configured = config.claude.path
    if configured:
        if configured.lower().endswith(".py"):
            if Path(configured).is_file():
                return [sys.executable, configured]
        elif Path(configured).is_file():
            return [configured]
        elif found := shutil.which(configured):
            return [found]
    elif found := shutil.which("claude"):
        return [found]
    raise not_installed("Claude Code was not found. Install it, or set claude.path in config/project.yaml.")


def build_argv(
    skill: str,
    schema: dict[str, Any],
    config: Config,
    *,
    executable: list[str],
    tools: tuple[str, ...] | None = None,
) -> list[str]:
    """The full argv (a list, never a shell string) for one locked-down call of ``skill``."""
    if skill not in SKILL_TOOLS:
        raise ValueError(f"unknown skill {skill!r}")
    allowed = SKILL_TOOLS[skill] if tools is None else tools
    denied = [t for t in LOCKED_OUT_TOOLS if t not in allowed]
    turns = config.claude.research_max_turns if skill in WEB_SKILLS else config.claude.max_turns
    # ``dontAsk`` denies any tool that isn't pre-approved, even one listed in ``--tools``; a skill's own
    # tools (and only those) are approved by name. A skill with no tools gets no rule (DECISIONS #61).
    approved = ["--allowedTools", ",".join(allowed)] if allowed else []
    return [
        *executable,
        "-p",
        PROMPT,
        "--append-system-prompt-file",
        str(skill_file(skill)),
        "--setting-sources",
        "",
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(schema, separators=(",", ":")),
        "--permission-mode",
        "dontAsk",
        "--permission-prompts",
        "none",
        "--tools",
        ",".join(allowed),
        "--disallowedTools",
        ",".join(denied),
        "--strict-mcp-config",
        "--settings",
        NO_HOOKS,
        "--no-session-persistence",
        "--max-turns",
        str(turns),
        *approved,
    ]


def _kill_tree(proc: subprocess.Popen[str]) -> None:
    """Kill the process and everything it started (``claude.cmd`` → node, or the fake and its child)."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False)
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(OSError):
        proc.kill()


def _first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def _parse(stdout: str) -> BackendReply:
    """The CLI's JSON result as a backend reply (schema validation is shared, in ``hub.ai.skills``)."""
    try:
        raw = json.loads(stdout)
    except json.JSONDecodeError:
        raise invalid_output("not JSON") from None
    if not isinstance(raw, dict):
        raise invalid_output("not a JSON object")
    if raw.get("is_error"):
        raise invalid_output(f"it reported an error: {raw.get('subtype') or 'error'}")
    cost = raw.get("total_cost_usd")
    return BackendReply(
        data=raw.get("structured_output"),
        cost_usd=float(cost) if isinstance(cost, int | float) else None,
        session_id=str(raw.get("session_id", "")),
        raw=raw,
    )


class CliBackend:
    """Runs a skill with ``claude -p``, locked down (see the module docstring)."""

    def call(
        self,
        skill: str,
        payload: dict[str, Any],
        schema: dict[str, Any],
        tools: tuple[str, ...],
        timeout_s: int,
        config: Config,
    ) -> BackendReply:
        argv = build_argv(skill, schema, config, executable=resolve_executable(config), tools=tools)
        kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        try:
            proc = subprocess.Popen(
                argv,
                cwd=REPO_ROOT,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                **kwargs,
            )
        except OSError:
            raise not_installed(
                "Claude Code could not be started. Check claude.path in config/project.yaml."
            ) from None
        try:
            stdout, stderr = proc.communicate(json.dumps(payload), timeout=timeout_s)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            proc.communicate()
            raise timed_out(timeout_s) from None
        if proc.returncode != 0:
            reason = _first_line(stderr)
            if not reason:
                try:
                    reason = str(json.loads(stdout).get("subtype") or "")
                except (json.JSONDecodeError, AttributeError):
                    reason = ""
            raise failed(reason or f"exit code {proc.returncode}")
        return _parse(stdout)
