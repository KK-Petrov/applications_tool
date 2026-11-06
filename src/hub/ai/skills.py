"""The one entry point for AI features: ``run_skill`` (docs/04 §1; DECISIONS #50).

The app imports only ``run_skill``, ``SkillResult``, ``SkillError`` (and ``load_schema``) from here. A skill
is ``.claude/skills/<name>/SKILL.md``; its input is a JSON payload built in Python and its answer must match a
JSON schema. ``run_skill`` hands the call to the backend named by ``claude.backend`` (only ``"cli"``,
headless Claude Code, exists: ``hub.ai.claude_cli``) and then does what every backend shares:

- **Tools**: a skill may use only the tools in ``SKILL_TOOLS`` (rec-email and grade-claims none).
- **Validation**: the reply is checked against the schema here, whatever the backend checked itself.
- **Errors**: every failure is a ``SkillError`` of kind not_installed, timeout, failed or invalid_output,
  with a message for the user that ends "Nothing was saved."; callers write to data_dir only after a
  successful return (C-2b-05).
- **Usage log**: one line per call to ``claude.usage_log`` (skill, duration, cost, ok/error), never the
  payload or the output.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any, Protocol

import jsonschema

from hub.config import REPO_ROOT, Config

SCHEMAS_DIR = Path(__file__).resolve().parent / "schemas"
SKILLS_DIR = REPO_ROOT / ".claude" / "skills"
SAVED_NOTHING = "Nothing was saved."

# The only tools each skill may use, whatever the backend (the cli backend denies everything else).
SKILL_TOOLS: dict[str, tuple[str, ...]] = {
    "rec-email": (),
    "grade-claims": (),
    "probe-skill": (),
    "probe-lockdown": (),
    "research-programme": ("WebSearch", "WebFetch"),  # Phase 2c
}
_LOG_LOCK = threading.Lock()


@dataclass(frozen=True)
class SkillResult:
    """A successful call: the schema-valid output plus what the backend reported about the run."""

    data: dict[str, Any]
    cost_usd: float | None
    duration_s: float
    session_id: str
    raw: dict[str, Any] = field(default_factory=dict, repr=False)  # the backend's own result (no payload)


class SkillError(Exception):
    """A failed call. ``kind``: not_installed | timeout | failed | invalid_output.

    ``message`` is shown to the user as is.
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


# The four failures, worded once for every backend (docs/03 §5).
def not_installed(detail: str) -> SkillError:
    return SkillError("not_installed", f"{detail} {SAVED_NOTHING}")


def timed_out(seconds: int) -> SkillError:
    return SkillError("timeout", f"Claude Code did not respond within {seconds} s. {SAVED_NOTHING}")


def failed(reason: str) -> SkillError:
    return SkillError("failed", f"Claude Code stopped with an error: {reason.rstrip('.')}. {SAVED_NOTHING}")


def invalid_output(detail: str) -> SkillError:
    return SkillError(
        "invalid_output", f"Claude Code returned output the app could not use ({detail}). {SAVED_NOTHING}"
    )


@dataclass(frozen=True)
class BackendReply:
    """What a backend returns: the structured output (not yet validated) and run details."""

    data: Any
    cost_usd: float | None
    session_id: str
    raw: dict[str, Any]


class Backend(Protocol):
    """A way to run a skill. It raises ``SkillError`` (built with the helpers above) for its own failures."""

    def call(
        self,
        skill: str,
        payload: dict[str, Any],
        schema: dict[str, Any],
        tools: tuple[str, ...],
        timeout_s: int,
        config: Config,
    ) -> BackendReply: ...


@cache
def load_schema(name: str) -> dict[str, Any]:
    """An output schema from ``src/hub/ai/schemas/<name>.json``."""
    return json.loads((SCHEMAS_DIR / f"{name}.json").read_text(encoding="utf-8"))


def skill_file(skill: str) -> Path:
    """``.claude/skills/<skill>/SKILL.md``: the instructions given to the model for one call."""
    return SKILLS_DIR / skill / "SKILL.md"


def backend(config: Config) -> Backend:
    """The backend named by ``claude.backend``."""
    if config.claude.backend == "cli":
        from hub.ai.claude_cli import CliBackend

        return CliBackend()
    raise ValueError(f"unknown AI backend {config.claude.backend!r}")


def _validate(data: Any, schema: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise invalid_output("no structured output")
    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as exc:
        where = "/".join(str(p) for p in exc.absolute_path) or "the top level"
        raise invalid_output(f"it does not match the expected form at {where}") from None
    return data


def _usage_log(config: Config, entry: dict[str, Any]) -> None:
    """One line per call; never the payload or the output. A log that can't be written is skipped."""
    path = Path(config.claude.usage_log)
    if not path.is_absolute():
        path = REPO_ROOT / path
    line = json.dumps({"at": datetime.now(UTC).isoformat(timespec="seconds"), **entry})
    try:
        with _LOG_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except OSError:
        pass


def run_skill(
    skill: str,
    payload: dict[str, Any],
    schema: dict[str, Any],
    *,
    config: Config,
    tools: list[str] | None = None,
    timeout_s: int | None = None,
) -> SkillResult:
    """Run ``skill`` on ``payload``; return the schema-valid result or raise ``SkillError``.

    ``tools`` may only narrow the skill's own allow-list (``SKILL_TOOLS``); anything else is a ``ValueError``.
    """
    if skill not in SKILL_TOOLS:
        raise ValueError(f"unknown skill {skill!r}")
    if tools is not None and not set(tools) <= set(SKILL_TOOLS[skill]):
        raise ValueError(f"{skill} may not use {sorted(set(tools) - set(SKILL_TOOLS[skill]))}")
    if not skill_file(skill).is_file():
        raise ValueError(f"{skill_file(skill)} does not exist")
    allowed = tuple(tools) if tools is not None else SKILL_TOOLS[skill]
    timeout = timeout_s or config.claude.timeout_s
    start = time.monotonic()
    entry: dict[str, Any] = {"skill": skill, "backend": config.claude.backend}
    try:
        reply = backend(config).call(skill, payload, schema, allowed, timeout, config)
        data = _validate(reply.data, schema)
    except SkillError as err:
        entry.update(
            duration_s=round(time.monotonic() - start, 2), total_cost_usd=None, ok=False, error=err.kind
        )
        _usage_log(config, entry)
        raise
    duration = time.monotonic() - start
    entry.update(duration_s=round(duration, 2), total_cost_usd=reply.cost_usd, ok=True, error=None)
    _usage_log(config, entry)
    return SkillResult(
        data=data,
        cost_usd=reply.cost_usd,
        duration_s=max(duration, 1e-6),
        session_id=reply.session_id,
        raw=reply.raw,
    )
